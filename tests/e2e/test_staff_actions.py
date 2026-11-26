"""What staff actions and the handoff check change, return, send and log.

Each test here pins a behavior that mutation testing showed no test was guarding.
"""

from datetime import timedelta

from structlog.testing import capture_logs

from pedidos_bot.channels.base import ChannelCapabilities
from pedidos_bot.domain.order import OrderSource, create_order
from pedidos_bot.engine.lifecycle import Event, Lifecycle
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.engine.staff import StaffDecision, StaffReply, Verdict
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")  # shadow mode, handoff_timeout_minutes: 30
TO_CONFIRM = ("unos tenders", "no", "Ana", "3001234567", "para recoger", "nequi")


async def _sent_to_staff(chat: Chat) -> None:
    for text in (*TO_CONFIRM, "sí"):
        await chat.say(text)
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


async def _with_a_person(chat: Chat) -> str:
    await chat.say("unos tenders")
    await chat.say("asesor")
    assert chat.conversation.handoff is not None
    return chat.conversation.handoff.code


# --- decisions --------------------------------------------------------------------------------


async def test_a_decision_says_whether_it_changed_anything() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    await chat.tap("Aprobar")
    assert chat.applied is True
    await chat.tap("Rechazar")
    assert chat.applied is False


async def test_an_approval_clears_the_question_that_was_pending() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    await chat.tap("Aprobar")
    conversation = chat.conversation
    assert (conversation.asked, conversation.asked_choice, conversation.options) == (None, None, ())


async def test_a_decision_sends_each_message_under_its_own_key() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    await chat.tap("Aprobar")
    customer, staff = chat.channel.sent[-2:]
    assert (customer.chat_ref, staff.chat_ref) == ("chat", "staff")
    assert customer.dedupe_key.startswith("test:")
    assert staff.dedupe_key == f"{customer.dedupe_key}:staff"


async def test_a_decision_on_an_order_that_came_from_no_chat_changes_nothing() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    cart = chat.conversation.cart
    elsewhere = create_order(cart, BURGER, source=OrderSource.CHAT, idempotency_key="phone-1")
    async with chat.repository.turn(chat.message("x", external_id="setup")) as scope:
        assert scope is not None
        scope.place_order(elsewhere)
    decision = StaffDecision(
        external_id="77", order_key="phone-1", revision=1, verdict=Verdict.APPROVE, at=chat._now
    )
    assert await chat.service.handle_staff(decision) is False


async def test_decisions_are_logged() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    with capture_logs() as logs:
        await chat.tap("Aprobar")
        await chat.tap("Rechazar")
    applied, stale = (log for log in logs if log["event"] == "staff_decision")
    assert applied == {
        "event": "staff_decision",
        "log_level": "info",
        "turn_id": applied["turn_id"],
        "verdict": "ok",
        "order_id": order.id,
        "applied": True,
        "state_after": S.CONFIRMED,
    }
    assert applied["turn_id"].startswith("staff:")
    assert (stale["verdict"], stale["order_id"], stale["applied"]) == ("no", order.id, False)


# --- relay ------------------------------------------------------------------------------------


async def test_staff_messages_say_whether_they_reached_the_customer() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    await chat.staff_says(f"/responder {code} hola")
    assert chat.applied is True
    await chat.staff_says(f"/devolver {code}")
    assert chat.applied is True
    await chat.staff_says(f"/responder {code} ¿sigues?")
    assert chat.applied is False
    nobody = "FFFF" if code != "FFFF" else "EEEE"
    await chat.staff_says(f"/responder {nobody} hola")
    assert chat.applied is False


async def test_a_reply_goes_out_under_its_own_key() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    await chat.staff_says(f"/responder {code} hola")
    sent = chat.channel.sent[-1]
    assert (sent.chat_ref, sent.message.text) == ("chat", "hola")
    assert sent.dedupe_key.startswith("test:")


async def test_a_reply_to_a_chat_left_in_handoff_without_details_reaches_nobody() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    stuck = chat.conversation.model_copy(update={"handoff": None})
    chat.repository.conversations[("test", "chat")] = stuck  # type: ignore[attr-defined]
    assert await chat.staff_says(f"/responder {code} hola") == ""
    assert "ya no está con una persona" in chat.staff[-1].text


async def test_staff_messages_are_logged() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    with capture_logs() as logs:
        await chat.staff_says(f"/devolver {code}")
    [log] = [log for log in logs if log["event"] == "staff_message"]
    assert (log["code"], log["applied"], log["returned"]) == (code, True, True)


async def test_giving_back_returns_to_where_the_handoff_began() -> None:
    chat = Chat(BURGER)
    for text in TO_CONFIRM:
        await chat.say(text)
    assert chat.state is S.AWAITING_CONFIRMATION
    await chat.say("asesor")
    code = chat.conversation.code
    await chat.staff_says(f"/devolver {code}")
    assert chat.state is S.AWAITING_CONFIRMATION


async def test_a_handoff_with_no_state_to_return_to_goes_back_to_ordering() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    lost = chat.conversation.model_copy(update={"lifecycle": Lifecycle(state=S.HANDOFF)})
    chat.repository.conversations[("test", "chat")] = lost  # type: ignore[attr-defined]
    await chat.staff_says(f"/devolver {code}")
    assert chat.state is S.ORDERING


# --- nobody answers ---------------------------------------------------------------------------


async def test_a_handoff_expires_at_exactly_the_timeout() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=30)
    assert "No pudimos atenderte a tiempo" in await chat.tick()
    assert chat.applied == 1


async def test_the_check_counts_what_it_expired() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=5)
    await chat.tick()
    assert chat.applied == 0


async def test_one_chat_still_waiting_does_not_stop_the_others_from_expiring() -> None:
    early, late = (
        Chat(BURGER, chat_ref="early"),
        Chat(BURGER, chat_ref="late", first_message_id=500),
    )
    late.repository = early.repository
    late.service = early.service
    await _with_a_person(early)
    late.wait(minutes=20)
    await _with_a_person(late)
    early.wait(minutes=31)  # early expired; late has waited 11 minutes
    assert await early.service.expire_handoffs(early._now) == 1
    assert (await early.repository.load("test", "early")).handoff is None  # type: ignore[union-attr]
    assert (await early.repository.load("test", "late")).handoff is not None  # type: ignore[union-attr]


async def test_the_same_chat_can_expire_twice() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=31)
    await chat.tick()
    await chat.say("asesor")
    chat.wait(minutes=31)
    assert "No pudimos atenderte a tiempo" in await chat.tick()
    assert chat.state is S.ORDERING


async def test_a_chat_left_in_handoff_without_details_expires_at_the_check() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    stuck = chat.conversation.model_copy(update={"handoff": None})
    chat.repository.conversations[("test", "chat")] = stuck  # type: ignore[attr-defined]
    assert "No pudimos atenderte a tiempo" in await chat.tick()


async def test_the_timeout_message_goes_out_under_its_own_key() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    chat.wait(minutes=31)
    await chat.tick()
    customer, staff = chat.channel.sent[-2:]
    assert customer.chat_ref == "chat" and customer.dedupe_key.startswith("test:handoff-timeout:")
    assert staff.message.text.startswith(f"⏱️ #{code}: nadie respondió en 30 minutos")


async def test_a_late_customer_message_also_tells_the_staff() -> None:
    chat = Chat(BURGER)
    code = await _with_a_person(chat)
    chat.wait(minutes=45)
    await chat.say("hola")
    assert chat.staff[-1].text.startswith(f"⏱️ #{code}: nadie respondió en 30 minutos")


async def test_expiring_is_logged() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    chat.wait(minutes=31)
    with capture_logs() as logs:
        await chat.tick()
    assert [log["event"] for log in logs] == ["handoff_expired"]
    assert logs[0]["conversation_id"] == chat.conversation.id


async def test_handing_back_and_forth_keeps_the_order() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    assert chat.conversation.lifecycle.can(Event.HANDOFF_ENDED)
    await chat.say("volver")
    assert chat.lines == [("chicken_tenders", 1, set())]


async def test_resuming_lists_every_item() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    await chat.say("una corona")
    chat.wait(hours=3)
    assert "1 x Chicken Tenders y 1 x Corona" in await chat.say("hola")


# --- second pass, after the scoped re-run -----------------------------------------------------


async def test_the_same_decision_delivered_twice_applies_once() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    decision = StaffDecision(
        external_id="77", order_key=order.idempotency_key, revision=1, verdict=Verdict.APPROVE,
        at=chat._now,
    )  # fmt: skip
    with capture_logs() as logs:
        assert await chat.service.handle_staff(decision) is True
        assert await chat.service.handle_staff(decision) is False
    assert "duplicate_message" in [log["event"] for log in logs]


async def test_the_same_reply_delivered_twice_applies_once() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    reply = StaffReply(external_id="88", code=chat.conversation.code, text="hola", at=chat._now)
    with capture_logs() as logs:
        assert await chat.service.handle_staff(reply) is True
        assert await chat.service.handle_staff(reply) is False
    assert "duplicate_message" in [log["event"] for log in logs]


async def test_notices_about_doing_nothing_name_the_order_or_customer() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    code = chat.conversation.code
    await chat.tap("Aprobar")
    await chat.tap("Rechazar")
    assert chat.staff[-1].text == f"El pedido {code} ya no está pendiente: no cambié nada."
    await chat.say("asesor")
    handoff = chat.conversation.code
    await chat.staff_says(f"/devolver {handoff}")
    await chat.staff_says(f"/responder {handoff} hola")
    assert chat.staff[-1].text == f"#{handoff} ya no está con una persona: no le envié nada."


async def test_actions_that_find_nothing_are_logged() -> None:
    chat = Chat(BURGER)
    await _with_a_person(chat)
    code = chat.conversation.code
    nobody = "FFFF" if code != "FFFF" else "EEEE"
    missing = StaffDecision(
        external_id="90", order_key="nope", revision=1, verdict=Verdict.REJECT, at=chat._now
    )
    with capture_logs() as logs:
        await chat.service.handle_staff(missing)
        await chat.staff_says(f"/responder {nobody} hola")
        await chat.staff_says(f"/devolver {code}")
        await chat.staff_says(f"/responder {code} ¿sigues?")
    events = [(log["event"], log.get("applied")) for log in logs]
    assert ("staff_decision_without_order", None) in events
    assert ("staff_message_without_conversation", None) in events
    assert events[-1] == ("staff_message", False)
    assert all(log["turn_id"].startswith("staff:") for log in logs)


async def test_waiting_chats_first_in_line_do_not_stop_the_check() -> None:
    waiting = Chat(BURGER, chat_ref="waiting")
    expired = Chat(BURGER, chat_ref="expired", first_message_id=500)
    other = Chat(BURGER, chat_ref="other", first_message_id=900)
    for chat in (expired, other):
        chat.repository, chat.service = waiting.repository, waiting.service
    waiting.wait(minutes=20)
    await _with_a_person(waiting)  # first in the repository, handed off later
    await _with_a_person(expired)
    await _with_a_person(other)
    now = expired._now + timedelta(minutes=31)
    assert await waiting.service.expire_handoffs(now) == 2
    assert (await waiting.repository.load("test", "waiting")).handoff is not None  # type: ignore[union-attr]


async def test_giving_back_while_staff_decide_keeps_the_order_waiting() -> None:
    chat = Chat(BURGER)
    await _sent_to_staff(chat)
    await chat.say("asesor")
    await chat.staff_says(f"/devolver {chat.conversation.code}")
    assert chat.state is S.PENDING_STAFF_CONFIRMATION


async def test_a_yes_to_see_the_menu_is_not_a_misunderstanding() -> None:
    chat = Chat(BURGER)
    await chat.say("una pizza")
    await chat.say("sí")
    assert chat.conversation.misunderstood == 0


async def test_resuming_answers_a_numbered_yes() -> None:
    chat = Chat(BURGER, capabilities=ChannelCapabilities())  # no buttons: options are numbered
    await chat.say("unos tenders")
    chat.wait(hours=3)
    assert "1. Sí" in await chat.say("hola")
    assert "entendí" not in await chat.say("1")
    assert chat.conversation.resume_offer is None
    assert chat.lines == [("chicken_tenders", 1, set())]


async def test_a_recovered_order_replays_what_the_customer_came_back_with() -> None:
    chat = Chat(BURGER)
    await chat.say("unos tenders")
    chat.wait(hours=30)
    assert "¿Lo retomamos?" in await chat.say("una corona")
    await chat.say("sí")
    assert [line[0] for line in chat.lines] == ["chicken_tenders", "corona"]


async def test_a_recovered_order_picks_up_where_it_was() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger"):
        await chat.say(text)
    chat.wait(hours=30)
    await chat.say("hola")
    reply = await chat.say("sí")
    assert "¿Cómo vas a pagar?" in reply  # not "anything else?" again, nor a greeting
    assert BURGER.tone.greeting not in reply
    conversation = chat.conversation
    assert (conversation.greeted, conversation.items_done, conversation.upsell_offered) == (
        True, True, True,
    )  # fmt: skip


async def test_a_recovered_order_does_not_ask_for_cash_again() -> None:
    chat = Chat(BURGER)
    for text in ("unos tenders", "no", "Ana", "3001234567", "para recoger", "efectivo", "exacto"):
        await chat.say(text)
    chat.wait(hours=30)
    await chat.say("hola")
    assert (await chat.say("sí")).startswith("Este es tu pedido:")
