# ADR-0002: PostgreSQL as the persistence layer from day one

- Status: accepted (revised in PR #35 review: replaces the initial SQLite choice)
- Date: 2026-11-04
- Issue: #2

## Context
From the first version we need to persist conversation state, orders, inbound messages (for idempotency), an outbox for outgoing messages and LLM call metrics, surviving process restarts. The pilot targets 2–3 restaurants, but the project is expected to move to more robust infrastructure if it succeeds, and it must also stand as a portfolio piece.

## Decision
PostgreSQL 16 from the start, run locally with Docker Compose, accessed with **psycopg 3** (async) and plain, numbered SQL migrations applied at startup. No ORM.

Idempotency and concurrency rely on the database:
- `INSERT ... ON CONFLICT DO NOTHING` on `inbound_messages(channel, external_id)` to drop duplicate webhooks/retries.
- One turn = one transaction; `SELECT ... FOR UPDATE` on the conversation row serializes turns of the same conversation across processes.
- `orders.idempotency_key` and `outbox.dedupe_key` are `UNIQUE`.
- The outbox worker claims rows with `FOR UPDATE SKIP LOCKED`.
- `jsonb` for the cart snapshot and per-turn trace payloads.

Tests: unit and e2e tests use in-memory repository fakes; repository and migration tests run against a real PostgreSQL (Docker locally, a `services:` container in CI).

## Alternatives considered
- **SQLite**: zero infrastructure, but single writer, weaker concurrency and a guaranteed migration later. With Docker Compose already in the stack (Langfuse, ADR-0003), PostgreSQL adds little operational weight.
- **ORM (SQLAlchemy) + Alembic**: more abstraction and dependencies than the small data model justifies; can be revisited if the schema grows.
- **Cache / in-memory store (Redis or similar)**: will likely be needed later (e.g. hot session cache, rate limiting, short-lived dedupe windows at higher volume), but in this phase there is no workload that requires a cache and no use case for an in-memory database. Deferred until a concrete need appears.

## Consequences
- Positive: real concurrency control and row locking for idempotency; same database engine from development to production; widely known stack for a portfolio.
- Negative: development and tests depend on Docker; slightly more setup than SQLite.
- Revisit if: a concrete caching or rate-limiting need appears (add Redis), or the schema grows enough to justify an ORM.
