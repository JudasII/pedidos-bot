# M4 — Telegram, the restaurant side, shadow mode

Work in progress: phase A (customers on Telegram) and the first half of phase B (staff notification and shadow mode) are done. The human relay (#20) is next.

## How the pieces fit

- `channels/telegram.py` reads private chats (customers) and one group (the staff chat). Both arrive as `InboundMessage`; the staff chat's carry its chat id as `chat_ref`.
- `app.py` routes by that id. Customer messages go to `ConversationService.handle`. Staff messages are parsed by `engine/staff.py`; today only a tap on ✓ or ✗ means something, and it goes to `handle_staff`.
- When a customer confirms, the turn saves the order and puts two messages in the outbox in the same transaction: the reply to the customer and the notification to the staff chat. The notification is built from the order alone (ADR-0004).
- A button carries `verdict:order key:revision`. `handle_staff` locks the customer's conversation and applies the decision only if that revision is still the one waiting. Anything else (the customer changed or cancelled the order, a second tap, a tap on an old summary) changes nothing and the staff chat is told.

## Decisions

- **✗ returns to ordering.** The order is marked rejected, the customer keeps the cart and is told they can change it or ask for a person. Sending it again keeps the order's code and raises its revision.
- **A decision during a handoff** is applied and the handoff goes on: the customer who asked for a person still gets one.
- **Buttons stay on the message** after a decision. A later tap gets "ya no está pendiente". Removing them needs `editMessageReplyMarkup`, left for later.
- **A tap is acknowledged to Telegram at once**, before its turn runs, so the button never spins while the engine works.
- **Anyone in the staff chat can decide.** Who tapped is not recorded yet.

## Manual demo (shadow mode)

Not yet run against the real Bot API: these are the steps.

1. Create a bot with BotFather and put its token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Create a Telegram group, add the bot, and put the group's id (a negative number) in `.env` as `BURGER_DEMO_STAFF_CHAT_ID`. The staff chat must not be the chat you order from.
3. `docker compose up -d db`, then `uv run pedidos-bot run --restaurant burger_demo`.
4. From a private chat with the bot, order: `una sencilla`, `no`, `para recoger`, `nequi`, `sí`. The bot answers that the order is being confirmed.
5. The group receives the order with its code, lines, total, fulfillment and payment, and two buttons.
6. Tap **✓ Aprobar**: the customer is told the order is confirmed, the group gets "Pedido XXXX aprobado".
7. Repeat the order and tap **✗ Rechazar**: the customer is told it could not be taken as it is. Write `agrega una doble`, then `sí`: the group gets the same code with "(cambio 1)".
8. Tap ✓ on the first, older message of that order: nothing changes and the group is told it is no longer pending.

Use only your own test accounts; nothing from a real customer goes into this file.

## First manual run (2026-11-17)

The flow worked end to end on Telegram: order, summary, the cashier's ✓ from the group, confirmation. Three findings, each now covered by `tests/e2e/test_first_run.py`:

- **A stale "which one?" added a second burger.** "una hamburguesa con papas sin cebolla" was read as one ambiguous item and the bot asked which product. The tester rephrased the whole order instead; the old question stayed, and tapping "Hamburguesa Sencilla" added a second burger ($18.000 too much). Ordering one of the options, for at least the units asked about, now settles the question.
- **Off-menu requests got "no te entendí".** Whether something is on the menu was already decided by the resolver; the answer is now the restaurant's `tone.off_menu` phrase (with `{item}`), or a plain "no vendemos {item}" without one. A "sí" to "what would you like?" shows the menu.
- **Nobody knew who the order was for.** Once the items are done the bot asks for a name and a phone (a Colombian mobile or landline, ten digits). Both appear in the summary and in the staff notification, and are masked in the logs.

Still open: "no, así está bien" is not a known phrase for finishing (#43, #44), and "hamburguesa con papas" is one item to the rule-based fake LLM. The real model (M5) should read it as two.

## Handoff relay (#20)

Found in the first run: "asesor" put the chat in handoff, nobody at the restaurant was told, and there was no way out. Every later message got no answer.

- **Asking for a person** (or a voice note, photo, location, two misunderstandings) tells the staff chat: "🙋 Cliente #AB12 pide hablar con una persona", what they wrote, the order in progress and how to answer.
- **What the customer writes** during the handoff reaches the staff chat as "💬 #AB12: …". Media can't be forwarded yet: a voice note shows as "[nota de voz]", a photo as "[foto]", a location as a map link.
- **The cashier answers** with Telegram's "Reply" on any of those messages (they all carry `#AB12`), or with `/responder AB12 text`. Privacy mode delivers both: replies to the bot's messages and commands.
- **`/devolver AB12`**, or `/devolver` as a reply, gives the customer back to the bot, which goes on with the order where it was.
- **The customer can leave**: the handoff message carries a "Volver al asistente" button (or they write "volver"), and "cancelar" cancels. The staff chat is told either way.
- **Nobody answers**: after `session.handoff_timeout_minutes` without a message from staff, the bot takes over, says so to the customer and to the staff chat. A check runs every minute, and the customer's next message ends an expired handoff too. A chat left in handoff by an older version (no handoff details) ends at its next message.

## Second manual run (2026-11-19)

At the phone question the tester wrote "a este". It scored 83 against the alias "la especial" (the shared article "la"/"a" did the work), so the bot asked "Especial or Sencilla?". The question was queued again on every turn, "no pedí hamburguesa" didn't drop it, and each tap on "Hamburguesa Sencilla" added a burger: the order reached the restaurant with 4 burgers and 3 tenders nobody asked for ($140.000 instead of $36.000). The cashier rejected it, which is what shadow mode is for. Fixed: leading articles carry no weight in fuzzy matching, an identical question is never queued twice, a no to the thing asked about drops it, and the fake LLM never reads a refusal or a bare number as an order. The conversation is in `tests/e2e/test_first_run.py`.

Still open from that run, with the rule-based fake LLM: "pedí tenders" (a restatement) adds another one, and "cuánto es?" isn't answered with the total.

## Logs

`run` keeps its log in `data/private/logs/<restaurant>.log` (git-ignored), one readable line per event, appended across restarts. The terminal still shows the same events as JSON. Each `turn` line has the customer's message and the bot's reply, both masked (phones, street addresses, and the address the customer gave, which the summary repeats), plus the state before and after, the actions understood and the notices sent.
