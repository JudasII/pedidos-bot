"""PostgreSQL repository (ADR-0002). One turn is one transaction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from pedidos_bot.channels.base import ChannelError, InboundMessage, OutboundMessage
from pedidos_bot.domain.order import Order
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.storage.base import (
    MAX_SEND_ATTEMPTS,
    BufferedScope,
    OutboxEntry,
    Sender,
    TurnScope,
    retry_delay,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_LOCK = 7_203_451
"""Arbitrary advisory lock id: processes starting together must not migrate at once."""


async def migrate(connection: AsyncConnection) -> list[str]:
    """Apply pending migrations in order, each once. Returns the ones applied."""
    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
    await connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               name text PRIMARY KEY,
               applied_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    cursor = await connection.execute("SELECT name FROM schema_migrations")
    done = {row[0] for row in await cursor.fetchall()}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name not in done:
            await connection.execute(path.read_text(encoding="utf-8").encode())
            await connection.execute(
                "INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied


class PostgresRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def open(cls, database_url: str) -> AsyncIterator[Self]:
        """Connect and bring the schema up to date."""
        async with AsyncConnectionPool(database_url, open=False) as pool:
            async with pool.connection() as connection:
                await migrate(connection)
            yield cls(pool)

    @asynccontextmanager
    async def turn(self, inbound: InboundMessage) -> AsyncIterator[TurnScope | None]:
        async with self._pool.connection() as connection:  # commits, or rolls back on error
            cursor = await connection.execute(
                """INSERT INTO inbound_messages (channel, external_id) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING RETURNING 1""",
                (inbound.channel, inbound.external_id),
            )
            if await cursor.fetchone() is None:
                yield None
                return
            scope = BufferedScope(await self._lock_conversation(connection, inbound))
            yield scope
            await connection.execute(
                """UPDATE conversations SET state = %s, updated_at = now()
                   WHERE channel = %s AND chat_ref = %s""",
                (_json(scope.conversation), inbound.channel, inbound.chat_ref),
            )
            for entry in scope.outbox:
                await connection.execute(
                    """INSERT INTO outbox (dedupe_key, chat_ref, message) VALUES (%s, %s, %s)
                       ON CONFLICT (dedupe_key) DO NOTHING""",
                    (entry.dedupe_key, entry.chat_ref, _json(entry.message)),
                )
            for order in scope.orders:
                await connection.execute(
                    """INSERT INTO orders (id, idempotency_key, conversation_id, data)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO UPDATE SET
                           data = jsonb_set(EXCLUDED.data, '{id}', to_jsonb(orders.id)),
                           updated_at = now()""",
                    (order.id, order.idempotency_key, order.conversation_id, _json(order)),
                )

    async def _lock_conversation(
        self, connection: AsyncConnection, inbound: InboundMessage
    ) -> Conversation:
        """Load the conversation, holding its row lock until the turn ends."""
        new = Conversation(channel=inbound.channel, chat_ref=inbound.chat_ref)
        await connection.execute(
            """INSERT INTO conversations (channel, chat_ref, state) VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (inbound.channel, inbound.chat_ref, _json(new)),
        )
        cursor = await connection.execute(
            "SELECT state FROM conversations WHERE channel = %s AND chat_ref = %s FOR UPDATE",
            (inbound.channel, inbound.chat_ref),
        )
        row = await cursor.fetchone()
        assert row is not None
        return Conversation.model_validate(row[0])

    async def load(self, channel: str, chat_ref: str) -> Conversation | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT state FROM conversations WHERE channel = %s AND chat_ref = %s",
                (channel, chat_ref),
            )
            row = await cursor.fetchone()
        return Conversation.model_validate(row[0]) if row else None

    async def load_order(self, idempotency_key: str) -> Order | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT data FROM orders WHERE idempotency_key = %s", (idempotency_key,)
            )
            row = await cursor.fetchone()
        return Order.model_validate(row[0]) if row else None

    async def pending_outbound(self) -> list[OutboxEntry]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT chat_ref, message, dedupe_key FROM outbox "
                "WHERE sent_at IS NULL AND failed_at IS NULL ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [OutboxEntry(r[0], OutboundMessage.model_validate(r[1]), r[2]) for r in rows]

    async def deliver_next(self, send: Sender) -> bool:
        # The row stays locked during the send: other workers skip it, and if this process
        # dies the lock goes away and the message is due again.
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT id, chat_ref, message, dedupe_key, attempts FROM outbox
                   WHERE sent_at IS NULL AND failed_at IS NULL AND next_attempt_at <= now()
                   ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"""
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            row_id, chat_ref, message, dedupe_key, attempts = row
            try:
                await send(
                    OutboxEntry(chat_ref, OutboundMessage.model_validate(message), dedupe_key)
                )
            except ChannelError:
                await self._record_failure(connection, row_id, attempts + 1)
            else:
                await connection.execute(
                    "UPDATE outbox SET sent_at = now() WHERE id = %s", (row_id,)
                )
            return True

    async def _record_failure(
        self, connection: AsyncConnection, row_id: int, attempts: int
    ) -> None:
        await connection.execute(
            """UPDATE outbox SET attempts = %s, next_attempt_at = now() + %s,
                   failed_at = CASE WHEN %s THEN now() END
               WHERE id = %s""",
            (attempts, retry_delay(attempts), attempts >= MAX_SEND_ATTEMPTS, row_id),
        )


def _json(model: Conversation | OutboundMessage | Order) -> Jsonb:
    return Jsonb(model.model_dump(mode="json"))
