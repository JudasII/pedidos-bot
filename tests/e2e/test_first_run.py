"""Cases found in the first manual run on Telegram (2026-11-17). Wording is the tester's."""

import pytest
from structlog.testing import capture_logs

from pedidos_bot.engine.actions import AddItem
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.engine.policy import Step
from pedidos_bot.llm.fake import ScriptedLLM
from tests.e2e.harness import Chat
from tests.factories import make_config, sample_config

BURGER = sample_config("burger_demo")


async def test_restating_the_order_settles_the_question_it_answers() -> None:
    """The tester was asked "which one?" and rephrased the whole order instead of answering.

    The old question stayed alive, and the next tap on "Hamburguesa Sencilla" added a second
    burger nobody asked for.
    """
    chat = Chat(BURGER)
    await chat.say("hola")
    asked = await chat.say("una hamburguesa con papas sin cebolla")
    assert "¿Cuál quieres" in asked
    reply = await chat.say("una hamburguesa sencilla sin cebolla y unas papas")
    assert "¿Cuál quieres" not in reply
    assert "tamaño" in reply  # the fries still need one
    await chat.say("personal")
    assert chat.lines == [
        ("hamburguesa_sencilla", 1, {"sin_cebolla"}),
        ("papas_fritas", 1, {"papas_personal"}),
    ]
    assert chat.state is S.ORDERING


async def test_a_question_about_another_item_is_kept() -> None:
    chat = Chat(BURGER)
    await chat.say("una hamburguesa con papas sin cebolla")
    reply = await chat.say("una coca cola personal")
    assert "¿Cuál quieres" in reply  # the coke answers nothing that was asked


async def test_asking_for_something_off_the_menu_gets_the_restaurants_answer() -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    reply = await chat.say("quiero pedir una pizza")
    assert reply == BURGER.tone.off_menu.format(item="pizza")  # type: ignore[union-attr]
    assert "entendí" not in reply and "asesor" not in reply
    assert chat.conversation.misunderstood == 0


async def test_yes_to_the_suggestion_shows_the_menu() -> None:
    chat = Chat(BURGER)
    await chat.say("una pizza")
    reply = await chat.say("sí")
    assert "Hamburguesa Sencilla" in reply and "entendí" not in reply


async def test_off_the_menu_in_the_middle_of_an_order_keeps_the_order() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    await chat.say("no")
    reply = await chat.say("y una pizza")
    assert "no vendemos pizza" in reply
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


async def test_a_formal_restaurant_answers_in_its_own_words() -> None:
    minimal = sample_config("minimal_demo")
    chat = Chat(minimal)
    reply = await chat.say("una pizza")
    assert reply.endswith(minimal.tone.off_menu.format(item="pizza"))  # type: ignore[union-attr]


async def test_without_a_phrase_the_bot_says_it_plainly() -> None:
    chat = Chat(make_config())
    reply = await chat.say("una pizza")
    assert "no vendemos pizza" in reply.lower()
    assert "¿Qué te provoca?" in reply


async def _items_done(chat: Chat) -> str:
    await chat.say("una sencilla")
    return await chat.say("no")


async def test_when_the_items_are_done_the_bot_asks_who_the_order_is_for() -> None:
    chat = Chat(BURGER)
    assert "¿A nombre de quién" in await _items_done(chat)
    assert "teléfono" in await chat.say("Juan Pérez")
    assert "domicilio o pasas a recoger" in await chat.say("300 123 4567")
    for text in ("para recoger", "nequi"):
        reply = await chat.say(text)
    assert "A nombre de: Juan Pérez" in reply
    assert "Teléfono: 300 123 4567" in reply
    await chat.say("sí")
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    assert (order.cart.customer_name, order.cart.phone) == ("Juan Pérez", "3001234567")
    assert "Cliente: Juan Pérez · 300 123 4567" in chat.staff[-1].text


@pytest.mark.parametrize(
    ("said", "name"),
    [("soy Ana", "Ana"), ("a nombre de María José", "María José"), ("me llamo luis.", "luis")],
)
async def test_the_name_is_read_from_how_people_say_it(said: str, name: str) -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say(said)
    assert chat.conversation.cart.customer_name == name


@pytest.mark.parametrize("said", ["+57 300 123 4567", "3001234567", "300-123-4567", "601 234 5678"])
async def test_phones_are_read_in_the_usual_forms(said: str) -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say("Ana")
    await chat.say(said)
    assert chat.conversation.cart.phone == said.replace("+57", "").replace("-", "").replace(" ", "")


async def test_a_number_that_is_not_a_phone_is_asked_again() -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say("Ana")
    reply = await chat.say("12345")
    assert "10 dígitos" in reply and "teléfono" in reply
    assert chat.conversation.cart.phone is None
    assert chat.conversation.misunderstood == 0


async def test_cancelling_while_asked_for_the_name_cancels() -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say("cancelar")
    assert chat.state is S.CANCELLED
    assert chat.conversation.cart.customer_name is None


async def test_more_items_while_asked_for_the_name_are_added() -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say("una coca cola personal")
    assert chat.conversation.cart.customer_name is None
    assert "coca_cola" in [line[0] for line in chat.lines]


@pytest.mark.parametrize("said", ["y una pizza", "quítale la cebolla", "¿cuánto se demora?"])
async def test_what_is_clearly_not_a_name_is_not_taken_as_one(said: str) -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    await chat.say(said)
    assert chat.conversation.cart.customer_name is None


async def test_name_and_phone_stay_out_of_the_logs() -> None:
    chat = Chat(BURGER)
    await _items_done(chat)
    with capture_logs() as logs:
        for text in ("Juan Pérez", "300 123 4567", "para recoger", "nequi"):
            await chat.say(text)
    text = str(logs)
    assert "Juan" not in text and "Pérez" not in text and "4567" not in text
    assert logs[0]["message"] == "[name]"
    assert "A nombre de: [name]" in logs[-1]["reply"]


# --- second manual run (2026-11-19) ---------------------------------------------------------


async def _asked_for_the_phone(chat: Chat) -> None:
    for text in ("quiero unos tenders, una malteada y una corona", "chocolate", "no", "Pepito"):
        await chat.say(text)
    assert chat.conversation.asked is Step.PHONE


async def test_a_stray_short_answer_is_not_read_as_a_burger() -> None:
    """At the phone question the tester wrote "a este"; it became "Especial or Sencilla?"."""
    chat = Chat(BURGER)
    await _asked_for_the_phone(chat)
    reply = await chat.say("a este")
    assert "¿Cuál quieres" not in reply
    assert chat.conversation.clarifications == ()


async def test_the_same_question_is_never_queued_twice() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="hamburguesa")]] * 3))
    for _ in range(3):
        await chat.say("hamburguesa")
    assert len(chat.conversation.clarifications) == 1
    await chat.say("Hamburguesa Sencilla")
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


@pytest.mark.parametrize("said", ["no pedí hamburguesa", "sin hamburguesa", "no"])
async def test_saying_no_to_which_one_drops_the_question(said: str) -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    await chat.say("hamburguesa")
    assert "¿Cuál quieres" in chat.reply.text
    reply = await chat.say(said)
    assert "¿Cuál quieres" not in reply
    assert chat.conversation.clarifications == ()
    assert [line[0] for line in chat.lines] == ["chicken_tenders"]


async def test_a_no_about_something_else_keeps_the_question() -> None:
    chat = Chat(BURGER)
    await chat.say("hamburguesa")
    await chat.say("no quiero cebolla")
    assert chat.conversation.clarifications != ()


# --- third manual run (2026-11-20) ----------------------------------------------------------


async def _confirmed(chat: Chat) -> str:
    """A delivery order the cashier approved. Returns its code."""
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi", "sí"):
        await chat.say(text)
    await chat.tap("Aprobar")
    assert chat.state is S.CONFIRMED
    return chat.conversation.code


@pytest.mark.parametrize("said", ["quiero hacer un cambio", "quiero editar mi pedido", "editar"])
async def test_wanting_to_change_something_is_not_off_the_menu(said: str) -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    assert "no vendemos" not in await chat.say(said)


async def test_the_customer_is_told_the_order_code() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi"):
        await chat.say(text)
    code = chat.conversation.code
    assert f"pedido {code}" in await chat.say("sí")
    assert f"Pedido {code}" in chat.staff[-1].text  # the same code the restaurant sees
    assert f"{code} confirmado" in await chat.tap("Aprobar")


async def test_changing_a_confirmed_order_goes_to_a_person_with_the_order() -> None:
    chat = Chat(BURGER)
    code = await _confirmed(chat)
    reply = await chat.say("quiero hacer un cambio")
    assert "ya está confirmado" in reply and "alguien del equipo" in reply
    assert chat.state is S.HANDOFF
    notice = chat.staff[-1].text
    assert f"Último pedido: {code} (confirmado)" in notice and "Chicken Tenders" in notice


async def test_a_person_asked_for_after_an_order_knows_which_one() -> None:
    chat = Chat(BURGER)
    code = await _confirmed(chat)
    await chat.say("asesor")
    assert f"Último pedido: {code} (confirmado)" in chat.staff[-1].text


async def test_asking_to_change_before_confirming_asks_what() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi"):
        await chat.say(text)
    assert chat.state is S.AWAITING_CONFIRMATION
    assert "qué le cambiamos" in await chat.say("quiero hacer un cambio")
    assert chat.state is S.ORDERING


async def test_the_bot_says_it_is_a_bot_once_per_chat() -> None:
    chat = Chat(BURGER)
    await _confirmed(chat)
    assert "asistente automático" not in await chat.say("una sencilla")


async def test_cash_below_the_total_is_asked_again() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "efectivo"):
        await chat.say(text)
    reply = await chat.say("10 mil")
    assert "no alcanza" in reply and "$16.000" in reply
    assert chat.conversation.cart.payment is not None
    assert chat.conversation.cart.payment.cash_change_for is None
    assert chat.state is S.ORDERING


@pytest.mark.parametrize("said", ["cuanto es?", "¿cuánto sería?", "cuál es el total?"])
async def test_how_much_is_answered_with_the_total(said: str) -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    reply = await chat.say(said)
    assert "$16.000" in reply and "no te lo sé" not in reply
