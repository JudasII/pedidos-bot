"""The composition root: the one place that builds the real objects and connects them."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import structlog

from pedidos_bot.channels.base import Channel, InboundHandler, InboundMessage
from pedidos_bot.channels.telegram import TelegramChannel
from pedidos_bot.channels.terminal import EXIT, TerminalChannel
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.service import ConversationService
from pedidos_bot.llm.fake import RuleBasedLLM
from pedidos_bot.observability.logging import configure_logging
from pedidos_bot.settings import Settings
from pedidos_bot.storage.base import ConversationRepository, deliver_pending
from pedidos_bot.storage.memory import InMemoryRepository
from pedidos_bot.storage.postgres import PostgresRepository

OUTBOX_INTERVAL = 2.0
"""Seconds between checks for messages whose retry is due."""

_logger = structlog.get_logger()


def build_service(
    config: RestaurantConfig, repository: ConversationRepository, pii_salt: str | None = None
) -> ConversationService:
    # TODO(M5): the real LLM goes here; until then every process uses the rule-based fake.
    llm = RuleBasedLLM(MenuResolver(config))
    return ConversationService(config, llm, repository, pii_salt)


def reply_through(
    service: ConversationService, repository: ConversationRepository, channel: Channel
) -> InboundHandler:
    """Handle a message and send what it left in the outbox, without waiting for the loop."""

    async def on_inbound(inbound: InboundMessage) -> None:
        await service.handle(inbound)
        await deliver_pending(repository, channel)

    return on_inbound


async def deliver_forever(
    repository: ConversationRepository, channel: Channel, interval: float = OUTBOX_INTERVAL
) -> None:
    """Send what is due, again and again: this is what retries a failed send."""
    while True:
        try:
            await deliver_pending(repository, channel)
        except Exception as error:
            _logger.error("outbox_loop_failed", error=type(error).__name__)
        await asyncio.sleep(interval)


async def serve(
    config: RestaurantConfig,
    repository: ConversationRepository,
    channel: Channel,
    pii_salt: str | None = None,
) -> None:
    """Answer customers until the channel stops."""
    service = build_service(config, repository, pii_salt)
    outbox = asyncio.create_task(deliver_forever(repository, channel))
    try:
        await channel.start(reply_through(service, repository, channel))
    finally:
        outbox.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await outbox


async def run_telegram(config: RestaurantConfig, settings: Settings) -> None:
    """One process for one restaurant: its bot and its database (ADR-0005)."""
    configure_logging(settings.log_level)
    token = settings.telegram_bot_token.get_secret_value()
    async with (
        PostgresRepository.open(settings.database_url) as repository,
        httpx.AsyncClient() as client,
    ):
        channel = TelegramChannel(token, client)
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(stop_signal, channel.stop)
        _logger.info("telegram_started", restaurant=config.restaurant.id)
        await serve(config, repository, channel, settings.pii_hash_salt)
        _logger.info("telegram_stopped", restaurant=config.restaurant.id)


async def run_chat(
    config: RestaurantConfig,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Chat with the engine in a terminal. Nothing is kept after it ends."""
    repository = InMemoryRepository()
    service = build_service(config, repository)
    channel = TerminalChannel(write)
    write(f"{config.restaurant.name} (fake LLM). Write {EXIT} to quit.\n")
    count = 0
    while True:
        try:
            text = await asyncio.to_thread(read, "tú> ")
        except EOFError:
            return
        if text.strip() == EXIT:
            return
        count += 1
        message = InboundMessage(
            channel=channel.name,
            external_id=str(count),
            chat_ref=channel.name,
            customer_ref=channel.name,
            sent_at=datetime.now(UTC),
            text=text,
        )
        result = await service.handle(message)
        await deliver_pending(repository, channel)
        if result.conversation is not None:
            write(f"[{result.conversation.lifecycle.state}]\n")
