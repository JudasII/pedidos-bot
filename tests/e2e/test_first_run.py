"""Cases found in the first manual run on Telegram (2026-11-17). Wording is the tester's."""

from pedidos_bot.engine.lifecycle import LifecycleState as S
from tests.e2e.harness import Chat
from tests.factories import sample_config

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
