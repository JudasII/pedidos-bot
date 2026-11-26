"""The persistence contract. One customer message is one atomic turn.

`ConversationRepository.turn` is the boundary: it drops duplicates, serializes turns of a
conversation and commits state and outbound messages together, or not at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import structlog

from pedidos_bot.channels.base import Channel, ChannelError, InboundMessage, OutboundMessage
from pedidos_bot.domain.order import Order
from pedidos_bot.engine.conversation import Conversation

_logger = structlog.get_logger()

MAX_SEND_ATTEMPTS = 8
BASE_RETRY_DELAY = timedelta(seconds=2)
MAX_RETRY_DELAY = timedelta(minutes=5)


@dataclass(frozen=True)
class OutboxEntry:
    chat_ref: str
    message: OutboundMessage
    dedupe_key: str


Sender = Callable[[OutboxEntry], Awaitable[None]]
OrderLoader = Callable[[str], Awaitable[Order | None]]


def log_given_up(entry: OutboxEntry) -> None:
    _logger.error("outbound_given_up", dedupe_key=entry.dedupe_key)


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next try, after `attempts` failures: doubles, up to `MAX_RETRY_DELAY`."""
    return min(BASE_RETRY_DELAY * (1 << (attempts - 1)), MAX_RETRY_DELAY)


def delay_after(error: Exception, attempts: int) -> timedelta:
    """The wait after a failed send: what the channel asked for, else our own backoff."""
    if isinstance(error, ChannelError) and error.retry_after is not None:
        return error.retry_after
    return retry_delay(attempts)


class TurnScope(Protocol):
    @property
    def conversation(self) -> Conversation: ...

    def save(self, conversation: Conversation) -> None: ...

    async def load_order(self, idempotency_key: str) -> Order | None:
        """Read an order as stored before this turn, without leaving the turn.

        Inside a turn, read orders only through here: asking the repository would need a
        second connection while this turn holds one, and enough such turns starve the pool.
        """
        ...

    def enqueue(self, entry: OutboxEntry) -> None: ...

    def place_order(self, order: Order) -> None: ...

    def withdraw_order(self, idempotency_key: str) -> None: ...


class BufferedScope:
    """Collects a turn's writes, so a repository can commit them all at the end."""

    def __init__(self, conversation: Conversation, load_order: OrderLoader) -> None:
        self._conversation = conversation
        self._load_order = load_order
        self.outbox: list[OutboxEntry] = []
        self.orders: list[Order] = []
        self.withdrawn: list[str] = []

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    def save(self, conversation: Conversation) -> None:
        self._conversation = conversation

    async def load_order(self, idempotency_key: str) -> Order | None:
        return await self._load_order(idempotency_key)

    def enqueue(self, entry: OutboxEntry) -> None:
        if any(queued.dedupe_key == entry.dedupe_key for queued in self.outbox):
            raise ValueError(f"two messages of one turn share the key '{entry.dedupe_key}'")
        self.outbox.append(entry)

    def place_order(self, order: Order) -> None:
        self.orders.append(order)

    def withdraw_order(self, idempotency_key: str) -> None:
        self.withdrawn.append(idempotency_key)


class ConversationRepository(Protocol):
    def turn(self, inbound: InboundMessage) -> AbstractAsyncContextManager[TurnScope | None]:
        """Open the turn for `inbound`. Yields None if the message was already processed."""
        ...

    async def load(self, channel: str, chat_ref: str) -> Conversation | None: ...

    async def load_order(self, idempotency_key: str) -> Order | None: ...

    async def load_by_code(self, code: str) -> Conversation | None:
        """The conversation whose `code` this is, as staff typed it (upper case)."""
        ...

    async def in_handoff(self) -> list[Conversation]:
        """Conversations a person is answering, or was."""
        ...

    async def pending_outbound(self) -> list[OutboxEntry]: ...

    async def deliver_next(self, send: Sender) -> bool:
        """Attempt the oldest message that is due. False if there is none.

        A chat's messages go out in the order they were written: a message is not due while
        an older one for the same chat is being sent or waiting for its retry. Other chats
        are not held up, and a message that is given up on lets the rest of its chat through.

        Any exception from `send` counts as a failed attempt. Delivery is at-least-once:
        after a crash the message is sent again with the same dedupe key.
        """
        ...


async def deliver_pending(repository: ConversationRepository, channel: Channel) -> int:
    """Attempt everything that is due. A failed message waits for its retry; the rest go on."""
    sent = 0

    async def send(entry: OutboxEntry) -> None:
        nonlocal sent
        await channel.send(entry.chat_ref, entry.message, dedupe_key=entry.dedupe_key)
        sent += 1

    while await repository.deliver_next(send):
        pass
    return sent
