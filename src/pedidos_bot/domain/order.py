"""Orders: the one way a finished cart becomes something the restaurant acts on.

An order doesn't depend on where it came from (ADR-0004), so a conversation is optional.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import Field

from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import Cart, Totals, pending, totals
from pedidos_bot.models import FrozenModel


class OrderSource(StrEnum):
    CHAT = "chat"


class OrderStatus(StrEnum):
    PENDING_VALIDATION = "pending_validation"
    CONFIRMED = "confirmed"
    WITHDRAWN = "withdrawn"
    """The customer cancelled or changed it before staff decided: nobody must act on it."""


class Order(FrozenModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    idempotency_key: str
    """Saving an order with the same key revises it instead of creating another."""
    restaurant_id: str
    source: OrderSource
    conversation_id: str | None = None
    status: OrderStatus
    cart: Cart
    totals: Totals


class IncompleteOrderError(Exception):
    """The cart still has missing slots."""


def create_order(
    cart: Cart,
    config: RestaurantConfig,
    *,
    source: OrderSource,
    idempotency_key: str,
    conversation_id: str | None = None,
) -> Order:
    missing = pending(cart, config)
    if missing:
        raise IncompleteOrderError(f"cart is missing: {[str(slot) for slot in missing]}")
    shadow = config.modes.shadow
    return Order(
        idempotency_key=idempotency_key,
        restaurant_id=config.restaurant.id,
        source=source,
        conversation_id=conversation_id,
        status=OrderStatus.PENDING_VALIDATION if shadow else OrderStatus.CONFIRMED,
        cart=cart,
        totals=totals(cart, config),
    )
