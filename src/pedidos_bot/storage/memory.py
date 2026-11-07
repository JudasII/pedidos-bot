"""In-memory repository for tests and the REPL. Same guarantees as the real one, minus restarts."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pedidos_bot.channels.base import InboundMessage
from pedidos_bot.domain.order import Order, OrderStatus
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.storage.base import (
    MAX_SEND_ATTEMPTS,
    BufferedScope,
    OutboxEntry,
    Sender,
    TurnScope,
    log_given_up,
    retry_delay,
)


@dataclass(eq=False)
class _Pending:
    entry: OutboxEntry
    attempts: int = 0
    next_attempt_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    claimed: bool = False


class InMemoryRepository:
    def __init__(self) -> None:
        self.conversations: dict[tuple[str, str], Conversation] = {}
        self._processed: set[tuple[str, str]] = set()
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._outbox: list[_Pending] = []
        self._orders: dict[str, Order] = {}

    @asynccontextmanager
    async def turn(self, inbound: InboundMessage) -> AsyncIterator[TurnScope | None]:
        key = (inbound.channel, inbound.chat_ref)
        message_id = (inbound.channel, inbound.external_id)
        async with self._locks[key]:
            if message_id in self._processed:
                yield None
                return
            current = self.conversations.get(key)
            scope = BufferedScope(current or Conversation(channel=key[0], chat_ref=key[1]))
            yield scope  # an exception here discards the whole turn
            self._processed.add(message_id)
            self.conversations[key] = scope.conversation
            self._outbox.extend(_Pending(entry) for entry in scope.outbox)
            for order in scope.orders:
                previous = self._orders.get(order.idempotency_key)
                kept_id = {"id": previous.id} if previous else {}
                self._orders[order.idempotency_key] = order.model_copy(update=kept_id)
            for order_key in scope.withdrawn:
                withdrawn = {"status": OrderStatus.WITHDRAWN}
                self._orders[order_key] = self._orders[order_key].model_copy(update=withdrawn)

    async def load(self, channel: str, chat_ref: str) -> Conversation | None:
        return self.conversations.get((channel, chat_ref))

    async def load_order(self, idempotency_key: str) -> Order | None:
        return self._orders.get(idempotency_key)

    async def pending_outbound(self) -> list[OutboxEntry]:
        return [item.entry for item in self._outbox]

    async def deliver_next(self, send: Sender) -> bool:
        now = datetime.now(UTC)
        due = next((i for i in self._outbox if i.next_attempt_at <= now and not i.claimed), None)
        if due is None:
            return False
        due.claimed = True
        try:
            await send(due.entry)
        except Exception:
            due.claimed = False
            due.attempts += 1
            due.next_attempt_at = now + retry_delay(due.attempts)
            if due.attempts >= MAX_SEND_ATTEMPTS:
                self._outbox.remove(due)
                log_given_up(due.entry)
        else:
            self._outbox.remove(due)
        return True
