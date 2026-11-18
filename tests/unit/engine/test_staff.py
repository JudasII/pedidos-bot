"""Reading what staff do, and the lifecycle rules for their decisions."""

import pytest

from pedidos_bot.channels.base import MessageKind
from pedidos_bot.engine.lifecycle import Event, Lifecycle
from pedidos_bot.engine.lifecycle import LifecycleState as S
from pedidos_bot.engine.staff import Verdict, parse_staff
from tests.factories import make_inbound


def _tap(payload: str | None) -> object:
    return parse_staff(make_inbound(kind=MessageKind.INTERACTIVE_REPLY, reply_payload=payload))


def test_a_button_payload_becomes_a_decision() -> None:
    decision = parse_staff(
        make_inbound(external_id="77", kind=MessageKind.INTERACTIVE_REPLY, reply_payload="no:abc:3")
    )
    assert decision is not None
    assert (decision.external_id, decision.order_key, decision.revision) == ("77", "abc", 3)
    assert decision.verdict is Verdict.REJECT


@pytest.mark.parametrize("payload", [None, "", "ok", "ok:abc", "ok::2", "maybe:abc:2", "ok:abc:x"])
def test_anything_else_is_not_a_decision(payload: str | None) -> None:
    assert _tap(payload) is None


def test_plain_text_in_the_staff_chat_is_not_a_decision() -> None:
    assert parse_staff(make_inbound(text="ok:abc:1")) is None


@pytest.mark.parametrize(
    ("event", "after"), [(Event.STAFF_APPROVED, S.CONFIRMED), (Event.STAFF_REJECTED, S.ORDERING)]
)
def test_a_decision_during_a_handoff_keeps_the_handoff(event: Event, after: S) -> None:
    waiting = Lifecycle(state=S.PENDING_STAFF_CONFIRMATION)
    decided = waiting.apply(Event.HANDOFF_REQUESTED).apply(event)
    assert (decided.state, decided.before_handoff) == (S.HANDOFF, after)
    assert decided.apply(Event.HANDOFF_ENDED).state is after


def test_a_handoff_from_ordering_has_nothing_to_decide() -> None:
    handoff = Lifecycle().apply(Event.HANDOFF_REQUESTED)
    assert not handoff.can(Event.STAFF_APPROVED)
