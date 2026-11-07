"""The persistence contract. One customer message is one atomic turn.

`ConversationRepository.turn` is the boundary: it drops duplicates, serializes turns of a
conversation and commits state and outbound messages together, or not at all.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from pedidos_bot.channels.base import Channel, InboundMessage, OutboundMessage
from pedidos_bot.engine.conversation import Conversation


@dataclass(frozen=True)
class OutboxEntry:
    chat_ref: str
    message: OutboundMessage
    dedupe_key: str


class TurnScope(Protocol):
    @property
    def conversation(self) -> Conversation: ...

    def save(self, conversation: Conversation) -> None: ...

    def enqueue(self, entry: OutboxEntry) -> None: ...


class ConversationRepository(Protocol):
    def turn(self, inbound: InboundMessage) -> AbstractAsyncContextManager[TurnScope | None]:
        """Open the turn for `inbound`. Yields None if the message was already processed."""
        ...

    async def pending_outbound(self) -> list[OutboxEntry]: ...

    async def mark_sent(self, dedupe_key: str) -> None: ...


async def deliver_pending(repository: ConversationRepository, channel: Channel) -> int:
    """Send everything in the outbox, oldest first. Returns how many were sent."""
    entries = await repository.pending_outbound()
    for entry in entries:
        await channel.send(entry.chat_ref, entry.message, dedupe_key=entry.dedupe_key)
        await repository.mark_sent(entry.dedupe_key)
    return len(entries)
