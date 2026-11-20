"""Handing a customer to a person and back (#20). Wording is Spanish because people write it."""

import pytest
from structlog.testing import capture_logs

from pedidos_bot.channels.base import GeoPoint, MessageKind
from pedidos_bot.engine.lifecycle import LifecycleState as S
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")  # handoff_timeout_minutes: 30


async def _with_a_person(chat: Chat) -> str:
    """A customer with a burger in the cart asks for a person. Returns the handoff's code."""
    await chat.say("una sencilla")
    await chat.say("asesor")
    assert chat.state is S.HANDOFF
    assert chat.conversation.handoff is not None
    return chat.conversation.handoff.code


async def test_asking_for_a_person_tells_the_staff_chat() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    assert "alguien del equipo" in chat.reply.text
    notice = chat.staff[-1].text
    assert f"#{code}" in notice and "pide hablar con una persona" in notice
    assert "“asesor”" in notice  # what the customer said
    assert "1 x Hamburguesa Sencilla" in notice  # what they were ordering
    assert "Responde a este mensaje" in notice and f"/devolver {code}" in notice


async def test_what_the_customer_writes_goes_to_the_staff_chat() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    assert await chat.say("¿tienen hamburguesa vegana?") == ""
    assert chat.staff[-1].text == f"💬 #{code}: ¿tienen hamburguesa vegana?"


@pytest.mark.parametrize(
    ("kind", "shown"),
    [
        (MessageKind.VOICE, "[nota de voz]"),
        (MessageKind.IMAGE, "[foto]"),
        (MessageKind.STICKER, "[sticker]"),
    ],
)
async def test_what_the_bot_cannot_forward_is_named(kind: MessageKind, shown: str) -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    await chat.send(chat.message(None, kind))
    assert chat.staff[-1].text.startswith(f"💬 #{code}: {shown}")


async def test_a_location_reaches_the_staff_as_a_map_link() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    point = chat.message(None, MessageKind.LOCATION).model_copy(
        update={"location": GeoPoint(latitude=4.6, longitude=-74.1)}
    )
    await chat.send(point)
    assert chat.staff[-1].text == f"💬 #{code}: 📍 https://maps.google.com/?q=4.6,-74.1"


async def test_replying_to_the_notice_writes_to_the_customer() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    seen = await chat.staff_says("Hola, soy Carlos. ¿Te ayudo?", reply_to=chat.staff[-1])
    assert seen == "Hola, soy Carlos. ¿Te ayudo?"
    assert chat.state is S.HANDOFF


async def test_replying_to_a_forwarded_message_works_too() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    await chat.say("¿tienen vegana?")
    assert await chat.staff_says("No, lo siento", reply_to=chat.staff[-1]) == "No, lo siento"


async def test_the_responder_command_writes_to_the_customer() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    assert await chat.staff_says(f"/responder {code} Ya va tu pedido") == "Ya va tu pedido"
    assert await chat.staff_says(f"/responder@demo_bot {code.lower()} Listo") == "Listo"


async def test_devolver_returns_the_customer_to_the_bot_with_the_order_kept() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    seen = await chat.staff_says(f"/devolver {code}")
    assert "sigo yo" in seen and "¿Algo más?" in seen
    assert chat.state is S.ORDERING and chat.conversation.handoff is None
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]
    assert f"#{code} volvió al asistente" in chat.staff[-1].text
    assert "Anotado" in await chat.say("una coca cola personal")


async def test_devolver_as_a_reply_needs_no_code() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    await chat.staff_says("/devolver", reply_to=chat.staff[-1])
    assert chat.state is S.ORDERING


async def test_a_reply_after_the_customer_went_back_reaches_nobody() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    notice = chat.staff[-1]
    await chat.staff_says(f"/devolver {code}")
    assert await chat.staff_says("¿sigues ahí?", reply_to=notice) == ""
    assert "ya no está con una persona" in chat.staff[-1].text


async def test_chatter_in_the_staff_chat_is_ignored() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    sent = len(chat.channel.sent)
    assert await chat.staff_says("¿alguien vio las llaves?") == ""
    assert len(chat.channel.sent) == sent


# --- nobody answers -------------------------------------------------------------------------


async def test_when_nobody_answers_the_bot_takes_over_and_says_so() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    chat.wait(minutes=29)
    assert await chat.tick() == ""
    chat.wait(minutes=2)
    seen = await chat.tick()
    assert "No pudimos atenderte a tiempo" in seen and "¿Algo más?" in seen
    assert chat.state is S.ORDERING
    assert f"#{code}" in chat.staff[-1].text and "30 minutos" in chat.staff[-1].text
    assert await chat.tick() == ""  # once


async def test_a_customer_who_comes_back_late_is_answered_by_the_bot() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=45)  # the periodic check didn't run: the next message ends it
    seen = await chat.say("una coca cola personal")
    assert "No pudimos atenderte a tiempo" in seen and "Anotado: 1 x Coca-Cola" in seen
    assert chat.state is S.ORDERING


async def test_a_cashier_who_writes_keeps_the_handoff_alive() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=20)
    await chat.staff_says("Ya te atiendo", reply_to=chat.staff[-1])
    chat.wait(minutes=20)
    assert await chat.tick() == ""
    assert await chat.say("¿y entonces?") == ""
    assert chat.state is S.HANDOFF


async def test_a_handoff_saved_without_its_details_ends_at_the_next_message() -> None:
    """Chats left in handoff before the relay existed have no handoff details."""
    chat = Chat(BURGER)
    await _with_a_person(chat)
    stuck = chat.conversation.model_copy(update={"handoff": None})
    chat.repository.conversations[("test", "chat")] = stuck  # type: ignore[attr-defined]
    assert "Anotado" in await chat.say("una coca cola personal")
    assert chat.state is S.ORDERING


async def test_relayed_messages_are_masked_in_the_logs() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    with capture_logs() as logs:
        await chat.say("mi número es 3001234567")
    assert "3001234567" not in str(logs)


# --- the customer leaves ----------------------------------------------------------------------


async def test_the_handoff_message_offers_a_way_back() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    reply = chat.reply
    assert "Volver al asistente" in reply.text
    assert [button.title for button in reply.quick_replies] == ["Volver al asistente"]


@pytest.mark.parametrize("said", ["Volver al asistente", "volver"])
async def test_the_customer_can_go_back_to_the_bot(said: str) -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    seen = await chat.say(said)
    assert "sigo yo" in seen and "¿Algo más?" in seen
    assert chat.state is S.ORDERING and chat.conversation.handoff is None
    assert chat.lines == [("hamburguesa_sencilla", 1, set())]
    assert f"#{code} volvió al asistente por su cuenta" in chat.staff[-1].text


async def test_the_customer_can_cancel_from_the_handoff() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    seen = await chat.say("cancelar")
    assert "cancelé tu pedido" in seen
    assert chat.state is S.CANCELLED
    assert f"#{code} canceló" in chat.staff[-1].text


async def test_anything_else_still_goes_to_the_person() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    assert await chat.say("¿vuelven a abrir mañana?") == ""
    assert chat.staff[-1].text == f"💬 #{code}: ¿vuelven a abrir mañana?"
