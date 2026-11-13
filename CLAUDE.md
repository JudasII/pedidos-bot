# CLAUDE.md — pedidos-bot

Conversational assistant engine that takes burger-restaurant orders over chat. Side project: validate within months, otherwise it becomes a portfolio piece. **Public** repo.

## Language
- **English is the repo's working language**: code, comments, docstrings, tests, docs, ADRs, dev notes, issues, PRs and commit messages.
- **Spanish (Colombian, informal) only where the product needs it**: customer-facing templates, LLM prompts and few-shots, restaurant configs (`config/restaurants/*.yaml`), evaluation conversations, and test fixtures that contain customer messages.
- LinkedIn drafts are written in both Spanish and English (`docs/linkedin/mX.es.md`, `mX.en.md`).
- Talk to the user in the language they use.

## Scope (do not change without asking the user)
- In: conversation and order engine, Telegram channel for development, LLM behind an interface, restaurant notification via Telegram, payment-method recording, human handoff (relay through the bot), shadow mode, persistence/idempotency, evaluation, observability.
- Out: real WhatsApp adapter (only design the interface with it in mind), payment gateways, POS, CRM, web dashboard, customer web ordering (deferred, ADR-0004), multi-tenant, other verticals, advanced context summarization.
- Never hardcode a restaurant in `src/`. Everything specific goes in `config/restaurants/*.yaml`. A test (`tests/unit/config/test_sample_configs.py`) enforces this, including docstrings and comments: use generic examples (e.g. "pizza") in `src/`.
- If something is ambiguous, ask the user instead of assuming.

## Architecture (ADR-0001)
`normalizer → deterministic shortcuts → NLU (LLM → JSON actions) → menu resolver → apply to cart → policy + lifecycle FSM → templates → outbox`.
- The LLM **only** emits typed actions that reference the customer's own words. Never IDs or prices.
- Resolver: config aliases + fuzzy matching; ambiguity ⇒ clarifying question, never guess.
- The order "stage" is derived from missing slots; the FSM only models the lifecycle (ORDERING, AWAITING_CONFIRMATION, PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED).
- Template responses; LLM paraphrasing behind a flag, off by default.

## Decisions agreed with the user
- Handoff: relay through the bot (cashier replies from the restaurant chat; `/devolver` or timeout returns control).
- Total = items + fixed delivery fee per restaurant; pickup option. No zones or minimum order (for now).
- Currency COP as integers, displayed as `$18.000`. Fixed ETA per fulfillment mode in config.
- Persistence: **PostgreSQL from day one** (ADR-0002), psycopg 3, plain SQL migrations. No cache (Redis) yet: not needed in this phase.
- Observability: OpenTelemetry instrumentation exported to **self-hosted Langfuse** (ADR-0003).
- Orders don't depend on the chat: one entry point, `orders.source`, optional conversation link, notification built from the order alone (ADR-0004).
- One process, one Telegram bot and one PostgreSQL database per restaurant; the schema has no restaurant column, so never share a database (ADR-0005).

## Code map (as of M4, phase A)
- `domain/menu.py` menu models · `domain/cart.py` immutable cart + pure ops, `totals()`, `pending()` · `domain/money.py` `format_cop`.
- `config/schema.py` `RestaurantConfig` · `config/loader.py` `load_restaurant`, `ConfigError`, `resolve_staff_chat_id`, JSON Schema export (`docs/config.schema.json`, kept in sync by a test).
- `engine/resolver.py` `MenuResolver`: exact → containment → fuzzy; returns RESOLVED / AMBIGUOUS / NOT_FOUND, never guesses. Orders go through `resolve_item`, which checks the whole phrase; `resolve_product` alone ignores leftover words.
- `engine/actions.py` typed actions (schema in `docs/actions.schema.json`, kept in sync by a test) · `engine/nlu.py` `Nlu` · `llm/base.py` `LLMProvider` · `llm/fake.py` `ScriptedLLM`, `RuleBasedLLM`.
- `engine/shortcuts.py` `detect` · `engine/lifecycle.py` `Lifecycle`, `TRANSITIONS` · `engine/policy.py` `next_step` · `engine/templates.py` `Msg`, per-tone text · `engine/responder.py` `Responder`.
- `engine/conversation.py` `Conversation` (state between turns) · `engine/turn.py` `Turn` (logic of one turn, no I/O) · `engine/service.py` `ConversationService.handle`.
- `channels/base.py` message models, `Channel`, `degrade` · `channels/memory.py` `InMemoryChannel`, `WHATSAPP_LIKE` · `channels/terminal.py` `TerminalChannel` · `channels/telegram.py` `TelegramChannel` (long polling, customers only), `to_inbound`.
- `app.py` the composition root: `build_service`, `serve`, `run_telegram`, `run_chat` (REPL) · `settings.py` `Settings` (from `.env`) · `cli.py` commands, including `run`.
- `storage/base.py` `ConversationRepository` (owns the turn boundary), `deliver_pending` · `storage/memory.py` `InMemoryRepository` · `storage/postgres.py` `PostgresRepository`, `migrate` · `storage/migrations/*.sql`.
- `domain/order.py` `create_order`, the one entry point for orders (ADR-0004) · `domain/cart.py` `revalidate` · `observability/masking.py` `mask_text`, `hash_customer` · `observability/logging.py` `configure_logging`.
- `chats/whatsapp.py` `parse`: reads WhatsApp "Export chat" files · `chats/transcripts.py` `import_exports`, `mask`: raw exports and masked transcripts under `data/private/chats/` (`pedidos-bot chats import`). Tests use invented chats only.
- `tests/integration/test_repository.py` one contract suite for both repositories. Run PostgreSQL tests with `uv run pytest -m postgres`; the fixture only wipes databases ending in `_test`.
- `tests/e2e/harness.py` `Chat`: a customer talking to the real engine.
- `tests/factories.py` `make_config()` small generic config for unit tests.
- `tests/telegram_fake.py` `FakeTelegram`: a fake Bot API behind httpx's mock transport.
- After `ruff check --fix`, run `ruff format` again.

## Conventions
- Python 3.12, `uv`.
- Quality gate before every commit: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest && uv run pytest -m postgres`.
- Tests: unit in `tests/unit`, end-to-end conversations with `FakeLLM` in `tests/e2e`, integration with real services marked `@pytest.mark.postgres` / `@pytest.mark.ollama` / `@pytest.mark.hosted` (skipped by default; run with `uv run pytest -m postgres`).
- Commits: Conventional Commits in English referencing an issue, e.g. `feat(engine): menu resolver with aliases (#12)`.
- One branch per milestone (`m1-domain-config`), PR to `main` that closes the issues.
- Relevant decisions ⇒ ADR in `docs/adr/NNNN-title.md` (template: `0000-template.md`).
- When closing each milestone: `docs/dev-notes/mX-<slug>.md` and LinkedIn drafts `docs/linkedin/mX.es.md` + `mX.en.md` (150–250 words, first person, real metrics only, suggested visual, few hashtags). Not every milestone needs a standalone post; small ones can be folded into a later post. **Never publish anything**: the user reviews and publishes.

## Security and data
- Public repo: secrets only in `.env` (ignored); `.env.example` has no values.
- Real chats only under `data/private/` (ignored). Never copy them into tests, fixtures, docs or commits. That includes the masked transcripts: masking is best effort.
- Logs and traces mask phone numbers and addresses; customer identifiers are HMAC'd with `PII_HASH_SALT`.
- gitleaks runs in pre-commit and CI.

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
