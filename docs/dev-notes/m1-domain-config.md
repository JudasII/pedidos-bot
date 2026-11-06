# Milestone M1 — Domain and configuration

Date: 2026-11-05 · Branch `m1-domain-config` · Issues #3, #4, #5, #6

## What was done
- **Config schema (#3):** pydantic models with `extra=forbid` for restaurant info, staff-chat env var, fulfillment (fixed delivery fee / pickup), payment methods, shadow mode, session policy, tone, menu and rules (`upsell`, `max_qty_per_line`, `age_notice`). Cross-reference validation (unique ids, modifier groups, rule categories). `ConfigError` messages list field path and reason. JSON Schema exported to `docs/config.schema.json` (a test keeps it in sync; configs reference it for editor autocomplete). CLI: `pedidos-bot config validate|schema`.
- **Sample configs (#4):** `burger_demo` (15 products: 3 burgers, tenders, onion flower, onion rings, fries, milkshake, 4 drinks, 3 beers) and `minimal_demo` (3 products, pickup only, cash only, formal tone, no shadow mode).
- **Domain (#5):** an immutable `Cart` with pure operations. Identical items are merged; a new `choose_one` choice replaces the old one; prices are frozen from the config; there are per-group selection limits and a per-line quantity limit. `totals()` computes the totals and `pending()` returns the missing slots in the order the bot should ask for them, ready for the M2 policy. `format_cop` → `$18.000`.
- **Menu resolver (#6):** customer wording → product / modifier / payment ids. It tries an exact alias first, then the longest alias contained in the text, then fuzzy matching with a threshold and a minimum gap to the runner-up. `resolve_item` then checks the whole phrase, because one alias can look right while the rest of the phrase says otherwise. CLI: `pedidos-bot resolve`.

## Decisions
- The resolver only says RESOLVED when it is unambiguous. A near tie or a low score returns AMBIGUOUS with candidates, so the bot asks; unknown text returns NOT_FOUND.
- **Negation guard:** "cebolla" alone can never resolve to "sin cebolla"; at most it becomes a clarification candidate. Confusing "with" and "without" is the worst error for the cashier.
- **Category aliases** ("cerveza", "gaseosa", "hamburguesa") list the category's products instead of resolving to one. A category hit does not block a specific fuzzy match: "hamburguesa espesial" still resolves to the especial.
- **Modifiers from leftover words** ("malteada de fresa" → product + flavor). This makes the 4B model's job easier: it doesn't have to split product and modifiers perfectly.
- **Whole-phrase check:** a phrase is only RESOLVED when one reading explains its words. If leftover words name another product, the other reading wins when it explains everything ("papas sencillas" → fries, personal size); otherwise the result is AMBIGUOUS with both products ("sencilla con coca"). Modifiers follow their product, so "sencillas con papas" never becomes small fries.
- **Modifiers survive a clarification:** an AMBIGUOUS item keeps its modifiers, resolved against the first candidate. M2 must resolve each `query` again once the product is chosen.
- If a restaurant offers only one fulfillment mode, that mode is implied and the bot never asks for it.
- Identical lines are merged, but "2 sencillas, una sin cebolla" stays as two lines because the modifiers differ.

## Rejected alternatives
- Regex as the matcher for items and modifiers: raised in the PR #36 review and evaluated only after M1 was built. On 16 typos, the fuzzy pass resolved 15 and a hand-tuned regex generated from the same aliases (repeated letters, s/c/z and b/v swaps) resolved 10; it misses dropped or swapped letters such as "malteda" and "tendrs". Small sample chosen by hand. The fuzzy miss, "cocacolla", returns AMBIGUOUS, so the bot asks.
- Embeddings / semantic search for the resolver: unnecessary for 15–40 items, harder to debug and not deterministic enough to promise "never guesses".
- Using `partial_ratio` with no limits: it would make every short word match everything. Partial matching only applies when the query covers at least half of the alias.

## Problems
- The first probe showed four failure modes: a typo inside a longer phrase ("malteda de chocolate" → not found), typo words wrongly left over as modifiers ("chiken"), "cebolla" resolving to "sin cebolla", and the category alias shadowing a typo of a specific product. Each one now has a fix and a test.
- The PR #36 review found that "never guesses" was false for phrases. The containment pass took the longest alias and ignored what the leftover words meant: "papas sencillas" became a burger, "sencilla con coca" dropped the drink, "hamburguesa con doble carne" became the double. The self-review then found the same fault in modifiers ("doble sin tomate tocineta" lost the paid extra), plus "agua con gas" (still water), "coca 2 litros" (1.5 L) and "cebolla" alone (onion rings).
- The first version of the fix added a new guess: "2 sencillas con papas" became fries. Probing realistic phrases caught it before any review did.
- `ruff --fix` rewrote a `str.split()` constant into a long list, which then needed formatting: run `ruff format` after `ruff check --fix`.

## Real metrics
- 172 tests, 0 failures (102 of them for the resolver).
- Resolver on the original 47-phrase probe, measured before the whole-phrase check and not re-run since: all typo, slang and plural cases resolved. That probe had few multi-word phrases, which is how the bugs above got through.

## Known limitations
- "la de la casa sin tomate" returns AMBIGUOUS, because an alias that starts with a filler word is not found by containment. The bot asks, so the order stays correct.
- Two products in one item are reported as AMBIGUOUS, the same status as "which one?". The NLU is expected to split them; M2 may need to tell the two cases apart for the wording of the question.
- `resolve_product` on its own ignores leftover words. Orders must go through `resolve_item`.

## Learnings
- Probing with realistic phrases before writing assertions found more bugs than the unit tests I had planned would have. The probe has to include multi-word phrases: single items passed while real orders failed.
- A strong claim like "never guesses" needs tests that try to break it, not only tests of the cases it was built for.
- The "no restaurant data in src/" test caught example product names inside docstrings. Even comments have to be generic.
