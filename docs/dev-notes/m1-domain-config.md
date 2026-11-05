# Milestone M1 — Domain and configuration

Date: 2026-11-05 · Branch `m1-domain-config` · Issues #3, #4, #5, #6

## What was done
- **Config schema (#3):** pydantic models with `extra=forbid` for restaurant info, staff-chat env var, fulfillment (fixed delivery fee / pickup), payment methods, shadow mode, session policy, tone, menu and rules (`upsell`, `max_qty_per_line`, `age_notice`). Cross-reference validation (unique ids, modifier groups, rule categories). `ConfigError` messages list field path and reason. JSON Schema exported to `docs/config.schema.json` (a test keeps it in sync; configs reference it for editor autocomplete). CLI: `pedidos-bot config validate|schema`.
- **Sample configs (#4):** `burger_demo` (15 products: 3 burgers, tenders, onion flower, onion rings, fries, milkshake, 4 drinks, 3 beers) and `minimal_demo` (3 products, pickup only, cash only, formal tone, no shadow mode).
- **Domain (#5):** an immutable `Cart` with pure operations. Identical items are merged; a new `choose_one` choice replaces the old one; prices are frozen from the config; there are per-group selection limits and a per-line quantity limit. `totals()` computes the totals and `pending()` returns the missing slots in the order the bot should ask for them, ready for the M2 policy. `format_cop` → `$18.000`.
- **Menu resolver (#6):** customer wording → product / modifier / payment ids. It tries an exact alias first, then the longest alias contained in the text, then fuzzy matching with a threshold and a minimum gap to the runner-up. It never guesses. CLI: `pedidos-bot resolve`.

## Decisions
- The resolver only says RESOLVED when it is unambiguous. A near tie or a low score returns AMBIGUOUS with candidates, so the bot asks; unknown text returns NOT_FOUND.
- **Negation guard:** "cebolla" alone can never resolve to "sin cebolla"; at most it becomes a clarification candidate. Confusing "with" and "without" is the worst error for the cashier.
- **Category aliases** ("cerveza", "gaseosa", "hamburguesa") list the category's products instead of resolving to one. A category hit does not block a specific fuzzy match: "hamburguesa espesial" still resolves to the especial.
- **Modifiers from leftover words** ("malteada de fresa" → product + flavor). This makes the 4B model's job easier: it doesn't have to split product and modifiers perfectly.
- If a restaurant offers only one fulfillment mode, that mode is implied and the bot never asks for it.
- Identical lines are merged, but "2 sencillas, una sin cebolla" stays as two lines because the modifiers differ.

## Rejected alternatives
- Embeddings / semantic search for the resolver: unnecessary for 15–40 items, harder to debug and not deterministic enough to promise "never guesses".
- Using `partial_ratio` with no limits: it would make every short word match everything. Partial matching only applies when the query covers at least half of the alias.

## Problems
- The first probe showed four failure modes: a typo inside a longer phrase ("malteda de chocolate" → not found), typo words wrongly left over as modifiers ("chiken"), "cebolla" resolving to "sin cebolla", and the category alias shadowing a typo of a specific product. Each one now has a fix and a test.
- `ruff --fix` rewrote a `str.split()` constant into a long list, which then needed formatting: run `ruff format` after `ruff check --fix`.

## Real metrics
- 133 tests, 0 failures (67 of them for the resolver). 25 files changed.
- Resolver on a 47-phrase probe against burger_demo: all typo, slang and plural cases resolve. The only AMBIGUOUS results are the intended ones (generic "hamburguesa", "cerveza", "gaseosa", "cebolla"), and "pizza", "perro caliente" and "queso" return NOT_FOUND.

## Learnings
- Probing with realistic phrases before writing assertions found more bugs than the unit tests I had planned would have.
- The "no restaurant data in src/" test caught example product names inside docstrings. Even comments have to be generic.
