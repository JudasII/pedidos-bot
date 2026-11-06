# Roadmap

Detailed tracking in the [GitHub milestones and issues](https://github.com/JudasII/pedidos-bot/milestones).

## Phase 1 — Development (current)

| Milestone | Goal | Status |
|---|---|---|
| M0 - Foundations | Repo, tooling, CI, conventions, base ADRs | done |
| M1 - Domain and configuration | Validated YAML schema, 2 sample restaurants, cart and totals, menu resolver | done |
| M2 - Engine with simulated LLM | Action contract, shortcuts, policy, FSM, templates, REPL; deterministic e2e tests | pending |
| M3 - Persistence and idempotency | PostgreSQL, dedupe, outbox, expiry/resumption, safe restarts, logging with IDs | pending |
| M4 - Telegram, restaurant, handoff, shadow mode | Telegram adapter, cashier notification, human relay, shadow mode | pending |
| M5 - Real LLM and observability | Ollama + Qwen3 4B, OpenTelemetry + Langfuse, cost and tokens per order | pending |
| M6 - Evaluation | Conversation bank, local vs hosted comparison, model ADR | pending |

## Phase 2 — Pilot with WhatsApp and a hosted model

- WhatsApp Cloud API adapter (webhooks, templates, 24-hour window, interactive messages).
- Deployment with PostgreSQL, backups and Langfuse; add a cache (Redis) only when a concrete need appears.
- Shadow-mode pilot with 1 restaurant; autonomous mode if metrics allow.

## Phase 3 — Monetization decision

- Measure attention workload saved and order errors during the pilot.
- Define criteria to continue / charge / archive as a portfolio piece.
