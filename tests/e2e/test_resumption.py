"""A customer who comes back later. Customer wording is Spanish on purpose."""

from pathlib import Path

from pedidos_bot.channels.base import MessageKind
from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.engine.lifecycle import LifecycleState as S
from tests.e2e.harness import Chat
from tests.factories import config_dict, make_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "restaurants"
BURGER = load_restaurant("burger_demo", CONFIG_DIR)


async def _chat_with_a_burger() -> Chat:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    return chat


async def test_inside_the_silent_window_nothing_is_asked() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(minutes=119)
    assert "Anotado" in await chat.say("una doble")


async def test_after_the_silent_window_the_bot_asks_before_going_on() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=3)
    assert "¿Seguimos con tu pedido de 1 x Hamburguesa Sencilla?" in await chat.say("hola?")
    assert chat.reply.quick_replies != ()


async def test_saying_yes_handles_the_message_that_was_waiting() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=3)
    await chat.say("una doble")
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]
    assert "Anotado: 1 x Hamburguesa Doble" in await chat.say("sí")
    assert [line[0] for line in chat.lines] == ["hamburguesa_sencilla", "hamburguesa_doble"]


async def test_a_button_yes_also_handles_the_waiting_message() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=3)
    await chat.say("una doble")
    button = chat.message(None, MessageKind.INTERACTIVE_REPLY).model_copy(
        update={"reply_payload": "Sí"}
    )
    await chat.send(button)
    assert [line[0] for line in chat.lines] == ["hamburguesa_sencilla", "hamburguesa_doble"]


async def test_coming_back_with_a_yes_shows_the_summary_again_first() -> None:
    chat = await _chat_with_a_burger()
    for text in ("no", "para recoger", "nequi"):
        await chat.say(text)
    chat.wait(hours=3)
    await chat.say("sí")
    reply = await chat.say("sí")
    assert chat.state is S.AWAITING_CONFIRMATION
    assert "Este es tu pedido" in reply and "no te entendí" not in reply


async def test_recovering_with_a_yes_shows_the_next_question_not_an_error() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=25)
    await chat.say("sí")
    assert "no te entendí" not in await chat.say("sí")


async def test_a_menu_change_during_a_handoff_is_not_missed() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    await chat.say("un asesor")
    data["categories"][0]["products"][0]["price"] += 1000
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    await changed.say("hola?")
    assert changed.conversation.menu_version == chat.conversation.menu_version


async def test_an_old_offer_is_repriced_if_the_menu_changed_meanwhile() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    chat.wait(hours=25)
    await chat.say("hola?")
    price = chat.conversation.resume_offer.cart.lines[0].unit_price  # type: ignore[union-attr]
    data["categories"][0]["products"][0]["price"] += 1000
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    assert "¿Lo retomamos?" in await changed.say("sí")
    await changed.say("sí")
    assert changed.conversation.cart.lines[0].unit_price == price + 1000


async def test_saying_no_starts_over() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=3)
    await chat.say("hola?")
    assert "¿Qué te provoca?" in await chat.say("no")
    assert chat.lines == []


async def test_answering_something_else_continues_with_that() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=3)
    await chat.say("hola?")
    await chat.say("una doble")
    assert [line[0] for line in chat.lines] == ["hamburguesa_sencilla", "hamburguesa_doble"]


async def test_after_a_day_the_cart_can_be_recovered_in_a_new_conversation() -> None:
    chat = await _chat_with_a_burger()
    old_id = chat.conversation.id
    chat.wait(hours=25)
    reply = await chat.say("hola?")
    assert "1 x Hamburguesa Sencilla" in reply and "¿Lo retomamos?" in reply
    assert chat.conversation.id != old_id
    assert chat.lines == []
    await chat.say("sí")
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


async def test_after_a_day_saying_no_discards_the_cart() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=25)
    await chat.say("hola?")
    await chat.say("no")
    assert chat.lines == []


async def test_after_a_day_a_new_request_replaces_the_old_cart() -> None:
    chat = await _chat_with_a_burger()
    chat.wait(hours=25)
    await chat.say("hola?")
    await chat.say("una doble")
    assert chat.lines == [("hamburguesa_doble", 1, set())]


async def test_a_menu_change_revalidates_prices_right_away() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    data["categories"][0]["products"][0]["price"] += 1000
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    assert "¿Lo retomamos?" in await changed.say("hola?")
    old_price = chat.conversation.cart.lines[0].unit_price
    await changed.say("sí")
    assert changed.conversation.cart.lines[0].unit_price == old_price + 1000


async def test_a_product_that_is_gone_is_reported_and_dropped() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    await chat.say("una gaseosa")
    data["categories"][0]["products"][0]["available"] = False
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    reply = await changed.say("hola?")
    assert "Ya no tenemos 1 x" in reply
    await changed.say("sí")
    assert len(changed.lines) == 1


async def test_a_customer_is_told_when_their_whole_cart_is_gone() -> None:
    data = config_dict()
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    data["categories"][0]["products"][0]["available"] = False
    changed = Chat(make_config(**data), repository=chat.repository, first_message_id=100)
    assert "Ya no tenemos 1 x" in await changed.say("hola?")
    assert changed.lines == []


async def test_thresholds_come_from_the_config() -> None:
    data = config_dict()
    data["session"] = {"resume_silently_minutes": 5, "resume_prompt_hours": 1}
    chat = Chat(make_config(**data))
    await chat.say("una clasica")
    chat.wait(minutes=10)
    assert "¿Seguimos" in await chat.say("hola?")
    await chat.say("sí")
    chat.wait(hours=2)
    assert "¿Lo retomamos?" in await chat.say("hola?")


async def test_an_empty_conversation_is_not_asked_about() -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    chat.wait(hours=30)
    assert "Anotado" in await chat.say("una doble")


async def test_an_order_with_the_staff_is_not_asked_about() -> None:
    chat = Chat(BURGER)
    for text in ("una sencilla", "no", "para recoger", "nequi", "sí"):
        await chat.say(text)
    chat.wait(hours=30)
    assert "confirmación" in await chat.say("hola?")
    assert chat.state is S.PENDING_STAFF_CONFIRMATION
