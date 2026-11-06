"""Development REPL: chat with the engine in a terminal, using the rule-based fake LLM."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    InboundHandler,
    InboundMessage,
    OutboundMessage,
    SendResult,
    degrade,
)
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.service import ConversationService
from pedidos_bot.llm.fake import RuleBasedLLM
from pedidos_bot.storage.base import deliver_pending
from pedidos_bot.storage.memory import InMemoryRepository

_CHAT = "terminal"
_EXIT = "/salir"


class TerminalChannel:
    def __init__(self, write: Callable[[str], None] = print) -> None:
        self.capabilities = ChannelCapabilities()
        self._write = write

    async def start(self, on_inbound: InboundHandler) -> None:
        """Nothing to do: `run_chat` drives the loop."""

    async def send(self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str) -> SendResult:
        self._write(f"bot> {degrade(message, self.capabilities).text}\n")
        return SendResult(external_id=dedupe_key)

    def can_send_freeform(self, last_inbound_at: datetime, now: datetime) -> bool:
        return True


async def run_chat(
    config: RestaurantConfig,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    repository = InMemoryRepository()
    llm = RuleBasedLLM(MenuResolver(config))
    service = ConversationService(config, llm, repository)
    channel = TerminalChannel(write)
    write(f"{config.restaurant.name} (fake LLM). Write {_EXIT} to quit.\n")
    count = 0
    while True:
        try:
            text = await asyncio.to_thread(read, "tú> ")
        except EOFError:
            return
        if text.strip() == _EXIT:
            return
        count += 1
        message = InboundMessage(
            channel=_CHAT,
            external_id=str(count),
            chat_ref=_CHAT,
            customer_ref=_CHAT,
            sent_at=datetime.now(UTC),
            text=text,
        )
        result = await service.handle(message)
        await deliver_pending(repository, channel)
        if result.conversation is not None:
            write(f"[{result.conversation.lifecycle.state}]\n")
