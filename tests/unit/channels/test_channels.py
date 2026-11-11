"""Channel behavior, against a fake with the limits of a strict messaging platform."""

from datetime import UTC, datetime, timedelta

import pytest

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    ChannelError,
    InboundMessage,
    MessageKind,
    OutboundMessage,
    QuickReply,
    TemplateRef,
    degrade,
)
from pedidos_bot.channels.memory import WHATSAPP_LIKE, InMemoryChannel
from tests.factories import make_inbound

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def _replies(*titles: str) -> tuple[QuickReply, ...]:
    return tuple(QuickReply(title=title, payload=title) for title in titles)


def test_buttons_that_fit_are_kept() -> None:
    message = OutboundMessage(text="¿Cuál?", quick_replies=_replies("Uno", "Dos", "Tres"))
    assert degrade(message, WHATSAPP_LIKE) == message


@pytest.mark.parametrize(
    ("titles", "capabilities"),
    [
        (("A", "B", "C", "D"), WHATSAPP_LIKE),
        (("Un título demasiado largo aquí", "B"), WHATSAPP_LIKE),
        (("A", "B"), ChannelCapabilities(max_buttons=0)),
    ],
)
def test_buttons_that_do_not_fit_become_a_numbered_list(
    titles: tuple[str, ...], capabilities: ChannelCapabilities
) -> None:
    message = OutboundMessage(text="¿Cuál?", quick_replies=_replies(*titles))
    degraded = degrade(message, capabilities)
    assert degraded.quick_replies == ()
    assert degraded.text.startswith("¿Cuál?\n\n1. ")
    assert f"{len(titles)}. {titles[-1]}" in degraded.text


def test_message_without_buttons_is_unchanged() -> None:
    message = OutboundMessage(text="Hola")
    assert degrade(message, ChannelCapabilities()) == message


def test_button_payload_wins_over_text() -> None:
    reply = make_inbound(kind=MessageKind.INTERACTIVE_REPLY, text="Sí", reply_payload=" confirm ")
    assert reply.content == "confirm"
    assert make_inbound(text=" hola ").content == "hola"
    assert make_inbound(kind=MessageKind.VOICE).content == ""


async def test_channel_delivers_inbound_to_the_handler() -> None:
    received: list[InboundMessage] = []

    async def handler(message: InboundMessage) -> None:
        received.append(message)

    channel = InMemoryChannel()
    await channel.start(handler)
    await channel.receive(make_inbound(text="hola"))
    assert [m.text for m in received] == ["hola"]


async def test_send_degrades_and_ignores_repeated_dedupe_keys() -> None:
    channel = InMemoryChannel(WHATSAPP_LIKE)
    message = OutboundMessage(text="¿Cuál?", quick_replies=_replies("A", "B", "C", "D"))
    first = await channel.send("chat", message, dedupe_key="k1")
    again = await channel.send("chat", message, dedupe_key="k1")
    assert first == again
    assert len(channel.sent) == 1
    assert "4. D" in channel.sent[0].message.text


async def test_free_text_is_refused_outside_the_window_but_templates_pass() -> None:
    channel = InMemoryChannel(WHATSAPP_LIKE)
    await channel.receive(make_inbound(text="hola"))
    channel.now = NOW + timedelta(hours=25)
    with pytest.raises(ChannelError, match="window closed"):
        await channel.send("chat", OutboundMessage(text="¿Seguimos?"), dedupe_key="k1")
    template = OutboundMessage(text="¿Seguimos?", template=TemplateRef(name="resume"))
    await channel.send("chat", template, dedupe_key="k2")
    assert len(channel.sent) == 1


def test_window_limits() -> None:
    channel = InMemoryChannel(WHATSAPP_LIKE)
    assert channel.can_send_freeform(NOW, NOW + timedelta(hours=24))
    assert not channel.can_send_freeform(NOW, NOW + timedelta(hours=24, seconds=1))
    assert InMemoryChannel().can_send_freeform(NOW, NOW + timedelta(days=30))
