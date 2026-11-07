# Milestone M3 — Persistence and idempotency

Date: 2026-11-06 · Branch `m3-persistence` · Issues #13, #14, #15, #16, #17, #41

## What was done
- **PostgreSQL (#13):** `PostgresRepository`, plain numbered SQL migrations applied on open under an advisory lock, `docker compose up db`, and a `postgres` pytest marker with a Postgres service in CI. One turn is one transaction with the conversation row locked (`SELECT ... FOR UPDATE`).
- **Idempotency (#14):** repeated `external_id`s are dropped with `ON CONFLICT DO NOTHING`. `create_order` is the single entry point from ADR-0004, and works without a conversation.
- **Outbox (#15):** exponential retries (2 s doubling to 5 min, 8 attempts), workers claim rows with `FOR UPDATE SKIP LOCKED`, and a crash during a send makes the message due again.
- **Expiry (#16):** inside the silent window the conversation continues; later the bot asks first; after a day, or if the menu changed, a new conversation offers the old cart. `revalidate` reprices it from the current menu and reports what is gone. Thresholds come from `session` in the config.
- **Logging (#17):** one JSON line per turn with conversation, turn and order IDs, state before and after, actions, notices and latency. Phones and addresses are masked, and customers are logged as a salted HMAC.
- **Address question (#41):** from the M2 review. After choosing pickup, an address makes the bot ask, showing the fee.

## Decisions
- **Only tables something writes to.** #13 lists nine. `llm_calls`, `handoffs`, `turns` and `order_items` wait for the milestones that fill them. `conversation_state` is folded into `conversations`, one row per chat, with the state as `jsonb`. Each order session has its own `Conversation.id`.
- **One contract suite for both repositories.** The in-memory fake can't drift from PostgreSQL without a test failing.
- **One order per conversation.** The conversation id is the idempotency key, so a repeated "sí" can't duplicate an order and a change after confirming revises the same row.
- **`withdrawn` status.** If the customer cancels or changes an order while staff decide, staff must not act on it. Confirming again restores it.
- **Expiry is checked on the next message.** No background job: nothing needs it until a customer returns. The `EXPIRED` lifecycle state is therefore still unused.
- **The waiting message is kept.** If the bot must ask "¿seguimos?", what the customer said is handled after "sí". A bare yes or no isn't replayed, since it answered a question from hours ago.
- **A cart keeps the menu version it was priced with** until it is revalidated.
- **Masking doesn't rely only on patterns.** Addresses have no fixed form, so a turn that sets an address, or answers the address question, is masked whole. Failures log only the exception type, because validation errors can quote customer text.
- **Any exception from a send counts as a failed attempt,** so a bad message can't block the rest.

## Rejected alternatives
- Version-based optimistic locking from the original plan: `FOR UPDATE` is simpler and ADR-0002 already chose it.
- A background expiry worker: more moving parts for no customer-visible difference yet.

## Problems
- **Test fixture nearly pointed at another project's database.** `localhost:5432` belonged to a running container of a different project, and the fixture wipes the schema. The port conflict stopped it before any test ran. The project now uses port 5433, tests use `pedidos_test`, and the fixture refuses any database not ending in `_test`.
- `docker compose` hung pulling `postgres:16-alpine`; `postgres:16` was already local.
- The self-review found 10 issues, all fixed. The worst: an order cancelled while staff decided stayed pending, and a tapped "Sí" button lost the waiting message because the button payload wins over the text.

## Real metrics
- 526 tests by default plus 19 against PostgreSQL. Memory and PostgreSQL run the same contract tests.
- No load testing: the connection limit and lock duration are unmeasured.

## Known limitations
- Nothing runs the worker or opens PostgreSQL in the app yet: there is no app entry point until M4. `pedidos-bot chat` still uses the in-memory repository.
- A turn holds a connection and a row lock during its LLM call (ADR-0002). `max_connections` defaults to 20; measure in M5.
- `inbound_messages` grows forever; it will need cleanup before long pilots.
- A retried message can arrive after a newer one from the same chat.
- `sent_at` must be timezone-aware; the channel adapters must guarantee it.
- M4 must tell staff when an order is withdrawn.

## Learnings
- A destructive test fixture needs its own guard, not just the right default.
- Running the same tests against the fake and the real database made the fake trustworthy.
- For the third milestone in a row, the self-review found real bugs in code with green tests.
