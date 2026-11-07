"""In-memory repository for tests and the REPL. Same guarantees as the real one, minus restarts."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pedidos_bot.channels.base import InboundMessage
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.storage.base import BufferedScope, OutboxEntry, TurnScope


class InMemoryRepository:
    def __init__(self) -> None:
        self.conversations: dict[tuple[str, str], Conversation] = {}
        self._processed: set[tuple[str, str]] = set()
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._outbox: list[OutboxEntry] = []

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
            self._outbox.extend(scope.outbox)

    async def load(self, channel: str, chat_ref: str) -> Conversation | None:
        return self.conversations.get((channel, chat_ref))

    async def pending_outbound(self) -> list[OutboxEntry]:
        return list(self._outbox)

    async def mark_sent(self, dedupe_key: str) -> None:
        self._outbox = [entry for entry in self._outbox if entry.dedupe_key != dedupe_key]
