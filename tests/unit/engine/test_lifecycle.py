"""Every (state, event) pair, so an illegal move can't slip in unnoticed."""

from itertools import product

import pytest

from pedidos_bot.engine.lifecycle import (
    TERMINAL_STATES,
    Event,
    InvalidTransitionError,
    Lifecycle,
    LifecycleState,
)

S, E = LifecycleState, Event

LEGAL = {
    (S.ORDERING, E.ORDER_COMPLETE): S.AWAITING_CONFIRMATION,
    (S.ORDERING, E.CANCEL): S.CANCELLED,
    (S.ORDERING, E.HANDOFF_REQUESTED): S.HANDOFF,
    (S.ORDERING, E.EXPIRE): S.EXPIRED,
    (S.AWAITING_CONFIRMATION, E.CUSTOMER_CONFIRMED): S.CONFIRMED,
    (S.AWAITING_CONFIRMATION, E.SENT_TO_STAFF): S.PENDING_STAFF_CONFIRMATION,
    (S.AWAITING_CONFIRMATION, E.CHANGE_REQUESTED): S.ORDERING,
    (S.AWAITING_CONFIRMATION, E.CANCEL): S.CANCELLED,
    (S.AWAITING_CONFIRMATION, E.HANDOFF_REQUESTED): S.HANDOFF,
    (S.AWAITING_CONFIRMATION, E.EXPIRE): S.EXPIRED,
    (S.PENDING_STAFF_CONFIRMATION, E.STAFF_APPROVED): S.CONFIRMED,
    (S.PENDING_STAFF_CONFIRMATION, E.STAFF_REJECTED): S.ORDERING,
    (S.PENDING_STAFF_CONFIRMATION, E.CHANGE_REQUESTED): S.ORDERING,
    (S.PENDING_STAFF_CONFIRMATION, E.CANCEL): S.CANCELLED,
    (S.PENDING_STAFF_CONFIRMATION, E.HANDOFF_REQUESTED): S.HANDOFF,
    (S.HANDOFF, E.CANCEL): S.CANCELLED,
}
RETURNS_FROM_HANDOFF = (S.ORDERING, S.AWAITING_CONFIRMATION, S.PENDING_STAFF_CONFIRMATION)


@pytest.mark.parametrize(("state", "event"), list(product(S, E)))
def test_every_pair_is_either_legal_or_refused(state: LifecycleState, event: Event) -> None:
    lifecycle = Lifecycle(state=state, before_handoff=S.ORDERING if state is S.HANDOFF else None)
    if (state, event) in LEGAL:
        assert lifecycle.apply(event).state is LEGAL[state, event]
    elif state is S.HANDOFF and event is E.HANDOFF_ENDED:
        assert lifecycle.apply(event).state is S.ORDERING
    else:
        assert not lifecycle.can(event)
        with pytest.raises(InvalidTransitionError):
            lifecycle.apply(event)


@pytest.mark.parametrize("state", RETURNS_FROM_HANDOFF)
def test_handoff_returns_to_the_interrupted_state(state: LifecycleState) -> None:
    during = Lifecycle(state=state).apply(E.HANDOFF_REQUESTED)
    assert during.state is S.HANDOFF
    after = during.apply(E.HANDOFF_ENDED)
    assert after == Lifecycle(state=state)


@pytest.mark.parametrize("state", TERMINAL_STATES)
def test_terminal_states_have_no_way_out(state: LifecycleState) -> None:
    lifecycle = Lifecycle(state=state)
    assert lifecycle.is_terminal
    assert not any(lifecycle.can(event) for event in E)
