"""What the bot asks next, from the sample configs."""

from pathlib import Path

from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.domain.cart import (
    Cart,
    Fulfillment,
    ModifierChoice,
    add_item,
    set_address,
    set_fulfillment,
    set_payment,
)
from pedidos_bot.engine.policy import Step, next_step, upsell_categories

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "restaurants"
BURGER = load_restaurant("burger_demo", CONFIG_DIR)
MINIMAL = load_restaurant("minimal_demo", CONFIG_DIR)


def _with_burger() -> Cart:
    return add_item(Cart(), BURGER, "hamburguesa_sencilla")[0]


def test_empty_cart_asks_for_items() -> None:
    assert next_step(Cart(), BURGER).step is Step.ITEMS


def test_required_choice_comes_before_anything_else() -> None:
    cart = add_item(Cart(), BURGER, "malteada")[0]
    step = next_step(cart, BURGER)
    assert step.step is Step.REQUIRED_CHOICE
    assert step.choice is not None and step.choice.group_id == "sabor_malteada"


def test_anything_else_offers_upsell_only_once() -> None:
    cart = _with_burger()
    first = next_step(cart, BURGER)
    assert first.step is Step.ANYTHING_ELSE
    assert first.upsell == ("acompanantes", "bebidas", "malteadas")
    assert next_step(cart, BURGER, upsell_offered=True).upsell == ()


def test_no_upsell_when_the_cart_already_has_a_suggested_category() -> None:
    cart = add_item(_with_burger(), BURGER, "agua")[0]
    assert upsell_categories(cart, BURGER) == ()


def test_no_upsell_when_nothing_triggers_it() -> None:
    assert upsell_categories(add_item(Cart(), BURGER, "agua")[0], BURGER) == ()


def test_the_full_order_of_questions() -> None:
    cart = _with_burger()
    assert next_step(cart, BURGER, items_done=True).step is Step.FULFILLMENT
    cart = set_fulfillment(cart, BURGER, Fulfillment.DELIVERY)
    assert next_step(cart, BURGER, items_done=True).step is Step.ADDRESS
    cart = set_address(cart, "Calle 1")
    assert next_step(cart, BURGER, items_done=True).step is Step.PAYMENT
    cart = set_payment(cart, BURGER, "transfer")
    assert next_step(cart, BURGER, items_done=True).step is Step.CONFIRM


def test_pickup_skips_the_address() -> None:
    cart = set_fulfillment(_with_burger(), BURGER, Fulfillment.PICKUP)
    assert next_step(cart, BURGER, items_done=True).step is Step.PAYMENT


def test_cash_change_is_asked_once() -> None:
    cart = set_fulfillment(_with_burger(), BURGER, Fulfillment.PICKUP)
    cart = set_payment(cart, BURGER, "cash")
    assert next_step(cart, BURGER, items_done=True).step is Step.CASH_CHANGE
    assert next_step(cart, BURGER, items_done=True, change_asked=True).step is Step.CONFIRM
    paid = set_payment(cart, BURGER, "cash", cash_change_for=50000)
    assert next_step(paid, BURGER, items_done=True).step is Step.CONFIRM


def test_a_single_fulfillment_mode_is_never_asked() -> None:
    cart = add_item(Cart(), MINIMAL, "limonada")[0]
    assert next_step(cart, MINIMAL, items_done=True).step is Step.PAYMENT


def test_a_new_item_with_a_required_choice_interrupts_a_finished_order() -> None:
    cart = set_fulfillment(_with_burger(), BURGER, Fulfillment.PICKUP)
    cart = set_payment(cart, BURGER, "transfer")
    choice = ModifierChoice(group_id="tamano_papas", option_id="papas_personal")
    assert next_step(cart, BURGER, items_done=True).step is Step.CONFIRM
    with_fries, line = add_item(cart, BURGER, "papas_fritas")
    assert next_step(with_fries, BURGER, items_done=True).step is Step.REQUIRED_CHOICE
    done = add_item(cart, BURGER, "papas_fritas", modifiers=[choice])[0]
    assert next_step(done, BURGER, items_done=True).step is Step.CONFIRM
    assert line.product_id == "papas_fritas"
