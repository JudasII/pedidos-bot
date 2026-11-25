# ADR-0006: A web panel for the restaurant's staff

- Status: proposed
- Date: 2026-11-24
- Issue: #55

## Context
CLAUDE.md keeps a web dashboard out of scope: the restaurant's side was meant to live in a Telegram group, so the project could be validated without a web app, sign-in or customer data on a website.

M4 built that group: orders arrive there, the cashier approves or rejects them with buttons, and talks to customers in a handoff by replying to the bot's messages. Three manual runs on Telegram (2026-11-17 to 2026-11-20) showed where a group stops being enough:

- **No state at a glance.** Nothing shows which orders are pending, approved, rejected or with a person. Staff scroll back through the chat to find out.
- **Several handoffs share one thread.** Each customer has a `#CODE`, but with two or three customers at once their messages interleave and replying to the right one depends on picking the right message.
- **Orders can't be edited.** In the third run the cashier agreed to add a burger in the chat; the stored order stayed as it was. Staff can approve, reject or talk, nothing else.
- **No history or search,** by customer or by day.
- **Everyone in the group can do everything,** and who did what isn't recorded.

Some of this can be patched in the group: a `/pedidos` command that lists what is pending, removing the buttons once an order is decided. Editing an order or keeping several conversations apart doesn't fit a chat.

## Decision
Bring a small web panel for the restaurant's staff into scope, built in Phase 2 (pilot) and not in M4. It covers what the group can't: the orders list and detail, a handoff inbox with one thread per customer, and editing an order that waits for approval. The Telegram group stays, for notifications and quick actions; both act on the same orders.

Constraints, so the scope stays small:
- One restaurant per deployment, as the bot (ADR-0005). No multi-tenant.
- Staff sign in; there are no customer accounts and no customer-facing pages (customer web ordering stays deferred, ADR-0004).
- Server-rendered pages served by the same process family as the bot, reading the same PostgreSQL database. No separate frontend app.
- No payments, POS, CRM or analytics beyond the orders list.

## Alternatives considered
- **Keep everything in the Telegram group**, adding commands (`/pedidos`, `/pedido CODE`) and inline buttons. Cheap, no new surface. But editing an order through chat commands is error-prone, and handoffs still share one thread. Good as the interim step, not as the end state.
- **A Telegram Mini App** (a web page opened inside Telegram). It keeps staff in Telegram, but it is still a web app with its own sign-in (Telegram's init data), tied to one messenger right when the pilot moves customers to WhatsApp.
- **A separate frontend (React or similar) with an API.** More flexible, but a second project to build, test and deploy for a handful of screens.
- **Build it now, in M4.** It would force deployment, sign-in and hosting decisions that the pilot has to make anyway. Doing them once, in Phase 2, is cheaper.

## Consequences
- Positive: staff see and fix orders without scrolling a chat; handoffs scale beyond one customer at a time; who approved or edited what can be recorded.
- Negative: a web surface that holds customer names, phones and addresses: it needs sign-in, HTTPS and care with what it logs. More to deploy and keep running per restaurant.
- The group and the panel must not disagree. An order decided in one place has to show as decided in the other; the same revision rule as today (a decision applies only to the revision staff saw) covers both.
- Revisit if the pilot shows staff prefer to stay in the chat, or if a POS integration makes the panel redundant.
