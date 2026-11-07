"""The order entry point, used without any conversation."""

import pytest

from pedidos_bot.domain.cart import Cart, Fulfillment, add_item, set_fulfillment, set_payment
from pedidos_bot.domain.order import (
    IncompleteOrderError,
    OrderSource,
    OrderStatus,
    create_order,
)
from tests.factories import config_dict, make_config


def _cart() -> Cart:
    config = make_config()
    cart, _ = add_item(Cart(), config, "classic", qty=2)
    cart = set_fulfillment(cart, config, Fulfillment.PICKUP)
    return set_payment(cart, config, "transfer")


def test_an_order_needs_no_conversation() -> None:
    config = make_config()
    order = create_order(_cart(), config, source=OrderSource.CHAT, idempotency_key="key")
    assert order.conversation_id is None
    assert order.totals.total == order.cart.lines[0].line_total
    assert order.restaurant_id == config.restaurant.id


@pytest.mark.parametrize(
    ("shadow", "status"),
    [(True, OrderStatus.PENDING_VALIDATION), (False, OrderStatus.CONFIRMED)],
)
def test_shadow_mode_decides_the_status(shadow: bool, status: OrderStatus) -> None:
    data = config_dict()
    data["modes"] = {"shadow": shadow}
    order = create_order(
        _cart(), make_config(**data), source=OrderSource.CHAT, idempotency_key="key"
    )
    assert order.status is status


def test_an_incomplete_cart_is_refused() -> None:
    with pytest.raises(IncompleteOrderError, match="payment"):
        create_order(
            set_fulfillment(
                add_item(Cart(), make_config(), "classic")[0], make_config(), Fulfillment.PICKUP
            ),
            make_config(),
            source=OrderSource.CHAT,
            idempotency_key="key",
        )
