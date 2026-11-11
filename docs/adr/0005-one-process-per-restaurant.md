# ADR-0005: One process, one bot and one database per restaurant

- Status: accepted
- Date: 2026-11-10
- Issue: #18

## Context
M4 adds the first long-running process: a Telegram bot that takes customer messages, runs turns and delivers the outbox. ADR-0002 expects 2–3 restaurants in the pilot, and multi-tenant is out of scope. So far nothing says how several restaurants are deployed.

The code already assumes one restaurant per process. `ConversationService` takes one `RestaurantConfig`, and the CLI takes one `--restaurant`. The schema doesn't record which restaurant a row belongs to:
- `conversations` is keyed by `(channel, chat_ref)`. A Telegram chat id identifies the customer, not the bot. With a shared database, a customer writing to two restaurants' bots would get one conversation and one cart for both.
- `inbound_messages` is keyed by `(channel, external_id)`. Telegram's `update_id` is only unique per bot, so two bots sharing a database could drop each other's messages as duplicates.
- `outbox` has no restaurant either, so a delivery loop couldn't tell which bot sends a row.

## Decision
Each restaurant runs as its own process, with its own config, its own Telegram bot token and its own PostgreSQL database. Several databases can share one PostgreSQL server. Nothing in a database refers to another restaurant.

## Alternatives considered
- **Shared database with a `restaurant_id` column**, in every key and every query: the multi-tenant design that CLAUDE.md keeps out of scope. It touches every table, the repository contract and the tests, for a pilot of 2–3 restaurants. Worth doing only if the number of restaurants grows.
- **One process serving several restaurants, one database each**: saves processes, but needs routing from each bot to its config and repository, and one crash takes every restaurant down. Nothing requires it at this size.
- **Leave it unwritten**: the first person who points two restaurants at one `DATABASE_URL` gets mixed carts and lost messages, with no error.

## Consequences
- Positive: no schema or code change. Restaurants are isolated: a bad config or a crash affects one. Each database can be backed up, wiped or moved on its own.
- Negative: one process and one database to run per restaurant. Cross-restaurant reports need to read several databases.
- Risk: the rule is only written, not enforced. A process doesn't check that its database belongs to its restaurant. If misconfiguration becomes likely, store the restaurant id in the database at the first migration and refuse to start when it doesn't match.
- Revisit if the project goes beyond the pilot with many restaurants, which would mean planning multi-tenant.
