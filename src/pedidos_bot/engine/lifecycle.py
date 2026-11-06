"""The order lifecycle. The stage of an order in progress is not here: it derives from the cart.

Every legal move is a row of `TRANSITIONS`; anything else raises, so an invalid state can't
be reached by accident.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class LifecycleState(StrEnum):
    ORDERING = "ordering"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PENDING_STAFF_CONFIRMATION = "pending_staff_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    HANDOFF = "handoff"
    EXPIRED = "expired"


class Event(StrEnum):
    ORDER_COMPLETE = "order_complete"
    CHANGE_REQUESTED = "change_requested"
    CUSTOMER_CONFIRMED = "customer_confirmed"
    SENT_TO_STAFF = "sent_to_staff"
    STAFF_APPROVED = "staff_approved"
    STAFF_REJECTED = "staff_rejected"
    CANCEL = "cancel"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_ENDED = "handoff_ended"
    EXPIRE = "expire"


_S, _E = LifecycleState, Event

TRANSITIONS: dict[tuple[LifecycleState, Event], LifecycleState] = {
    (_S.ORDERING, _E.ORDER_COMPLETE): _S.AWAITING_CONFIRMATION,
    (_S.ORDERING, _E.CANCEL): _S.CANCELLED,
    (_S.ORDERING, _E.HANDOFF_REQUESTED): _S.HANDOFF,
    (_S.ORDERING, _E.EXPIRE): _S.EXPIRED,
    (_S.AWAITING_CONFIRMATION, _E.CUSTOMER_CONFIRMED): _S.CONFIRMED,
    (_S.AWAITING_CONFIRMATION, _E.SENT_TO_STAFF): _S.PENDING_STAFF_CONFIRMATION,
    (_S.AWAITING_CONFIRMATION, _E.CHANGE_REQUESTED): _S.ORDERING,
    (_S.AWAITING_CONFIRMATION, _E.CANCEL): _S.CANCELLED,
    (_S.AWAITING_CONFIRMATION, _E.HANDOFF_REQUESTED): _S.HANDOFF,
    (_S.AWAITING_CONFIRMATION, _E.EXPIRE): _S.EXPIRED,
    (_S.PENDING_STAFF_CONFIRMATION, _E.STAFF_APPROVED): _S.CONFIRMED,
    (_S.PENDING_STAFF_CONFIRMATION, _E.STAFF_REJECTED): _S.ORDERING,
    (_S.PENDING_STAFF_CONFIRMATION, _E.CHANGE_REQUESTED): _S.ORDERING,
    (_S.PENDING_STAFF_CONFIRMATION, _E.CANCEL): _S.CANCELLED,
    (_S.PENDING_STAFF_CONFIRMATION, _E.HANDOFF_REQUESTED): _S.HANDOFF,
    (_S.HANDOFF, _E.CANCEL): _S.CANCELLED,
}
"""`HANDOFF_ENDED` is not here: it returns to whatever state the handoff interrupted."""

TERMINAL_STATES = frozenset({_S.CONFIRMED, _S.CANCELLED, _S.EXPIRED})


class InvalidTransitionError(Exception):
    def __init__(self, state: LifecycleState, event: Event) -> None:
        super().__init__(f"{event} is not allowed in {state}")


class Lifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: LifecycleState = LifecycleState.ORDERING
    before_handoff: LifecycleState | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def can(self, event: Event) -> bool:
        if event is Event.HANDOFF_ENDED:
            return self.state is LifecycleState.HANDOFF
        return (self.state, event) in TRANSITIONS

    def apply(self, event: Event) -> Lifecycle:
        if not self.can(event):
            raise InvalidTransitionError(self.state, event)
        if event is Event.HANDOFF_ENDED:
            assert self.before_handoff is not None
            return Lifecycle(state=self.before_handoff)
        before = self.state if event is Event.HANDOFF_REQUESTED else None
        return Lifecycle(state=TRANSITIONS[self.state, event], before_handoff=before)
