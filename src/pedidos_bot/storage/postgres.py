"""PostgreSQL repository (ADR-0002). One turn is one transaction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from pedidos_bot.channels.base import InboundMessage, OutboundMessage
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.storage.base import BufferedScope, OutboxEntry, TurnScope

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

    async def pending_outbound(self) -> list[OutboxEntry]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT chat_ref, message, dedupe_key FROM outbox WHERE sent_at IS NULL ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [OutboxEntry(r[0], OutboundMessage.model_validate(r[1]), r[2]) for r in rows]

    async def mark_sent(self, dedupe_key: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "UPDATE outbox SET sent_at = now() WHERE dedupe_key = %s", (dedupe_key,)
            )


def _json(model: Conversation | OutboundMessage) -> Jsonb:
    return Jsonb(model.model_dump(mode="json"))
