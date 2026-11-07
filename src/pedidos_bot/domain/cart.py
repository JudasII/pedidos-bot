"""The cart: the single source of truth for an order in progress.

All operations are pure functions `(cart, config, ...) -> new cart` that validate against the
restaurant's configuration and raise `CartError` subclasses on invalid input. Prices are
always taken from the configuration; the LLM never supplies them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.menu import Menu, ModifierGroupType, Product


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fulfillment(StrEnum):
    DELIVERY = "delivery"
    PICKUP = "pickup"


class ModifierChoice(_Model):
    group_id: str
    option_id: str


class CartLine(_Model):
    line_id: int
    product_id: str
    qty: int = Field(ge=1)
    modifiers: tuple[ModifierChoice, ...] = ()
    note: str = ""
    unit_price: int = Field(ge=0)
    """Product price plus modifier prices, frozen from the config when the line was priced."""

    @property
    def line_total(self) -> int:
        return self.unit_price * self.qty

    def same_item_as(self, other: CartLine) -> bool:
        return (
            self.product_id == other.product_id
            and set(self.modifiers) == set(other.modifiers)
            and self.note == other.note
            and self.unit_price == other.unit_price
        )


class Address(_Model):
    text: str = Field(min_length=1)
    details: str = ""
    """Apartment, landmark, gate code, etc."""


class Payment(_Model):
    method_id: str
    cash_change_for: int | None = Field(default=None, ge=0)
    """Bill the customer will pay with, when the method asks for change."""


class Cart(_Model):
    lines: tuple[CartLine, ...] = ()
    next_line_id: int = 1
    fulfillment: Fulfillment | None = None
    address: Address | None = None
    payment: Payment | None = None

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def line(self, line_id: int) -> CartLine | None:
        return next((line for line in self.lines if line.line_id == line_id), None)


class Totals(_Model):
    subtotal: int
    delivery_fee: int
    total: int


class Pending(StrEnum):
    """What is still missing before the order can be confirmed, in the order to ask for it."""

    ITEMS = "items"
    REQUIRED_CHOICES = "required_choices"
    FULFILLMENT = "fulfillment"
    ADDRESS = "address"
    PAYMENT = "payment"


class MissingChoice(_Model):
    line_id: int
    product_id: str
    group_id: str


# --- errors -------------------------------------------------------------------------------


class CartError(Exception):
    code = "cart_error"


class UnknownProductError(CartError):
    code = "unknown_product"


class ProductUnavailableError(CartError):
    code = "product_unavailable"


class ModifierNotAllowedError(CartError):
    code = "modifier_not_allowed"


class TooManySelectionsError(CartError):
    code = "too_many_selections"


class InvalidQuantityError(CartError):
    code = "invalid_quantity"


class LineNotFoundError(CartError):
    code = "line_not_found"


class FulfillmentUnavailableError(CartError):
    code = "fulfillment_unavailable"


class UnknownPaymentMethodError(CartError):
    code = "unknown_payment_method"


# --- operations ---------------------------------------------------------------------------


def add_item(
    cart: Cart,
    config: RestaurantConfig,
    product_id: str,
    qty: int = 1,
    modifiers: Iterable[ModifierChoice] = (),
    note: str = "",
) -> tuple[Cart, CartLine]:
    """Add an item. Identical items (same product, modifiers, note and price) are merged."""
    menu = config.menu
    product = _available_product(menu, product_id)
    choices = _validated_choices(menu, product, modifiers)
    unit_price = price_unit(menu, product, choices)
    candidate = CartLine(
        line_id=cart.next_line_id,
        product_id=product.id,
        qty=_check_qty(qty, config),
        modifiers=choices,
        note=note.strip(),
        unit_price=unit_price,
    )
    for existing in cart.lines:
        if existing.same_item_as(candidate):
            merged = existing.model_copy(
                update={"qty": _check_qty(existing.qty + candidate.qty, config)}
            )
            return _replace_line(cart, merged), merged
    new_cart = cart.model_copy(
        update={"lines": (*cart.lines, candidate), "next_line_id": cart.next_line_id + 1}
    )
    return new_cart, candidate


def update_line(
    cart: Cart,
    config: RestaurantConfig,
    line_id: int,
    *,
    qty: int | None = None,
    add_modifiers: Iterable[ModifierChoice] = (),
    remove_modifiers: Iterable[ModifierChoice] = (),
    note: str | None = None,
) -> Cart:
    """Change quantity, modifiers or note of a line.

    Adding an option to a `choose_one` group replaces the previous choice in that group. A
    line that ends up identical to another one is merged into it.
    """
    line = _existing_line(cart, line_id)
    menu = config.menu
    product = _available_product(menu, line.product_id)
    to_add = list(add_modifiers)
    replaced_groups = {
        c.group_id for c in to_add if _group_type(menu, c.group_id) is ModifierGroupType.CHOOSE_ONE
    }
    removed = set(remove_modifiers)
    kept = [c for c in line.modifiers if c not in removed and c.group_id not in replaced_groups]
    choices = _validated_choices(menu, product, [*kept, *to_add])
    updated = line.model_copy(
        update={
            "qty": _check_qty(line.qty if qty is None else qty, config),
            "modifiers": choices,
            "note": line.note if note is None else note.strip(),
            "unit_price": price_unit(menu, product, choices),
        }
    )
    twin = next(
        (other for other in cart.lines if other.line_id != line_id and other.same_item_as(updated)),
        None,
    )
    if twin is None:
        return _replace_line(cart, updated)
    merged = twin.model_copy(update={"qty": _check_qty(twin.qty + updated.qty, config)})
    return _replace_line(remove_line(cart, line_id), merged)


def remove_line(cart: Cart, line_id: int) -> Cart:
    _existing_line(cart, line_id)
    return cart.model_copy(
        update={"lines": tuple(line for line in cart.lines if line.line_id != line_id)}
    )


def set_fulfillment(cart: Cart, config: RestaurantConfig, mode: Fulfillment) -> Cart:
    if mode not in enabled_fulfillments(config):
        raise FulfillmentUnavailableError(f"{mode} is not offered by this restaurant")
    return cart.model_copy(update={"fulfillment": mode})


def set_address(cart: Cart, text: str, details: str = "") -> Cart:
    return cart.model_copy(update={"address": Address(text=text.strip(), details=details.strip())})


def set_payment(
    cart: Cart, config: RestaurantConfig, method_id: str, cash_change_for: int | None = None
) -> Cart:
    method = config.payment_method(method_id)
    if method is None:
        raise UnknownPaymentMethodError(f"unknown payment method '{method_id}'")
    change = cash_change_for if method.ask_change_for else None
    return cart.model_copy(update={"payment": Payment(method_id=method.id, cash_change_for=change)})


def revalidate(cart: Cart, config: RestaurantConfig) -> tuple[Cart, tuple[CartLine, ...]]:
    """Rebuild a saved cart against the current config. Returns it and the lines it lost."""
    fresh = Cart(address=cart.address)
    dropped = []
    for line in cart.lines:
        try:
            fresh, _ = add_item(fresh, config, line.product_id, line.qty, line.modifiers, line.note)
        except CartError:
            dropped.append(line)
    offered = cart.fulfillment in enabled_fulfillments(config)
    method = config.payment_method(cart.payment.method_id) if cart.payment else None
    update = {
        "fulfillment": cart.fulfillment if offered else None,
        "payment": cart.payment if method else None,
    }
    return fresh.model_copy(update=update), tuple(dropped)


# --- queries ------------------------------------------------------------------------------


def price_unit(menu: Menu, product: Product, choices: Iterable[ModifierChoice]) -> int:
    price = product.price
    for choice in choices:
        group = menu.group(choice.group_id)
        option = group.option(choice.option_id) if group else None
        if option is None:  # pragma: no cover - choices are validated before pricing
            raise ModifierNotAllowedError(f"unknown option {choice}")
        price += option.price
    return price


def enabled_fulfillments(config: RestaurantConfig) -> tuple[Fulfillment, ...]:
    modes = []
    if config.fulfillment.delivery.enabled:
        modes.append(Fulfillment.DELIVERY)
    if config.fulfillment.pickup.enabled:
        modes.append(Fulfillment.PICKUP)
    return tuple(modes)


def effective_fulfillment(cart: Cart, config: RestaurantConfig) -> Fulfillment | None:
    """The chosen mode, or the only one the restaurant offers."""
    if cart.fulfillment is not None:
        return cart.fulfillment
    modes = enabled_fulfillments(config)
    return modes[0] if len(modes) == 1 else None


def totals(cart: Cart, config: RestaurantConfig) -> Totals:
    subtotal = sum(line.line_total for line in cart.lines)
    is_delivery = effective_fulfillment(cart, config) is Fulfillment.DELIVERY
    fee = config.fulfillment.delivery.fee if is_delivery and cart.lines else 0
    return Totals(subtotal=subtotal, delivery_fee=fee, total=subtotal + fee)


def missing_choices(cart: Cart, config: RestaurantConfig) -> list[MissingChoice]:
    menu = config.menu
    missing = []
    for line in cart.lines:
        product = menu.product(line.product_id)
        if product is None:
            continue
        chosen_groups = {c.group_id for c in line.modifiers}
        for group in menu.groups_for(product):
            if group.required and group.id not in chosen_groups:
                missing.append(
                    MissingChoice(line_id=line.line_id, product_id=product.id, group_id=group.id)
                )
    return missing


def pending(cart: Cart, config: RestaurantConfig) -> list[Pending]:
    """Slots still missing before confirmation, in the order the bot should ask for them."""
    result = []
    if cart.is_empty:
        result.append(Pending.ITEMS)
    if missing_choices(cart, config):
        result.append(Pending.REQUIRED_CHOICES)
    mode = effective_fulfillment(cart, config)
    if mode is None:
        result.append(Pending.FULFILLMENT)
    elif mode is Fulfillment.DELIVERY and cart.address is None:
        result.append(Pending.ADDRESS)
    if cart.payment is None:
        result.append(Pending.PAYMENT)
    return result


# --- helpers ------------------------------------------------------------------------------


def _available_product(menu: Menu, product_id: str) -> Product:
    product = menu.product(product_id)
    if product is None:
        raise UnknownProductError(f"unknown product '{product_id}'")
    if not product.available:
        raise ProductUnavailableError(f"product '{product_id}' is not available")
    return product


def _validated_choices(
    menu: Menu, product: Product, modifiers: Iterable[ModifierChoice]
) -> tuple[ModifierChoice, ...]:
    choices = tuple(dict.fromkeys(modifiers))  # de-duplicate, keep order
    for choice in choices:
        if choice.group_id not in product.modifier_groups:
            raise ModifierNotAllowedError(
                f"group '{choice.group_id}' does not apply to product '{product.id}'"
            )
        group = menu.group(choice.group_id)
        if group is None or group.option(choice.option_id) is None:
            raise ModifierNotAllowedError(
                f"option '{choice.option_id}' does not exist in group '{choice.group_id}'"
            )
    for group_id, count in Counter(c.group_id for c in choices).items():
        group = menu.group(group_id)
        assert group is not None
        if count > group.max_selections:
            raise TooManySelectionsError(
                f"group '{group_id}' allows at most {group.max_selections} selection(s)"
            )
    return choices


def _group_type(menu: Menu, group_id: str) -> ModifierGroupType | None:
    group = menu.group(group_id)
    return group.type if group else None


def _check_qty(qty: int, config: RestaurantConfig) -> int:
    limit = config.max_qty_per_line
    if not 1 <= qty <= limit:
        raise InvalidQuantityError(f"quantity must be between 1 and {limit}, got {qty}")
    return qty


def _existing_line(cart: Cart, line_id: int) -> CartLine:
    line = cart.line(line_id)
    if line is None:
        raise LineNotFoundError(f"line {line_id} not in cart")
    return line


def _replace_line(cart: Cart, updated: CartLine) -> Cart:
    return cart.model_copy(
        update={
            "lines": tuple(
                updated if line.line_id == updated.line_id else line for line in cart.lines
            )
        }
    )
