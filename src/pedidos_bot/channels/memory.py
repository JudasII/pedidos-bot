"""An in-memory channel for tests, configurable to behave like a real one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    ChannelError,
    InboundHandler,
    InboundMessage,
    OutboundMessage,
    SendResult,
    degrade,
    within_freeform_window,
)

WHATSAPP_LIKE = ChannelCapabilities(
    max_buttons=3,
    max_button_title=20,
    freeform_window=timedelta(hours=24),
    requires_templates_outside_window=True,
)


@dataclass(frozen=True)
class SentMessage:
    chat_ref: str
    message: OutboundMessage
    dedupe_key: str


class InMemoryChannel:
    def __init__(self, capabilities: ChannelCapabilities | None = None) -> None:
        self.capabilities = capabilities or ChannelCapabilities()
        self.sent: list[SentMessage] = []
        self._on_inbound: InboundHandler | None = None
        self._results: dict[str, SendResult] = {}
        self._last_inbound: dict[str, datetime] = {}
        self.now: datetime | None = None
        """Override the clock for window checks; defaults to the last inbound time."""

    async def start(self, on_inbound: InboundHandler) -> None:
        self._on_inbound = on_inbound

    async def receive(self, message: InboundMessage) -> None:
        self._last_inbound[message.chat_ref] = message.sent_at
        if self._on_inbound is not None:
            await self._on_inbound(message)

    async def send(self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str) -> SendResult:
        if dedupe_key in self._results:
            return self._results[dedupe_key]
        last_inbound = self._last_inbound.get(chat_ref)
        if last_inbound is not None and message.template is None:
            now = self.now or last_inbound
            needs_template = self.capabilities.requires_templates_outside_window
            if needs_template and not self.can_send_freeform(last_inbound, now):
                raise ChannelError(f"free-form window closed for {chat_ref}")
        self.sent.append(SentMessage(chat_ref, degrade(message, self.capabilities), dedupe_key))
        result = SendResult(external_id=f"out-{len(self.sent)}")
        self._results[dedupe_key] = result
        return result

    def can_send_freeform(self, last_inbound_at: datetime, now: datetime) -> bool:
        return within_freeform_window(self.capabilities, last_inbound_at, now)
