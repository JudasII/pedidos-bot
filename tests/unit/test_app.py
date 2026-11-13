"""The wiring: a customer message goes in through a channel and the reply comes out."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from pedidos_bot.app import deliver_forever, serve
from pedidos_bot.channels.base import ChannelError, OutboundMessage, SendResult
from pedidos_bot.channels.memory import InMemoryChannel
from pedidos_bot.channels.telegram import TelegramChannel
from pedidos_bot.storage import base
from pedidos_bot.storage.base import OutboxEntry
from pedidos_bot.storage.memory import InMemoryRepository
from tests.factories import make_config, make_inbound
from tests.telegram_fake import TOKEN, FakeTelegram, make_update, until


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
