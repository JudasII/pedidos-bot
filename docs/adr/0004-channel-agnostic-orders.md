# ADR-0004: Channel-agnostic orders; web ordering link deferred

- Status: accepted
- Date: 2026-11-06
- Issue: #39

## Context
The first message could offer a web link where customers build the order themselves, instead of chatting. That flow would need no LLM, can't be misread, and could feed a future POS. Orders from any of these sources should reach the restaurant the same way.

The cart already allows this: its operations are pure, take product and option IDs, and price from the config. What doesn't exist yet is the order record (#13) or the restaurant notification (#19), so their shape is still cheap to choose.

The project's main question is whether conversational ordering works. A link in the first message could send customers away from the chat before the pilot has measured it. Web ordering and POS are outside the current scope, and a customer-facing page adds a frontend, a public API and abuse protection.

## Decision
1. **Orders don't depend on the chat.** One entry point creates orders from a validated cart. `orders.source` records the origin (`chat` for now) and the link to a conversation is optional. The restaurant notification is built from the order alone.
2. **The web ordering link is deferred.** It stays out of scope until pilot data supports it (#39). POS stays out of scope too.

If it is built, these constraints apply:
- A signed, expiring link per chat, so the order is confirmed in that chat and handoff still works.
- The server recomputes prices and totals from IDs and ignores amounts sent by the client.
- Web orders go through the same entry point, with `source=web`, and shadow mode applies to them.
- Prefer offering the link when a customer gets stuck, with the cart filled in, over offering it in the first message.

## Alternatives considered
- **Build the link now:** gives customers an unambiguous path, but delays M3–M6 and splits the traffic the pilot needs to evaluate chat.
- **Defer everything:** no work today, but orders and notifications shaped around conversations would need a migration later.
- **Reduce chat load another way:** not decided here. Chat load is unmeasured until M5, so load alone doesn't justify a second channel yet.

## Consequences
- Positive: chat, web and a POS can share one order path, and adding a source later needs no schema change.
- Negative: M3 and M4 carry a small abstraction with only one source for now.
- Risk: without the link, customers who dislike chatting have no alternative during the pilot; handoff is their fallback.
- Revisit after the pilot, or sooner if a POS is planned or measured chat cost per order is too high.
