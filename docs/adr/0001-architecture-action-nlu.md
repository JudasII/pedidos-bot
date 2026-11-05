# ADR-0001: LLM as action NLU + cart as state + deterministic policy

- Status: accepted
- Date: 2026-11-04
- Issue: #2

## Context
The bot must take orders in informal Colombian Spanish with a local 4B-parameter model (Qwen3:4b via Ollama) and, later, possibly a hosted model. Customers send several pieces of data in one message ("2 sencillas, una sin cebolla, una coca grande, a la calle 10, pago en efectivo"), correct themselves mid-order and cancel. The LLM must never invent products or prices.

The initial proposal was a linear, stage-based state machine (greeting → items → anything else? → address → payment → confirmation) with the LLM extracting data within each step.

## Decision
1. On every turn the LLM receives the full message and the menu, and returns a **list of typed actions** (JSON with a schema): `add_item`, `remove_item`, `update_item`, `set_fulfillment`, `set_address`, `set_payment`, `confirm`, `deny`, `cancel_order`, `ask_human`, `ask_question`, `greeting`, `unknown`. Products are referenced by the customer's own words.
2. A **deterministic resolver** (config aliases + fuzzy matching) maps text to menu IDs; on ambiguity, it asks.
3. The **cart** is the state; the "stage" is derived from what is missing. A deterministic **policy** decides the next question.
4. A **small FSM** models only the lifecycle: ORDERING, AWAITING_CONFIRMATION, PENDING_STAFF_CONFIRMATION, CONFIRMED, CANCELLED, HANDOFF, EXPIRED.
5. **Deterministic shortcuts** before the LLM (confirmations, cancel, ask for a person, payment methods, non-text messages).
6. **Template responses** using the config's tone; LLM paraphrasing behind a flag, off by default.

## Alternatives considered
- **Linear FSM + per-stage LLM**: short prompts, but loses data from multi-field messages and forces duplicating correction logic in every state.
- **LLM agent with tools**: more natural conversation, but latency, cost and non-determinism are not viable with a local 4B model and are hard to test.

## Consequences
- Positive: multi-field messages and corrections need no special code; a single LLM contract that can be evaluated and compared across models; deterministic end-to-end tests with a simulated LLM.
- Negative: the prompt includes the full menu (acceptable for 20–40 item menus); the action schema is demanding for a 4B model.
- Risks: invalid JSON or misassigned actions from the 4B model ("una sin cebolla" applied to both burgers). Mitigation: Ollama structured outputs, `think=false`, targeted few-shots, one retry, always confirm with a summary, dedicated evaluation metrics.
- Revisit if: menus grow to hundreds of items (would need candidate retrieval) or evaluation shows the 4B model cannot reach minimum accuracy.
