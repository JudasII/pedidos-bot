"""The Telegram adapter, against a fake Bot API. Nothing here touches the network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from pedidos_bot.channels.base import (
    ChannelError,
    GeoPoint,
    InboundMessage,
    MessageKind,
    OutboundMessage,
    QuickReply,
)
from pedidos_bot.channels.telegram import (
    MAX_TURN_ATTEMPTS,
    BadTokenError,
    TelegramChannel,
    to_inbound,
)
from tests.telegram_fake import SENT_AT, TOKEN, FakeTelegram, make_update, refusal, until


@asynccontextmanager
async def _channel(fake: FakeTelegram) -> AsyncIterator[TelegramChannel]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
        yield TelegramChannel(TOKEN, client, retry_wait=0.01)


async def _poll(
    fake: FakeTelegram,
    on_inbound: Callable[[InboundMessage], Awaitable[None]],
    done: Callable[[], bool],
) -> None:
    """Run the channel until `done` holds, then stop it and wait for it to end."""
    async with _channel(fake) as channel:
        polling = asyncio.create_task(channel.start(on_inbound))
        try:
            await until(lambda: done() or polling.done())
        finally:
            channel.stop()
            await polling


# --- mapping ------------------------------------------------------------------------------


def test_a_text_message_becomes_an_inbound_message() -> None:
    inbound = to_inbound(make_update(41, chat_id=7, text="una hamburguesa"))
    assert inbound is not None
    assert inbound.channel == "telegram"
    assert inbound.external_id == "41"
    assert inbound.chat_ref == "7" and inbound.customer_ref == "7"
    assert inbound.sent_at == datetime.fromtimestamp(SENT_AT, UTC)
    assert inbound.kind is MessageKind.TEXT
    assert inbound.content == "una hamburguesa"


@pytest.mark.parametrize(
    ("content", "kind", "media_ref"),
    [
        ({"voice": {"file_id": "v1"}}, MessageKind.VOICE, "v1"),
        ({"audio": {"file_id": "a1"}}, MessageKind.VOICE, "a1"),
        ({"photo": [{"file_id": "small"}, {"file_id": "large"}]}, MessageKind.IMAGE, "large"),
        ({"document": {"file_id": "d1"}}, MessageKind.DOCUMENT, "d1"),
        ({"sticker": {"file_id": "s1"}}, MessageKind.STICKER, "s1"),
        ({"contact": {"phone_number": "3000000000"}}, MessageKind.UNSUPPORTED, None),
    ],
)
def test_other_kinds_of_message_are_mapped(
    content: dict[str, Any], kind: MessageKind, media_ref: str | None
) -> None:
    inbound = to_inbound(make_update(1, **content))
    assert inbound is not None
    assert (inbound.kind, inbound.media_ref, inbound.content) == (kind, media_ref, "")


def test_a_location_keeps_its_coordinates() -> None:
    inbound = to_inbound(make_update(1, location={"latitude": 4.6, "longitude": -74.1}))
    assert inbound is not None
    assert inbound.kind is MessageKind.LOCATION
    assert inbound.location == GeoPoint(latitude=4.6, longitude=-74.1)


def test_a_caption_is_the_text_of_a_photo() -> None:
    inbound = to_inbound(make_update(1, photo=[{"file_id": "p"}], caption="así la quiero"))
    assert inbound is not None
    assert (inbound.kind, inbound.text) == (MessageKind.IMAGE, "así la quiero")


@pytest.mark.parametrize(
    "update",
    [
        make_update(1, chat_type="group"),
        make_update(1, chat_type="supergroup"),
        {"update_id": 1, "edited_message": {"text": "hola"}},
    ],
)
def test_updates_that_are_not_customer_messages_are_ignored(update: Any) -> None:
    assert to_inbound(update) is None


# --- polling ------------------------------------------------------------------------------


async def test_an_update_is_handled_and_then_confirmed() -> None:
    fake = FakeTelegram(make_update(41))
    handled: list[InboundMessage] = []

    async def on_inbound(inbound: InboundMessage) -> None:
        handled.append(inbound)

    await _poll(fake, on_inbound, lambda: not fake.updates)
    assert [inbound.external_id for inbound in handled] == ["41"]
    assert fake.offsets[0] is None  # Telegram remembers where we were
    assert fake.offsets[-1] == 42


async def test_an_update_is_not_confirmed_while_its_turn_is_running() -> None:
    fake = FakeTelegram(make_update(41))
    release = asyncio.Event()
    calls = 0

    async def on_inbound(inbound: InboundMessage) -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    async def release_after_some_polls() -> bool:
        await until(lambda: len(fake.offsets) >= 3)
        confirmed_early = any(offset is not None and offset > 41 for offset in fake.offsets)
        release.set()
        return confirmed_early

    watcher = asyncio.create_task(release_after_some_polls())
    await _poll(fake, on_inbound, lambda: not fake.updates)
    assert await watcher is False
    assert calls == 1  # Telegram sent it again on every poll; it ran once


async def test_an_ignored_update_is_confirmed() -> None:
    fake = FakeTelegram(make_update(41, chat_type="group"))
    handled: list[InboundMessage] = []

    async def on_inbound(inbound: InboundMessage) -> None:
        handled.append(inbound)

    await _poll(fake, on_inbound, lambda: not fake.updates)
    assert handled == []


async def test_a_chat_runs_in_order_and_does_not_hold_up_the_others() -> None:
    fake = FakeTelegram(
        make_update(1, chat_id=7), make_update(2, chat_id=7), make_update(3, chat_id=8)
    )
    release = asyncio.Event()
    started: list[str] = []
    finished: list[str] = []

    async def on_inbound(inbound: InboundMessage) -> None:
        started.append(inbound.external_id)
        if inbound.external_id == "1":
            await release.wait()
        finished.append(inbound.external_id)

    async def release_when_the_other_chat_is_done() -> list[str]:
        await until(lambda: "3" in finished)
        seen = list(started)
        release.set()
        return seen

    watcher = asyncio.create_task(release_when_the_other_chat_is_done())
    await _poll(fake, on_inbound, lambda: not fake.updates)
    assert await watcher == ["1", "3"]  # "2" waited for "1"; "3" didn't
    assert finished == ["3", "1", "2"]


async def test_a_turn_that_fails_is_tried_again() -> None:
    fake = FakeTelegram(make_update(41))
    calls = 0

    async def on_inbound(inbound: InboundMessage) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database down")

    await _poll(fake, on_inbound, lambda: not fake.updates)
    assert calls == 2


async def test_a_turn_that_keeps_failing_is_dropped_so_it_cannot_block() -> None:
    fake = FakeTelegram(make_update(41, text="mi teléfono es 3001234567"))
    calls = 0

    async def on_inbound(inbound: InboundMessage) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"could not handle {inbound.text}")

    with capture_logs() as logs:
        await _poll(fake, on_inbound, lambda: not fake.updates)
    assert calls == MAX_TURN_ATTEMPTS
    assert logs[-1]["event"] == "telegram_update_dropped"
    assert "3001234567" not in json.dumps(logs)


async def test_polling_recovers_from_errors() -> None:
    fake = FakeTelegram(make_update(41))
    fake.failures = [httpx.ConnectError("no route"), refusal(502)]
    handled: list[str] = []

    async def on_inbound(inbound: InboundMessage) -> None:
        handled.append(inbound.external_id)

    with capture_logs() as logs:
        await _poll(fake, on_inbound, lambda: not fake.updates)
    assert handled == ["41"]
    assert [log["event"] for log in logs] == ["telegram_poll_failed"] * 2
    assert TOKEN not in json.dumps(logs)


async def test_a_wrong_token_stops_the_channel() -> None:
    fake = FakeTelegram()
    fake.failures = [refusal(401)]

    async def on_inbound(inbound: InboundMessage) -> None:
        raise AssertionError("nothing to handle")

    with pytest.raises(BadTokenError) as raised:
        await _poll(fake, on_inbound, lambda: False)
    assert TOKEN not in str(raised.value)


async def test_stopping_lets_a_running_turn_finish() -> None:
    fake = FakeTelegram(make_update(41))
    started = asyncio.Event()
    finished = False

    async def on_inbound(inbound: InboundMessage) -> None:
        nonlocal finished
        started.set()
        await asyncio.sleep(0.05)
        finished = True

    await _poll(fake, on_inbound, started.is_set)
    assert finished


# --- sending ------------------------------------------------------------------------------


def _replies(*titles: str) -> tuple[QuickReply, ...]:
    return tuple(QuickReply(title=title, payload=title) for title in titles)


async def test_quick_replies_become_a_reply_keyboard() -> None:
    fake = FakeTelegram()
    async with _channel(fake) as channel:
        message = OutboundMessage(text="¿Confirmamos?", quick_replies=_replies("Sí", "No"))
        result = await channel.send("7", message, dedupe_key="k")
    assert result.external_id == "901"
    [sent] = fake.sent
    assert (sent["chat_id"], sent["text"]) == ("7", "¿Confirmamos?")
    assert sent["reply_markup"]["keyboard"] == [[{"text": "Sí"}], [{"text": "No"}]]
    assert sent["reply_markup"]["one_time_keyboard"] is True


async def test_a_message_without_replies_removes_the_old_keyboard() -> None:
    fake = FakeTelegram()
    async with _channel(fake) as channel:
        await channel.send("7", OutboundMessage(text="Listo"), dedupe_key="k")
    assert fake.sent[0]["reply_markup"] == {"remove_keyboard": True}


async def test_too_many_replies_become_a_numbered_list() -> None:
    fake = FakeTelegram()
    titles = [f"Opción {n}" for n in range(1, 10)]
    async with _channel(fake) as channel:
        message = OutboundMessage(text="¿Cuál?", quick_replies=_replies(*titles))
        await channel.send("7", message, dedupe_key="k")
    assert fake.sent[0]["text"].endswith("9. Opción 9")
    assert fake.sent[0]["reply_markup"] == {"remove_keyboard": True}


async def test_a_rate_limit_says_how_long_to_wait() -> None:
    fake = FakeTelegram()
    fake.failures = [refusal(429, retry_after=17)]
    async with _channel(fake) as channel:
        with pytest.raises(ChannelError) as raised:
            await channel.send("7", OutboundMessage(text="Hola"), dedupe_key="k")
    assert raised.value.retry_after == timedelta(seconds=17)


@pytest.mark.parametrize(
    "failure",
    [refusal(403), httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}/sendMessage")],
)
async def test_a_failed_send_never_shows_the_token(failure: httpx.Response | Exception) -> None:
    fake = FakeTelegram()
    fake.failures = [failure]
    async with _channel(fake) as channel:
        with pytest.raises(ChannelError) as raised:
            await channel.send("7", OutboundMessage(text="Hola"), dedupe_key="k")
    assert TOKEN not in str(raised.value)
    assert raised.value.retry_after is None
    # A chained httpx error would print the URL, token included, in a traceback.
    assert raised.value.__context__ is None or raised.value.__suppress_context__
