"""Cases found in the first manual run on Telegram (2026-11-17). Wording is the tester's."""

import pytest
from structlog.testing import capture_logs

from pedidos_bot.engine.lifecycle import LifecycleState as S
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
