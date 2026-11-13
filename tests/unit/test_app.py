"""The wiring: a customer message goes in through a channel and the reply comes out."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest

from pedidos_bot.app import deliver_forever, serve
from pedidos_bot.channels.base import ChannelError, OutboundMessage, SendResult
from pedidos_bot.channels.memory import InMemoryChannel
from pedidos_bot.channels.telegram import TelegramChannel
from pedidos_bot.storage import base
from pedidos_bot.storage.base import OutboxEntry
from pedidos_bot.storage.memory import InMemoryRepository
from tests.factories import make_config, make_inbound, sample_config
from tests.telegram_fake import TOKEN, FakeTelegram, make_tap, make_update, until


async def test_a_telegram_customer_gets_an_answer() -> None:
    config = make_config()
    fake = FakeTelegram(make_update(1, chat_id=7, text="hola"))
    repository = InMemoryRepository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
        channel = TelegramChannel(TOKEN, client, retry_wait=0.01)
        serving = asyncio.create_task(serve(config, repository, channel))
        await until(lambda: fake.sent and not fake.updates)
        channel.stop()
        await serving
    [sent] = fake.sent
    assert sent["chat_id"] == "7"
    assert config.tone.greeting in sent["text"]
    assert await repository.load("telegram", "7") is not None
    assert await repository.pending_outbound() == []


class _FlakyChannel(InMemoryChannel):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def send(self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str) -> SendResult:
        self.attempts += 1
        if self.attempts == 1:
            raise ChannelError("down")
        return await super().send(chat_ref, message, dedupe_key=dedupe_key)


async def test_the_outbox_loop_retries_a_failed_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "BASE_RETRY_DELAY", timedelta(0))
    repository = InMemoryRepository()
    async with repository.turn(make_inbound()) as scope:
        assert scope is not None
        scope.enqueue(OutboxEntry("chat", OutboundMessage(text="Hola"), "k1"))
    channel = _FlakyChannel()
    loop = asyncio.create_task(deliver_forever(repository, channel, interval=0.01))
    await until(lambda: channel.sent)
    loop.cancel()
    assert channel.attempts == 2
    assert [sent.dedupe_key for sent in channel.sent] == ["k1"]


async def test_a_cashier_approves_an_order_from_the_staff_chat() -> None:
    config = sample_config("burger_demo")
    said = ("una sencilla", "no", "para recoger", "nequi", "sí")
    fake = FakeTelegram(*(make_update(n, chat_id=7, text=text) for n, text in enumerate(said, 1)))
    repository = InMemoryRepository()

    def to_staff() -> list[dict[str, Any]]:
        return [sent for sent in fake.sent if sent["chat_id"] == "-100"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
        channel = TelegramChannel(TOKEN, client, staff_chat="-100", retry_wait=0.01)
        serving = asyncio.create_task(serve(config, repository, channel, staff_chat="-100"))
        await until(to_staff)
        [approve, _reject] = to_staff()[0]["reply_markup"]["inline_keyboard"][0]
        fake.updates.append(make_update(20, chat_id=-100, chat_type="group", text="¿quién va?"))
        fake.updates.append(make_tap(21, approve["callback_data"]))
        await until(lambda: len(to_staff()) == 2 and not fake.updates)
        channel.stop()
        await serving
    assert "aprobado" in to_staff()[1]["text"]
    assert "confirmado" in [s for s in fake.sent if s["chat_id"] == "7"][-1]["text"]
    assert fake.acknowledged == ["cb21"]
    assert await repository.load("telegram", "-100") is None  # staff chatter is no conversation
