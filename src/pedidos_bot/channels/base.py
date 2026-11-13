"""The channel interface. The engine only knows these types, never a concrete channel.

It is shaped by the strictest channel we expect: limited buttons with short titles, a
free-form window after the last customer message, duplicate webhooks and non-text messages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from pedidos_bot.models import FrozenModel


class MessageKind(StrEnum):
    TEXT = "text"
    INTERACTIVE_REPLY = "interactive_reply"
    VOICE = "voice"
    LOCATION = "location"
    IMAGE = "image"
    DOCUMENT = "document"
    STICKER = "sticker"
    UNSUPPORTED = "unsupported"


class GeoPoint(FrozenModel):
    latitude: float
    longitude: float


class InboundMessage(FrozenModel):
    channel: str
    external_id: str
    """Unique per channel; a repeated id is a duplicate delivery."""
    chat_ref: str
    customer_ref: str
    sent_at: datetime
    kind: MessageKind = MessageKind.TEXT
    text: str | None = None
    reply_payload: str | None = None
    media_ref: str | None = None
    location: GeoPoint | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    """The channel's original payload. Never log it unmasked."""

    @property
    def content(self) -> str:
        """What the customer said: the button payload, or the typed text."""
        return (self.reply_payload or self.text or "").strip()


class QuickReply(FrozenModel):
    title: str
    payload: str


class TemplateRef(FrozenModel):
    name: str
    params: tuple[str, ...] = ()


class OutboundMessage(FrozenModel):
    text: str
    quick_replies: tuple[QuickReply, ...] = ()
    template: TemplateRef | None = None
    """Required by some channels outside the free-form window."""


class ChannelCapabilities(FrozenModel):
    max_buttons: int = 0
    max_button_title: int = 20
    freeform_window: timedelta | None = None
    """How long after the last inbound message free text is allowed; None means always."""
    requires_templates_outside_window: bool = False


class SendResult(FrozenModel):
    external_id: str


class ChannelError(Exception):
    """The channel refused or failed to deliver a message."""

    def __init__(self, message: str, *, retry_after: timedelta | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        """How long the channel asked us to wait before trying again, if it said."""


InboundHandler = Callable[[InboundMessage], Awaitable[None]]


class Channel(Protocol):
    capabilities: ChannelCapabilities

    async def start(self, on_inbound: InboundHandler) -> None:
        """Deliver inbound messages to `on_inbound` until the channel stops.

        A channel that polls blocks here; one that is driven from outside returns at once.
        """
        ...

    async def send(
        self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str
    ) -> SendResult: ...

    def can_send_freeform(self, last_inbound_at: datetime, now: datetime) -> bool: ...


def within_freeform_window(
    capabilities: ChannelCapabilities, last_inbound_at: datetime, now: datetime
) -> bool:
    window = capabilities.freeform_window
    return window is None or now - last_inbound_at <= window


def degrade(message: OutboundMessage, capabilities: ChannelCapabilities) -> OutboundMessage:
    """Turn quick replies the channel can't show into a numbered list in the text."""
    replies = message.quick_replies
    fits = len(replies) <= capabilities.max_buttons and all(
        len(reply.title) <= capabilities.max_button_title for reply in replies
    )
    if fits:
        return message
    numbered = "\n".join(f"{i}. {reply.title}" for i, reply in enumerate(replies, 1))
    return message.model_copy(update={"text": f"{message.text}\n\n{numbered}", "quick_replies": ()})
