# ADR-0003: OpenTelemetry instrumentation with self-hosted Langfuse

- Status: accepted (revised in PR #35 review: replaces the initial Arize Phoenix proposal)
- Date: 2026-11-04
- Issue: #2

## Context
Every turn must be traceable (message, state before/after, prompt, raw response, parsed output, validations, latency, errors) and every LLM call must record model, input/output tokens, latency and estimated cost, aggregated per turn, order and conversation. The tool must be self-hostable and free. The project will grow through several milestones (evaluation, model comparison, pilot) and should also work as a portfolio piece.

## Decision
- Instrument with **OpenTelemetry** (spans per turn and per LLM call, GenAI semantic-convention attributes) plus structured JSON logs (structlog) carrying `conversation_id`, `turn_id`, `order_id`.
- Export traces to **self-hosted Langfuse** (Docker Compose) via its OTLP endpoint. Use Langfuse for trace exploration, prompt versioning, scores and datasets for evaluation runs.
- The database (`llm_calls`) remains the source of truth for cost reports; Langfuse is the exploration and evaluation UI.
- PII masking (phones, addresses, hashed customer IDs) happens in-process before anything is logged or exported.

## Alternatives considered
- **Arize Phoenix**: lighter (single container), but less complete for prompt management, datasets and long-term use.
- **Grafana LGTM (Tempo + Loki + Prometheus)**: great general observability, but not LLM-aware (no prompt/generation views, no evaluation datasets).
- **Langfuse Cloud (hobby tier)**: no infrastructure, but sends traces to a third party; kept only as a fallback for quick experiments with synthetic data.

## Consequences
- Positive: an LLM-native tool that scales with the project (evaluation datasets, prompt versions, scores); popular and well documented; OpenTelemetry keeps the backend swappable.
- Negative: the self-hosted stack is heavier (Langfuse web + worker, PostgreSQL, ClickHouse, Redis, object storage). Mitigation: run it only when needed via a separate compose profile; tests never depend on it.
- Revisit if: resource usage on the development machine or pilot host becomes a problem.
