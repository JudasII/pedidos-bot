# Milestone M0 — Foundations

Date: 2026-11-04 · PR #35 · Issues #1, #2

## What was done
- New public repo `pedidos-bot` with uv + Python 3.12, ruff, strict mypy, pytest.
- Public-repo guards: gitleaks and a block on `data/private/` in pre-commit and CI; tests verifying that `.env`, real data, local databases and reports are ignored.
- CLAUDE.md, a three-phase ROADMAP, ADR-0001 (architecture), ADR-0002 (PostgreSQL), ADR-0003 (observability).
- Idempotent script that created 20 labels, 9 milestones and 34 issues with acceptance criteria.

## Decisions
- Architecture: LLM as action NLU + cart as state + deterministic policy, instead of a linear stage-based FSM (ADR-0001).
- Handoff relayed through the bot; fixed delivery fee + pickup; COP as integers.
- Changes after PR review:
  - English as the repo's working language; Spanish only for customer-facing text, prompts, configs and eval conversations.
  - PostgreSQL from day one instead of SQLite: we would migrate anyway, and Docker Compose is already needed for Langfuse (ADR-0002).
  - Redis deferred because there is no caching need yet, not because of durability (ADR-0002).
  - Langfuse instead of Arize Phoenix: heavier, but it grows with the project (datasets, prompt versions, scores) and is well known (ADR-0003).

## Rejected alternatives
- Linear FSM with per-stage prompts: loses data in multi-field messages.
- LLM agent with tools: not viable in latency/determinism with a 4B model.
- SQLite and Phoenix: see the review changes above.

## Problems
- The gitleaks hook pinned at v8.21.2 did not detect a fake Telegram token that v8.30.1 does: the version was bumped and blocking was verified.
- A fake token shaped like an Anthropic API key was not detected by either version; we do not assume coverage for that format.

## Real metrics
- 11 tests, 0 failures; CI green (2 checks). 34 issues planned across 9 milestones.

## Learnings
- Verify guards with a positive case (a fake secret) instead of assuming the hook works.
- Write decisions down early: the review changed three of them before any code depended on them.
