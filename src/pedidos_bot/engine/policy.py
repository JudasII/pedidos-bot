"""Decides what to ask next. A pure function of the cart: there is no stage to get out of sync."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pedidos_bot.config.schema import RestaurantConfig, UpsellRule
from pedidos_bot.domain.cart import Cart, MissingChoice, Pending, missing_choices, pending


class Step(StrEnum):
    ITEMS = "items"
    REQUIRED_CHOICE = "required_choice"
    ANYTHING_ELSE = "anything_else"
    FULFILLMENT = "fulfillment"
    ADDRESS = "address"
    PAYMENT = "payment"
    CASH_CHANGE = "cash_change"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class NextStep:
    step: Step
    choice: MissingChoice | None = None
    """For `REQUIRED_CHOICE`: the line and group to ask about."""
    upsell: tuple[str, ...] = ()
    """For `ANYTHING_ELSE`: ids of the categories to suggest."""


def upsell_categories(cart: Cart, config: RestaurantConfig) -> tuple[str, ...]:
    """Categories to suggest, per the rules: the cart triggers them and has none of them."""
    menu = config.menu
    in_cart = {c.id for line in cart.lines if (c := menu.category_of(line.product_id))}
    suggested: list[str] = []
    for rule in config.rules:
        if (
            isinstance(rule, UpsellRule)
            and in_cart & set(rule.after_categories)
            and not in_cart & set(rule.suggest_categories)
        ):
            suggested.extend(rule.suggest_categories)
    return tuple(dict.fromkeys(suggested))


def next_step(
    cart: Cart,
    config: RestaurantConfig,
    *,
    items_done: bool = False,
    upsell_offered: bool = False,
    change_asked: bool = False,
) -> NextStep:
    """The next thing to ask, or `CONFIRM` when the order is complete.

    `items_done` means the customer finished adding items. `upsell_offered` and `change_asked`
    record questions that are only asked once.
    """
    slots = pending(cart, config)
    if Pending.ITEMS in slots:
        return NextStep(Step.ITEMS)
    if Pending.REQUIRED_CHOICES in slots:
        return NextStep(Step.REQUIRED_CHOICE, choice=missing_choices(cart, config)[0])
    if not items_done:
        offer = () if upsell_offered else upsell_categories(cart, config)
        return NextStep(Step.ANYTHING_ELSE, upsell=offer)
    if Pending.FULFILLMENT in slots:
        return NextStep(Step.FULFILLMENT)
    if Pending.ADDRESS in slots:
        return NextStep(Step.ADDRESS)
    if Pending.PAYMENT in slots:
        return NextStep(Step.PAYMENT)
    assert cart.payment is not None
    method = config.payment_method(cart.payment.method_id)
    wants_change = method is not None and method.ask_change_for
    if wants_change and cart.payment.cash_change_for is None and not change_asked:
        return NextStep(Step.CASH_CHANGE)
    return NextStep(Step.CONFIRM)
