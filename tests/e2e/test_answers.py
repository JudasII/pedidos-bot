"""How the bot reads answers and tells the customer what happened.

Each test here pins a behavior that mutation testing showed no test was guarding.
Customer wording is Spanish on purpose.
"""

import pytest
from structlog.testing import capture_logs

from pedidos_bot.channels.base import ChannelCapabilities
from pedidos_bot.engine.actions import AddItem, SetAddress, SetPayment, UpdateItem
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.engine.policy import Step
from pedidos_bot.llm.fake import ScriptedLLM
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")
MINIMAL = sample_config("minimal_demo")


async def _at(chat: Chat, step: Step, *said: str) -> None:
    for text in said:
        await chat.say(text)
    assert chat.conversation.asked is step, chat.reply.text


# --- questions --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("¿cuánto vale la doble?", "Hamburguesa Doble vale $25.000."),
        ("¿cuánto vale el sushi?", "No encontré “el sushi” en el menú."),
        ("¿a qué hora abren?", "Nuestro horario: martes, miércoles, jueves, domingo de 12:00"),
        ("¿tienen parqueadero?", "Eso no te lo sé responder."),
        ("cuánto es?", "Todavía no has pedido nada."),
    ],
)
async def test_questions_get_their_answer(question: str, answer: str) -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    reply = await chat.say(question)
    assert answer in reply
    assert "entendí" not in reply


# --- which one? -------------------------------------------------------------------------------


async def test_a_bare_yes_to_a_choice_of_several_adds_nothing() -> None:
    chat = Chat(BURGER)
    await chat.say("hamburguesa")
    await chat.say("sí")
    assert chat.lines == []
    assert chat.conversation.clarifications != ()


async def test_what_came_with_the_item_is_kept_when_the_product_is_chosen() -> None:
    chat = Chat(BURGER)
    await chat.say("una hamburguesa sin cebolla")
    await chat.say("Hamburguesa Sencilla")
    assert chat.lines == [("hamburguesa_sencilla", 1, {"sin_cebolla"})]


async def test_options_given_to_the_model_survive_the_question() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="hamburguesa", modifiers=("sin cebolla",))]]))
    await chat.say("una hamburguesa sin cebolla")
    await chat.say("Hamburguesa Doble")
    assert chat.lines == [("hamburguesa_doble", 1, {"sin_cebolla"})]


async def test_an_option_question_names_the_product() -> None:
    chat = Chat(BURGER)
    assert "Para Malteada, ¿te refieres a fresa?" in await chat.say("una malteada de fres")


async def test_yes_to_switching_to_delivery_at_the_summary_does_not_confirm() -> None:
    chat = Chat(BURGER)
    await _at(chat, Step.PAYMENT, "una sencilla", "no", "Ana", "3001234567", "para recoger")
    await chat.say("nequi")
    await chat.say("a la calle 10")
    await chat.say("sí")
    assert chat.state is S.AWAITING_CONFIRMATION
    assert await chat.repository.load_order(chat.conversation.id) is None


# --- name and phone ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "name"),
    [("soy Ximena.", "Ximena"), ("María José Pérez Gómez Ruiz", "María José Pérez Gómez Ruiz")],
)
async def test_names_up_to_five_words_are_kept_whole(said: str, name: str) -> None:
    chat = Chat(BURGER)
    await _at(chat, Step.NAME, "una sencilla", "no")
    await chat.say(said)
    assert chat.conversation.cart.customer_name == name


async def test_six_words_are_not_a_name() -> None:
    chat = Chat(BURGER)
    await _at(chat, Step.NAME, "una sencilla", "no")
    await chat.say("ana maria jose perez gomez ruiz")
    assert chat.conversation.cart.customer_name is None


async def test_a_wrong_phone_is_asked_again_without_the_llm() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sencilla")]]))  # one call, no more
    await _at(chat, Step.PHONE, "una sencilla", "no", "Ana")
    await chat.say("12345")
    assert chat.conversation.asked is Step.PHONE


async def test_an_item_at_the_phone_question_is_added() -> None:
    chat = Chat(BURGER)
    await _at(chat, Step.PHONE, "una sencilla", "no", "Ana")
    await chat.say("una coca cola personal")
    assert "coca_cola" in [line[0] for line in chat.lines]
    assert chat.conversation.cart.phone is None


# --- paying in cash ---------------------------------------------------------------------------


async def _cash(chat: Chat) -> None:
    await _at(
        chat,
        Step.CASH_CHANGE,
        "unos tenders",
        "no",
        "Ana",
        "3001234567",
        "para recoger",
        "efectivo",
    )


async def test_cash_for_exactly_the_total_is_accepted() -> None:
    chat = Chat(BURGER)
    await _cash(chat)
    reply = await chat.say("16 mil")
    assert "Pago: Efectivo (con $16.000)" in reply


async def test_cash_below_the_total_says_how_much_and_nothing_else() -> None:
    chat = Chat(BURGER)
    await _cash(chat)
    reply = await chat.say("10 mil")
    assert reply.startswith("Con $10.000 no alcanza: el total es $16.000.")
    assert "vendemos" not in reply and "entendí" not in reply


@pytest.mark.parametrize("said", ["50 mil", "exacto", "sí", "no"])
async def test_any_answer_to_the_cash_question_goes_to_the_summary(said: str) -> None:
    chat = Chat(BURGER)
    await _cash(chat)
    reply = await chat.say(said)
    assert reply.startswith("Este es tu pedido:"), reply


# --- telling the customer what happened -------------------------------------------------------


async def test_a_confirmed_order_without_shadow_mode_says_its_code_and_time() -> None:
    chat = Chat(MINIMAL)
    await _at(chat, Step.CONFIRM, "un perrito", "no", "Ana", "3001234567", "efectivo", "exacto")
    reply = await chat.say("sí")
    code = chat.conversation.code
    assert f"Su pedido {code} está confirmado. Tiempo estimado: 10 a 15 minutos." in reply


async def test_a_mode_the_restaurant_lacks_is_said_so() -> None:
    chat = Chat(MINIMAL)
    await chat.say("un perrito")
    assert "No contamos con esa opción de entrega." in await chat.say("a domicilio")


async def test_an_unknown_payment_method_is_named() -> None:
    chat = Chat(BURGER, ScriptedLLM([[SetPayment(method="bitcoin")]]))
    assert "No manejamos “bitcoin”." in await chat.say("pago con bitcoin")


async def test_cash_given_with_the_payment_reaches_the_summary() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="tenders")], [SetPayment(method="efectivo", cash_change_for=50000)]]
    )
    chat = Chat(BURGER, llm)
    await _at(chat, Step.PAYMENT, "unos tenders", "no", "Ana", "3001234567", "para recoger")
    assert "Pago: Efectivo (con $50.000)" in await chat.say("efectivo, pago con 50 mil")


async def test_address_details_reach_the_restaurant() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="tenders")],
            [SetAddress(address="calle 10 # 5-20", details="apto 301")],
        ]
    )
    chat = Chat(BURGER, llm)
    await _at(chat, Step.ADDRESS, "unos tenders", "no", "Ana", "3001234567", "domicilio")
    for text in ("calle 10 # 5-20 apto 301", "nequi", "sí"):
        await chat.say(text)
    assert "apto 301" in chat.staff[-1].text


async def test_a_change_the_cart_refuses_names_the_product() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="sencilla")], [UpdateItem(product="sencilla", quantity=25)]]
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla")
    reply = await chat.say("que sean 25")
    assert "Eso no se puede con Hamburguesa Sencilla" in reply
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


async def test_an_unknown_option_names_the_product() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sencilla", modifiers=("mango",))]]))
    assert "No pude ponerle “mango” a Hamburguesa Sencilla." in await chat.say("x")


async def test_which_line_is_asked_by_product_name() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    await chat.say("una sencilla sin cebolla")
    assert "Tienes varias líneas de Hamburguesa Sencilla" in await chat.say("quita la sencilla")


async def test_removing_every_unit_removes_the_line() -> None:
    chat = Chat(BURGER)
    await chat.say("2 sencillas")
    assert "Listo, quité 2 x Hamburguesa Sencilla" in await chat.say("quita 2 sencillas")
    assert chat.lines == []


# --- changing the order, people, and the counter ---------------------------------------------


async def test_wanting_a_change_on_a_new_chat_asks_what_not_for_a_person() -> None:
    chat = Chat(BURGER)
    assert "qué le cambiamos" in await chat.say("quiero hacer un cambio")
    assert chat.state is S.ORDERING


async def test_back_from_a_person_the_misunderstanding_count_starts_over() -> None:
    chat = Chat(BURGER)
    await chat.say("xyzzy")
    await chat.say("asesor")
    await chat.say("volver")
    await chat.say("plugh")
    assert chat.state is S.ORDERING


async def test_a_misunderstanding_is_logged_with_what_was_said_back() -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    with capture_logs() as logs:
        await chat.say("xyzzy")
    assert logs[-1]["notices"] == ("not_understood", "offer_person")


# --- second pass, after the scoped re-run -----------------------------------------------------


async def test_a_bare_yes_to_a_choice_of_options_picks_none() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sencilla", modifiers=("extra",))]]))
    reply = await chat.say("una sencilla con extra")
    assert "Para Hamburguesa Sencilla, ¿cuál quieres: tocineta extra" in reply
    await chat.say("sí")
    assert chat.lines == []


async def test_two_unclear_options_are_asked_one_after_the_other() -> None:
    chat = Chat(
        BURGER, ScriptedLLM([[AddItem(product="sencilla", modifiers=("extra", "cebolla"))]])
    )
    await chat.say("una sencilla con extra y cebolla")
    assert "sin cebolla o cebolla caramelizada" in await chat.say("queso extra")
    await chat.say("cebolla caramelizada")
    assert chat.lines == [
        ("hamburguesa_sencilla", 1, {"extra_queso", "extra_cebolla_caramelizada"})
    ]


async def test_an_unknown_option_in_a_change_names_the_product() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="sencilla")], [UpdateItem(product="sencilla", add_modifiers=("mango",))]]
    )
    chat = Chat(BURGER, llm)
    await chat.say("una sencilla")
    assert "No pude ponerle “mango” a Hamburguesa Sencilla." in await chat.say("ponle mango")


async def test_address_details_survive_the_switch_to_delivery() -> None:
    llm = ScriptedLLM(
        [[AddItem(product="tenders")], [SetAddress(address="calle 10 # 5-20", details="apto 301")]]
    )
    chat = Chat(BURGER, llm)
    await _at(chat, Step.PAYMENT, "unos tenders", "no", "Ana", "3001234567", "para recoger")
    await chat.say("calle 10 # 5-20 apto 301")
    for text in ("sí", "nequi", "sí"):
        await chat.say(text)
    assert "apto 301" in chat.staff[-1].text


async def test_questions_never_count_as_misunderstandings() -> None:
    chat = Chat(BURGER)
    for _ in range(3):
        await chat.say("¿a qué hora abren?")
    assert chat.state is S.ORDERING


async def test_yes_to_anything_else_is_understood() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    assert "entendí" not in await chat.say("sí")


async def test_a_short_answer_to_the_cash_question_never_reaches_the_llm() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="tenders")]]))  # one call, no more
    await _at(
        chat,
        Step.CASH_CHANGE,
        "unos tenders",
        "no",
        "Ana",
        "3001234567",
        "para recoger",
        "efectivo",
    )
    assert (await chat.say("exacto")).startswith("Este es tu pedido:")


# --- third pass: survivors the shared scripted fake had been hiding ---------------------------


async def test_a_reply_is_its_notes_and_question_separated_by_a_blank_line() -> None:
    chat = Chat(BURGER)
    await chat.say("hola")
    reply = await chat.say("una sencilla")
    assert reply == (
        "Anotado: 1 x Hamburguesa Sencilla.\n\n"
        "¿Le sumamos algo de acompañantes, bebidas o malteadas?"
    )


async def test_a_line_lists_its_options_separated_by_commas() -> None:
    chat = Chat(
        BURGER, ScriptedLLM([[AddItem(product="sencilla", modifiers=("tocineta", "huevo"))]])
    )
    assert "Anotado: 1 x Hamburguesa Sencilla (tocineta extra, huevo)." in await chat.say("x")


async def test_choosing_a_product_with_nothing_extra_adds_it_plainly() -> None:
    chat = Chat(BURGER)
    await chat.say("hamburguesa")
    reply = await chat.say("Hamburguesa Sencilla")
    assert "No pude ponerle" not in reply
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


async def test_too_many_extras_are_refused_by_product_name() -> None:
    extras = ("tocineta", "queso extra", "carne extra", "huevo", "cebolla caramelizada")
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sencilla", modifiers=extras)]]))
    assert "Eso no se puede con Hamburguesa Sencilla" in await chat.say("x")
    assert chat.lines == []


async def test_the_last_numbered_option_can_be_chosen() -> None:
    chat = Chat(BURGER, capabilities=ChannelCapabilities())  # numbered options
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger"):
        await chat.say(text)
    await chat.say("3")
    payment = chat.conversation.cart.payment
    assert payment is not None and payment.method_id == "card_on_delivery"


async def test_coming_back_after_exactly_the_quiet_time_asks_first() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    chat.wait(minutes=BURGER.session.resume_silently_minutes)
    assert "¿Seguimos con tu pedido" in await chat.say("una corona")


async def test_coming_back_after_exactly_a_day_offers_to_recover() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    chat.wait(hours=BURGER.session.resume_prompt_hours)
    assert "¿Lo retomamos?" in await chat.say("una corona")
