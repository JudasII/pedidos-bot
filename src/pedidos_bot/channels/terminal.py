"""A channel that prints to the terminal, for the development REPL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    InboundHandler,
    OutboundMessage,
    SendResult,
    degrade,
)

EXIT = "/salir"


class TerminalChannel:
    name = "terminal"

    def __init__(self, write: Callable[[str], None] = print) -> None:
        self.capabilities = ChannelCapabilities()
        self._write = write

    async def start(self, on_inbound: InboundHandler) -> None:
        """Nothing to do: the REPL drives the loop."""

    async def send(self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str) -> SendResult:
        self._write(f"bot> {degrade(message, self.capabilities).text}\n")
        return SendResult(external_id=dedupe_key)

    def can_send_freeform(self, last_inbound_at: datetime, now: datetime) -> bool:
        return True
