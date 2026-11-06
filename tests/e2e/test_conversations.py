"""Whole conversations through the real engine. Customer wording is Spanish on purpose."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from pedidos_bot.channels.base import ChannelCapabilities, MessageKind
from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.actions import AddItem, Confirm, UpdateItem
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.engine.policy import Step
from pedidos_bot.llm.fake import ScriptedLLM
from tests.e2e.harness import Chat

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "restaurants"
BURGER = load_restaurant("burger_demo", CONFIG_DIR)
MINIMAL = load_restaurant("minimal_demo", CONFIG_DIR)


@dataclass(frozen=True)
class Scenario:
    config: RestaurantConfig
    order: str
    product_id: str
    other: str
    other_id: str
    payment: str
    after_confirming: S
    total: str


SCENARIOS = [
    Scenario(
        BURGER, "una sencilla", "hamburguesa_sencilla", "una doble", "hamburguesa_doble",
        "nequi", S.PENDING_STAFF_CONFIRMATION, "Total: $18.000",
    ),
    Scenario(
        MINIMAL, "un perrito", "perro_sencillo", "un suizo", "perro_suizo",
        "efectivo", S.CONFIRMED, "Total: $9.000",
    ),
]  # fmt: skip
both = pytest.mark.parametrize("s", SCENARIOS, ids=lambda s: s.config.restaurant.id)


async def _to_confirmation(chat: Chat, s: Scenario) -> None:
    """Answer whatever the bot asks until it shows the summary."""
    answers = {Step.ANYTHING_ELSE: "no", Step.FULFILLMENT: "para recoger", Step.PAYMENT: s.payment}
    await chat.say(s.order)
    while chat.state is S.ORDERING:
        asked = chat.conversation.asked
        assert asked in answers, asked
        await chat.say(answers[asked])


@both
async def test_happy_path(s: Scenario) -> None:
    chat = Chat(s.config)
    assert s.config.tone.greeting in await chat.say("hola")
    await _to_confirmation(chat, s)
    assert chat.state is S.AWAITING_CONFIRMATION
    summary = chat.reply.text
    assert s.total in summary and "minutos" in summary
    await chat.say("dale")
    assert chat.state is s.after_confirming


async def test_full_delivery_flow_in_order() -> None:
    chat = Chat(BURGER)
    assert "Burger Demo" in await chat.say("hola")
    reply = await chat.say("una sencilla")
    assert "Anotado: 1 x Hamburguesa Sencilla" in reply
    assert "¿Le sumamos algo de acompañantes, bebidas o malteadas?" in reply
    assert "¿Algo más?" in await chat.say("una coca personal")
    assert "domicilio o pasas a recoger" in await chat.say("no")
    assert "dirección" in await chat.say("domicilio")
    assert "pagar" in await chat.say("calle 10 # 5-20")
    assert "vueltas" in await chat.say("efectivo")
    reply = await chat.say("50 mil")
    assert "Domicilio a calle 10 # 5-20: $5.000" in reply
    assert "Total: $27.000" in reply  # 18.000 + 4.000 + 5.000 delivery
    assert "Pago: Efectivo (con $50.000)" in reply
    assert "40 a 55 minutos" in reply
    assert chat.state is S.AWAITING_CONFIRMATION
    await chat.say("sí")
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


@pytest.mark.parametrize(
    "llm",
    [
        None,
        ScriptedLLM(
            [
                [
                    AddItem(product="sencilla"),
                    AddItem(product="sencilla", modifiers=("sin cebolla",)),
                    AddItem(product="coca", modifiers=("grande",)),
                ]
            ]
        ),
    ],
    ids=["rules", "scripted"],
)
async def test_two_burgers_one_different_and_a_drink(llm: ScriptedLLM | None) -> None:
    chat = Chat(BURGER, llm)
    reply = await chat.say("2 sencillas, una sin cebolla, y una coca grande")
    assert "(sin cebolla) y 1 x Coca-Cola" in reply
    assert chat.lines == [
        ("hamburguesa_sencilla", 1, set()),
        ("hamburguesa_sencilla", 1, {"sin_cebolla"}),
        ("coca_cola", 1, {"gaseosa_litro_y_medio"}),
    ]


async def test_multi_field_message_reaches_the_summary_in_one_turn() -> None:
    chat = Chat(BURGER)
    reply = await chat.say("una doble, a la calle 10, pago por nequi")
    assert chat.state is S.AWAITING_CONFIRMATION
    assert "Domicilio a calle 10" in reply and "Total: $30.000" in reply


async def test_multi_field_message_in_the_pickup_only_restaurant() -> None:
    chat = Chat(MINIMAL)
    reply = await chat.say("un suizo, pago en efectivo")
    assert chat.state is S.AWAITING_CONFIRMATION
    assert "Recoge en el local" in reply and "Total: $14.000" in reply


@both
async def test_correction_at_confirmation_keeps_the_rest(s: Scenario) -> None:
    chat = Chat(s.config)
    await _to_confirmation(chat, s)
    cart = chat.conversation.cart
    await chat.say(s.other)
    assert chat.state is S.AWAITING_CONFIRMATION
    assert [line[0] for line in chat.lines] == [s.product_id, s.other_id]
    assert chat.conversation.cart.payment == cart.payment
    assert chat.conversation.cart.fulfillment == cart.fulfillment
    assert "¿Confirm" in chat.reply.text


async def test_no_at_confirmation_asks_what_to_change_and_then_returns() -> None:
    chat = Chat(BURGER)
    await _to_confirmation(chat, SCENARIOS[0])
    assert "qué le cambiamos" in await chat.say("no")
    assert chat.state is S.ORDERING
    await chat.say("quita la sencilla")
    assert chat.state is S.ORDERING and chat.lines == []
    await chat.say("una doble")
    await chat.say("no")  # an emptied cart asks "anything else?" again
    assert chat.state is S.AWAITING_CONFIRMATION
    assert chat.conversation.cart.payment is not None


async def test_the_greeting_only_opens_a_conversation() -> None:
    chat = Chat(BURGER)
    await chat.say("una sencilla")
    reply = await chat.say("quita la sencilla")
    assert BURGER.tone.greeting not in reply
    assert "¿Qué te provoca?" in reply


async def test_confirm_with_a_change_shows_the_new_summary_first() -> None:
    llm = ScriptedLLM(
        [
            [AddItem(product="sencilla")],
            [Confirm(), UpdateItem(product="sencilla", add_modifiers=("sin tomate",))],
        ]
    )
    chat = Chat(BURGER, llm)
    for text in ("una sencilla", "no", "para recoger", "nequi"):
        await chat.say(text)
    assert chat.state is S.AWAITING_CONFIRMATION
    reply = await chat.say("sí, pero sin tomate")
    assert chat.state is S.AWAITING_CONFIRMATION
    assert "sin tomate" in reply and "¿Confirmamos?" in reply
    assert chat.lines == [("hamburguesa_sencilla", 1, {"sin_tomate"})]


@both
async def test_cancellation(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say(s.order)
    await chat.say("cancelar")
    assert chat.state is S.CANCELLED
    assert chat.reply.quick_replies == ()


@both
async def test_a_new_message_after_cancelling_starts_a_new_order(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say(s.order)
    await chat.say("cancelar")
    await chat.say(s.order)
    assert chat.state is S.ORDERING
    assert chat.lines == [(s.product_id, 1, set())]


@both
async def test_menu_question_does_not_lose_the_order(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say(s.order)
    reply = await chat.say("¿qué tienen en el menú?")
    assert next(iter(s.config.menu.products())).name in reply
    assert "$" in reply
    assert chat.state is S.ORDERING and len(chat.lines) == 1


@both
async def test_asking_for_a_person_hands_off_and_the_bot_goes_quiet(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say(s.order)
    await chat.say("quiero hablar con alguien")
    assert chat.state is S.HANDOFF
    assert await chat.say("hola?") == ""
    assert chat.lines == [(s.product_id, 1, set())]


@both
@pytest.mark.parametrize("kind", [MessageKind.VOICE, MessageKind.LOCATION, MessageKind.IMAGE])
async def test_non_text_messages_hand_off(s: Scenario, kind: MessageKind) -> None:
    chat = Chat(s.config, ScriptedLLM())
    await chat.send(chat.message(None, kind))
    assert chat.state is S.HANDOFF


@both
async def test_two_turns_without_understanding_escalate(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say("asdf qwer")
    assert chat.state is S.ORDERING
    await chat.say("zxcv uiop")
    assert chat.state is S.HANDOFF


async def test_understanding_resets_the_counter() -> None:
    chat = Chat(BURGER)
    await chat.say("asdf qwer")
    await chat.say("una sencilla")
    await chat.say("zxcv uiop")
    assert chat.state is S.ORDERING


async def test_shortcuts_never_reach_the_llm() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sencilla")]]))
    await chat.say("una sencilla")
    assert chat.last is not None and not chat.last.resolved_without_llm
    assert chat.last.usage is not None and chat.last.usage.output_tokens > 0
    for text in ("no", "para recoger", "nequi", "sí"):
        await chat.say(text)  # ScriptedLLM raises if any of these calls it
        assert chat.last.resolved_without_llm
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


async def test_ambiguous_product_is_asked_and_answered() -> None:
    chat = Chat(BURGER)
    reply = await chat.say("2 cervezas")
    assert "Club Colombia Dorada, Águila o Corona" in reply
    assert chat.lines == []
    await chat.say("corona")
    assert chat.lines == [("corona", 2, set())]


async def test_two_clarifications_are_asked_one_at_a_time() -> None:
    chat = Chat(BURGER)
    await chat.say("una cerveza y una hamburguesa")
    await chat.say("corona")
    assert "Hamburguesa Sencilla" in chat.reply.text
    await chat.say("la doble")
    assert [line[0] for line in chat.lines] == ["corona", "hamburguesa_doble"]


async def test_the_modifier_survives_the_clarifying_question() -> None:
    chat = Chat(BURGER)
    await chat.say("hamburguesa con doble carne")
    assert chat.lines == []
    await chat.say("la especial")
    assert chat.lines == [("hamburguesa_especial", 1, {"extra_carne"})]


async def test_ambiguous_modifier_is_asked_on_the_line() -> None:
    chat = Chat(BURGER)
    reply = await chat.say("una sencilla con cebolla")
    assert "sin cebolla o cebolla caramelizada" in reply
    await chat.say("caramelizada")
    assert chat.lines == [("hamburguesa_sencilla", 1, {"extra_cebolla_caramelizada"})]


async def test_single_candidate_modifier_is_a_yes_or_no_question() -> None:
    chat = Chat(MINIMAL)
    reply = await chat.say("un perro con rosada")
    assert "¿se refiere a sin salsa rosada?" in reply
    await chat.say("sí")
    assert chat.lines == [("perro_sencillo", 1, {"sin_salsa_rosada"})]


async def test_saying_no_to_a_clarification_drops_it() -> None:
    chat = Chat(MINIMAL)
    await chat.say("un perro con rosada")
    await chat.say("no")
    assert chat.lines == [("perro_sencillo", 1, set())]
    assert chat.conversation.clarifications == ()


async def test_required_choice_is_asked_and_answered() -> None:
    chat = Chat(BURGER)
    reply = await chat.say("una malteada")
    assert "¿Qué sabor quieres para Malteada?" in reply
    await chat.say("fresa")
    assert chat.lines == [("malteada", 1, {"sabor_fresa"})]


async def test_unknown_product_is_reported_not_added() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sushi")]]))
    assert "No encontré “sushi” en el menú" in await chat.say("un sushi")
    assert chat.lines == []


async def test_upsell_is_offered_once() -> None:
    chat = Chat(BURGER)
    assert "Le sumamos" in await chat.say("una sencilla")
    assert "Le sumamos" not in await chat.say("una doble")


async def test_buttons_degrade_and_a_number_answers_them() -> None:
    chat = Chat(BURGER, capabilities=ChannelCapabilities(max_buttons=0))
    await chat.say("una malteada")
    assert "2. chocolate" in chat.reply.text
    await chat.say("2")
    assert chat.lines == [("malteada", 1, {"sabor_chocolate"})]


async def test_a_duplicate_delivery_changes_nothing() -> None:
    chat = Chat(BURGER)
    message = chat.message("una sencilla", external_id="same")
    await chat.send(message)
    assert await chat.send(message) == ""
    assert chat.last is not None and chat.last.duplicate
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]


async def test_a_failed_turn_leaves_nothing_behind_and_can_be_retried() -> None:
    chat = Chat(BURGER, ScriptedLLM())
    message = chat.message("una sencilla", external_id="same")
    with pytest.raises(AssertionError):
        await chat.send(message)
    assert ("test", "chat") not in chat.repository.conversations
    assert await chat.repository.pending_outbound() == []


async def test_a_repeated_yes_after_confirming_does_not_start_over() -> None:
    chat = Chat(MINIMAL)
    await _to_confirmation(chat, SCENARIOS[1])
    await chat.say("sí")
    assert "ya está confirmado" in await chat.say("sí")
    assert chat.state is S.CONFIRMED


async def test_while_staff_decide_the_customer_is_told_to_wait_and_not_escalated() -> None:
    chat = Chat(BURGER)
    await _to_confirmation(chat, SCENARIOS[0])
    await chat.say("sí")
    for text in ("ok", "asdf qwer", "zxcv uiop"):
        assert "confirmación" in await chat.say(text)
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


async def test_a_change_while_staff_decide_goes_back_to_the_summary() -> None:
    chat = Chat(BURGER)
    await _to_confirmation(chat, SCENARIOS[0])
    await chat.say("sí")
    await chat.say("una doble")
    assert chat.state is S.AWAITING_CONFIRMATION


async def test_an_unclear_answer_to_the_address_question_is_the_address() -> None:
    chat = Chat(BURGER)
    await chat.say("una doble")
    await chat.say("no")
    await chat.say("domicilio")
    await chat.say("donde doña Marta frente al parque")
    cart = chat.conversation.cart
    assert cart.address is not None and cart.address.text.startswith("donde doña Marta")


@both
async def test_the_conversation_survives_serialization(s: Scenario) -> None:
    chat = Chat(s.config)
    await chat.say("hola")
    await _to_confirmation(chat, s)
    restored = Conversation.model_validate_json(chat.conversation.model_dump_json())
    assert restored == chat.conversation
