import pytest

from pedidos_bot.domain.cart import (
    Cart,
    Fulfillment,
    FulfillmentUnavailableError,
    InvalidQuantityError,
    LineNotFoundError,
    ModifierChoice,
    ModifierNotAllowedError,
    Pending,
    ProductUnavailableError,
    TooManySelectionsError,
    UnknownPaymentMethodError,
    UnknownProductError,
    add_item,
    missing_choices,
    pending,
    remove_line,
    set_address,
    set_fulfillment,
    set_payment,
    totals,
    update_line,
)
from tests.factories import config_dict, make_config

CONFIG = make_config()
NO_ONION = ModifierChoice(group_id="remove", option_id="no_onion")
BACON = ModifierChoice(group_id="extras", option_id="bacon")
CHEESE = ModifierChoice(group_id="extras", option_id="cheese")
EGG = ModifierChoice(group_id="extras", option_id="egg")
SMALL = ModifierChoice(group_id="size", option_id="small")
LARGE = ModifierChoice(group_id="size", option_id="large")


def test_add_item_prices_from_config() -> None:
    cart, line = add_item(Cart(), CONFIG, "classic", qty=2, modifiers=[BACON])
    assert line.unit_price == 18000 + 4000
    assert line.line_total == 44000
    assert totals(cart, CONFIG).subtotal == 44000


def test_split_quantities_become_distinct_lines() -> None:
    # "2 clásicas, una sin cebolla"
    cart, _ = add_item(Cart(), CONFIG, "classic", qty=1)
    cart, _ = add_item(cart, CONFIG, "classic", qty=1, modifiers=[NO_ONION])
    assert [(line.qty, line.modifiers) for line in cart.lines] == [(1, ()), (1, (NO_ONION,))]


def test_identical_items_are_merged() -> None:
    cart, _ = add_item(Cart(), CONFIG, "classic", modifiers=[BACON, NO_ONION])
    cart, line = add_item(cart, CONFIG, "classic", modifiers=[NO_ONION, BACON])
    assert len(cart.lines) == 1
    assert line.qty == 2


def test_unknown_and_unavailable_products() -> None:
    with pytest.raises(UnknownProductError):
        add_item(Cart(), CONFIG, "pizza")
    data = config_dict()
    data["categories"][0]["products"][0]["available"] = False
    with pytest.raises(ProductUnavailableError):
        add_item(Cart(), make_config(**data), "classic")


def test_modifier_must_belong_to_product() -> None:
    with pytest.raises(ModifierNotAllowedError, match="does not apply"):
        add_item(Cart(), CONFIG, "soda", modifiers=[NO_ONION])
    with pytest.raises(ModifierNotAllowedError, match="does not exist"):
        add_item(
            Cart(), CONFIG, "classic", modifiers=[ModifierChoice(group_id="extras", option_id="x")]
        )


def test_max_selections_per_group() -> None:
    with pytest.raises(TooManySelectionsError):
        add_item(Cart(), CONFIG, "classic", modifiers=[BACON, CHEESE, EGG])
    with pytest.raises(TooManySelectionsError):
        add_item(Cart(), CONFIG, "soda", modifiers=[SMALL, LARGE])


@pytest.mark.parametrize("qty", [0, -1, 11])
def test_quantity_limits(qty: int) -> None:
    with pytest.raises(InvalidQuantityError):
        add_item(Cart(), CONFIG, "classic", qty=qty)


def test_merging_respects_quantity_limit() -> None:
    cart, _ = add_item(Cart(), CONFIG, "classic", qty=6)
    with pytest.raises(InvalidQuantityError):
        add_item(cart, CONFIG, "classic", qty=5)


def test_required_choice_is_reported_missing() -> None:
    cart, line = add_item(Cart(), CONFIG, "soda")
    missing = missing_choices(cart, CONFIG)
    assert [(m.line_id, m.group_id) for m in missing] == [(line.line_id, "size")]
    cart = update_line(cart, CONFIG, line.line_id, add_modifiers=[LARGE])
    assert missing_choices(cart, CONFIG) == []
    assert cart.lines[0].unit_price == 4000 + 3000


def test_choose_one_update_replaces_previous_choice() -> None:
    cart, line = add_item(Cart(), CONFIG, "soda", modifiers=[SMALL])
    cart = update_line(cart, CONFIG, line.line_id, add_modifiers=[LARGE])
    assert cart.lines[0].modifiers == (LARGE,)


def test_update_line_qty_note_and_remove_modifier() -> None:
    cart, line = add_item(Cart(), CONFIG, "classic", modifiers=[BACON, NO_ONION])
    cart = update_line(
        cart, CONFIG, line.line_id, qty=3, remove_modifiers=[BACON], note=" bien asada "
    )
    updated = cart.lines[0]
    assert (updated.qty, updated.modifiers, updated.note) == (3, (NO_ONION,), "bien asada")
    assert updated.unit_price == 18000


def test_remove_line() -> None:
    cart, first = add_item(Cart(), CONFIG, "classic")
    cart, _ = add_item(cart, CONFIG, "soda", modifiers=[SMALL])
    cart = remove_line(cart, first.line_id)
    assert [line.product_id for line in cart.lines] == ["soda"]
    with pytest.raises(LineNotFoundError):
        remove_line(cart, first.line_id)


def test_line_ids_are_never_reused() -> None:
    cart, first = add_item(Cart(), CONFIG, "classic")
    cart = remove_line(cart, first.line_id)
    cart, second = add_item(cart, CONFIG, "classic")
    assert second.line_id != first.line_id


def test_delivery_fee_applies_only_to_delivery() -> None:
    cart, _ = add_item(Cart(), CONFIG, "classic")
    assert totals(set_fulfillment(cart, CONFIG, Fulfillment.DELIVERY), CONFIG).total == 23000
    assert totals(set_fulfillment(cart, CONFIG, Fulfillment.PICKUP), CONFIG).total == 18000


def test_empty_cart_has_no_delivery_fee() -> None:
    cart = set_fulfillment(Cart(), CONFIG, Fulfillment.DELIVERY)
    assert totals(cart, CONFIG).total == 0


def test_disabled_fulfillment_is_rejected() -> None:
    config = make_config(
        fulfillment={"pickup": {"enabled": True, "eta_minutes": {"min": 5, "max": 10}}}
    )
    with pytest.raises(FulfillmentUnavailableError):
        set_fulfillment(Cart(), config, Fulfillment.DELIVERY)


def test_payment_validation_and_change() -> None:
    cart = set_payment(Cart(), CONFIG, "cash", cash_change_for=50000)
    assert cart.payment is not None and cart.payment.cash_change_for == 50000
    cart = set_payment(Cart(), CONFIG, "transfer", cash_change_for=50000)
    assert cart.payment is not None and cart.payment.cash_change_for is None
    with pytest.raises(UnknownPaymentMethodError):
        set_payment(Cart(), CONFIG, "bitcoin")


def test_pending_slots_in_order() -> None:
    cart = Cart()
    assert pending(cart, CONFIG) == [Pending.ITEMS, Pending.FULFILLMENT, Pending.PAYMENT]
    cart, _ = add_item(cart, CONFIG, "soda")
    assert pending(cart, CONFIG)[0] is Pending.REQUIRED_CHOICES
    cart = update_line(cart, CONFIG, 1, add_modifiers=[SMALL])
    cart = set_fulfillment(cart, CONFIG, Fulfillment.DELIVERY)
    assert pending(cart, CONFIG) == [Pending.ADDRESS, Pending.PAYMENT]
    cart = set_address(cart, "Calle 10 # 5-20", "apto 301")
    cart = set_payment(cart, CONFIG, "cash")
    assert pending(cart, CONFIG) == []


def test_pickup_never_asks_for_address() -> None:
    cart, _ = add_item(Cart(), CONFIG, "classic")
    cart = set_payment(set_fulfillment(cart, CONFIG, Fulfillment.PICKUP), CONFIG, "cash")
    assert pending(cart, CONFIG) == []


def test_single_fulfillment_mode_is_implied() -> None:
    config = make_config(
        fulfillment={"pickup": {"enabled": True, "eta_minutes": {"min": 5, "max": 10}}}
    )
    cart, _ = add_item(Cart(), config, "classic")
    cart = set_payment(cart, config, "cash")
    assert pending(cart, config) == []
    assert totals(cart, config).delivery_fee == 0


def test_cart_is_immutable() -> None:
    cart = Cart()
    new_cart, _ = add_item(cart, CONFIG, "classic")
    assert cart.is_empty and not new_cart.is_empty
