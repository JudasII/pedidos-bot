"""The restaurant's side of an order: the notification, and staff approving or rejecting."""

from pedidos_bot.domain.order import OrderStatus
from pedidos_bot.engine.lifecycle import LifecycleState as S
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")  # shadow mode on
MINIMAL = sample_config("minimal_demo")  # shadow mode off


async def _order_sent(chat: Chat, address: str = "Calle 10 # 5-20") -> None:
    for text in ("una sencilla", "no", "domicilio", address, "efectivo", "50 mil", "sí"):
        await chat.say(text)
    assert chat.state is S.PENDING_STAFF_CONFIRMATION, chat.reply.text


async def test_staff_get_the_order_with_everything_needed_to_prepare_it() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    [notification] = chat.staff
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    text = notification.text
    assert f"Pedido {order.code}" in text
    assert "1 x Hamburguesa Sencilla: $18.000" in text
    assert "Domicilio: calle 10 # 5-20 ($5.000)" in text
    assert f"Total: ${order.totals.total:,}".replace(",", ".") in text
    assert "Pago: Efectivo (paga con $50.000)" in text
    assert [reply.title for reply in notification.quick_replies] == ["✓ Aprobar", "✗ Rechazar"]


async def test_the_order_knows_how_to_reach_its_customer() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    assert (order.channel, order.chat_ref, order.revision) == ("test", "chat", 1)


async def test_approving_confirms_the_order_and_tells_the_customer() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    seen = await chat.tap("Aprobar")
    assert "confirmado" in seen and "minutos" in seen
    assert chat.state is S.CONFIRMED
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None and order.status is OrderStatus.CONFIRMED
    assert f"Pedido {order.code} aprobado" in chat.staff[-1].text


async def test_rejecting_returns_to_ordering_with_the_cart_kept() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    key = chat.conversation.id
    seen = await chat.tap("Rechazar")
    assert "No pudimos tomar tu pedido" in seen and "“asesor”" in seen
    assert chat.state is S.ORDERING
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]
    order = await chat.repository.load_order(key)
    assert order is not None and order.status is OrderStatus.REJECTED


async def test_a_rejected_order_can_be_changed_and_sent_again_under_the_same_code() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    first = await chat.repository.load_order(chat.conversation.id)
    await chat.tap("Rechazar")
    await chat.say("agrega una doble")
    await chat.say("sí")
    assert chat.state is S.PENDING_STAFF_CONFIRMATION, chat.reply.text
    again = await chat.repository.load_order(chat.conversation.id)
    assert first is not None and again is not None
    assert (again.code, again.revision) == (first.code, 2)
    assert again.status is OrderStatus.PENDING_VALIDATION
    assert f"Pedido {first.code} (cambio 1)" in chat.staff[-1].text


async def test_a_tap_on_an_old_summary_does_not_approve_the_changed_order() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    old = chat.staff[-1]
    await chat.say("agrega una doble")
    assert "No lo preparen" in chat.staff[-1].text  # the withdrawal reaches staff too
    await chat.say("sí")
    assert chat.state is S.PENDING_STAFF_CONFIRMATION, chat.reply.text
    assert await chat.tap("Aprobar", on=old) == ""
    assert chat.state is S.PENDING_STAFF_CONFIRMATION
    assert "ya no está pendiente" in chat.staff[-1].text
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None and order.status is OrderStatus.PENDING_VALIDATION


async def test_a_second_tap_changes_nothing() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    await chat.tap("Aprobar")
    assert await chat.tap("Rechazar") == ""
    assert chat.state is S.CONFIRMED
    assert "ya no está pendiente" in chat.staff[-1].text


async def test_a_cancelled_order_tells_staff_and_cannot_be_approved() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    await chat.say("cancelar")
    assert "No lo preparen" in chat.staff[-1].text
    assert await chat.tap("Aprobar") == ""
    assert chat.state is S.CANCELLED


async def test_approving_during_a_handoff_confirms_and_keeps_the_person() -> None:
    chat = Chat(BURGER)
    await _order_sent(chat)
    await chat.say("asesor")
    assert "confirmado" in await chat.tap("Aprobar")
    assert chat.state is S.HANDOFF
    assert chat.conversation.lifecycle.before_handoff is S.CONFIRMED


async def test_without_shadow_mode_staff_are_told_with_nothing_to_decide() -> None:
    chat = Chat(MINIMAL)
    for text in ("un perrito", "no", "efectivo", "exacto", "sí"):
        await chat.say(text)
    assert chat.state is S.CONFIRMED, chat.reply.text
    [notification] = chat.staff
    assert "Recoge en el local" in notification.text
    assert "Confirmado con el cliente." in notification.text
    assert notification.quick_replies == ()


async def test_without_a_staff_chat_nobody_is_told() -> None:
    chat = Chat(BURGER, staff_chat=None)
    await _order_sent(chat)
    assert await chat.repository.pending_outbound() == []
    assert [sent.chat_ref for sent in chat.channel.sent] == ["chat"] * len(chat.channel.sent)
