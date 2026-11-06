# CLAUDE.md — pedidos-bot

Conversational assistant engine that takes burger-restaurant orders over chat. Side project: validate within months, otherwise it becomes a portfolio piece. **Public** repo.

## Language
- **English is the repo's working language**: code, comments, docstrings, tests, docs, ADRs, dev notes, issues, PRs and commit messages.
- **Spanish (Colombian, informal) only where the product needs it**: customer-facing templates, LLM prompts and few-shots, restaurant configs (`config/restaurants/*.yaml`), evaluation conversations, and test fixtures that contain customer messages.
- LinkedIn drafts are written in both Spanish and English (`docs/linkedin/mX.es.md`, `mX.en.md`).
- Talk to the user in the language they use.

## Scope (do not change without asking the user)
- In: conversation and order engine, Telegram channel for development, LLM behind an interface, restaurant notification via Telegram, payment-method recording, human handoff (relay through the bot), shadow mode, persistence/idempotency, evaluation, observability.
- Out: real WhatsApp adapter (only design the interface with it in mind), payment gateways, POS, CRM, web dashboard, multi-tenant, other verticals, advanced context summarization.
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

## Code map (as of M1)
- `domain/menu.py` menu models · `domain/cart.py` immutable cart + pure ops, `totals()`, `pending()` · `domain/money.py` `format_cop`.
- `config/schema.py` `RestaurantConfig` · `config/loader.py` `load_restaurant`, `ConfigError`, `resolve_staff_chat_id`, JSON Schema export (`docs/config.schema.json`, kept in sync by a test).
- `engine/resolver.py` `MenuResolver`: exact → containment → fuzzy; returns RESOLVED / AMBIGUOUS / NOT_FOUND, never guesses. Orders go through `resolve_item`, which checks the whole phrase; `resolve_product` alone ignores leftover words.
- `tests/factories.py` `make_config()` small generic config for unit tests.
- After `ruff check --fix`, run `ruff format` again.

## Conventions
- Python 3.12, `uv`.
- Quality gate before every commit: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`.
- Tests: unit in `tests/unit`, end-to-end conversations with `FakeLLM` in `tests/e2e`, integration with real services marked `@pytest.mark.ollama` / `@pytest.mark.hosted` (skipped by default; run with `uv run pytest -m ollama`).
- Commits: Conventional Commits in English referencing an issue, e.g. `feat(engine): menu resolver with aliases (#12)`.
- One branch per milestone (`m1-domain-config`), PR to `main` that closes the issues.
- Relevant decisions ⇒ ADR in `docs/adr/NNNN-title.md` (template: `0000-template.md`).
- When closing each milestone: `docs/dev-notes/mX-<slug>.md` and LinkedIn drafts `docs/linkedin/mX.es.md` + `mX.en.md` (150–250 words, first person, real metrics only, suggested visual, few hashtags). Not every milestone needs a standalone post; small ones can be folded into a later post. **Never publish anything**: the user reviews and publishes.

## Security and data
- Public repo: secrets only in `.env` (ignored); `.env.example` has no values.
- Real chats only under `data/private/` (ignored). Never copy them into tests, fixtures, docs or commits.
- Logs and traces mask phone numbers and addresses; customer identifiers are HMAC'd with `PII_HASH_SALT`.
- gitleaks runs in pre-commit and CI.
