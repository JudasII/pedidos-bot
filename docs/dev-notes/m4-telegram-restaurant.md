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
