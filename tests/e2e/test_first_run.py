"""Cases found in the first manual run on Telegram (2026-11-17). Wording is the tester's."""

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
