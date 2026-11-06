# Milestone M2 — Engine with simulated LLM

Date: 2026-11-06 · Branch `m2-engine-fake-llm` · Issues #7, #8, #9, #10, #11, #12

## What was done
- **NLU contract (#7):** 13 typed actions in pydantic that carry the customer's words, never IDs or prices. JSON Schema in `docs/actions.schema.json`, kept in sync by a test (`pedidos-bot nlu schema`). `LLMProvider` returns token usage and latency. `ScriptedLLM` replays fixed answers; `RuleBasedLLM` parses common phrasing. A bad answer is retried once, then the turn becomes `Unknown`.
- **Shortcuts (#8):** whole-message matches for yes, no, cancel, asking for a person, fulfillment and payment methods, tolerant of stretched letters ("siiii"). Non-text messages go to a person. `TurnResult.resolved_without_llm` is the metric.
- **Channels (#11):** `InboundMessage`, `OutboundMessage`, `ChannelCapabilities`, `Channel`, shaped by a WhatsApp-like channel. Buttons that don't fit become a numbered list, and a typed number maps back to the option. `pedidos-bot chat --restaurant burger_demo` runs the engine in the terminal.
- **Policy and FSM (#9):** every legal move is a row of one table; the tests cover all 70 state and event pairs. `next_step` derives the next question from the cart.
- **Templates (#10):** informal and formal Colombian Spanish, emoji level from the config, summary with lines, modifiers, total and ETA.
- **Service:** `ConversationService.handle` runs answers to the pending question, then shortcuts, then the NLU, inside one atomic turn. `Turn` holds the logic and does no I/O.
- **E2E (#12):** generic scenarios run against both configs.

## Decisions
- **The repository owns the turn boundary.** `ConversationRepository.turn(inbound)` drops duplicates, serializes a conversation's turns and commits state and outbound messages together. The in-memory version has the same guarantees, so M3 only adds PostgreSQL.
- **Outbound messages always go through the outbox,** even in the REPL.
- **Answers to the pending question are read before anything else,** without the LLM. An answer only counts if it explains the whole message; otherwise it goes on to the NLU, so nothing the customer said is lost.
- **An item with an unclear modifier stays out of the cart until the customer answers.** Added earlier, it could merge with an identical line and the answer would change units they never mentioned.
- **"Sí" and "listo" differ at "¿Algo más?":** "sí" means there is more, "listo" means done. At the confirmation both confirm.
- **Giving fulfillment, address or payment ends the item list,** so a multi-field message reaches the summary in one turn.
- **An address implies delivery,** even after choosing pickup. The summary shows the fee before confirming.
- **A confirmed order can't be cancelled by the bot.** The customer is told it is confirmed and handed to a person.
- **Only "didn't understand" escalates.** Asking for something off the menu gets an answer and doesn't count.
- A customer change during `PENDING_STAFF_CONFIRMATION` returns the order to ORDERING. M4 must tell the staff.

## Rejected alternatives
- Adding a line first and fixing it when the clarification is answered: this caused the merge bug above.
- Stage-based handling of answers: the cart stays the only state, and `asked` only records how to read a short reply.

## Problems
- The self-review found 10 candidates and 9 were real, including a false "cancelled" on a confirmed order, the merge bug, two crashes on odd input, and answers losing the rest of the message ("fresa y unas papas"), the same fault as in M1. The tenth, a crash removing an unavailable product, can't happen: the resolver never matches one.
- Running the REPL by hand found what the tests didn't: added items joined with "o", and the greeting repeated mid-order.
- Some first-draft tests could not fail (`or True`, `if False`) and had to be rewritten.
- A scripted commit landed on the wrong branch because a failed `git checkout` didn't stop the commands after it. Moved before pushing.

## Real metrics
- 435 tests, 0 failures (68 end-to-end cases). About 3,500 lines in `src/`.
- 5 of 9 turns in a full delivery order were resolved without the LLM. One scripted conversation, not a benchmark.
- No real LLM yet, so no accuracy, latency or cost figures.

## Known limitations
- `RuleBasedLLM` is a development stand-in, far weaker than a real model.
- Nothing wired yet: `age_notice`, closing hours as a restriction, resumption and expiry (#16), staff decisions and handoff relay (M4). Attachments are not stored for M4 yet.
- A cart holding a product that later became unavailable can't be edited by name. #16 must revalidate carts on resumption.
- `deliver_pending` stops at the first failed send, so one closed window would block the rest. #15 must handle this per message.
- The LLM call runs inside the turn, as ADR-0002 defines, so M3 will hold a row lock for its full latency. Worth measuring in M5.
- `system_prompt` is a placeholder until M5.

## Learnings
- Writing to a repository protocol from the start kept persistence out of the engine.
- The bugs that matter live in the spaces between turns: what a short answer means depends on what was asked.
- Self-reviewing my own code found serious bugs twice in a row. It stays in the routine.
