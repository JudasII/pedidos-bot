"""Renders text from templates, the tone and the cart. It decides nothing about the flow."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pedidos_bot.channels.base import QuickReply
from pedidos_bot.config.schema import EtaRange, RestaurantConfig
from pedidos_bot.domain.cart import (
    Cart,
    CartLine,
    Fulfillment,
    ModifierChoice,
    effective_fulfillment,
    totals,
)
from pedidos_bot.domain.money import format_cop
from pedidos_bot.engine.templates import HEAVY_EMOJI, LIGHT_EMOJI, TEMPLATES, WEEKDAYS, Msg


def join_options(options: Sequence[str]) -> str:
    """ "a, b o c" """
    if len(options) < 2:
        return "".join(options)
    return f"{', '.join(options[:-1])} o {options[-1]}"


class Responder:
    def __init__(self, config: RestaurantConfig) -> None:
        self._config = config
        self._menu = config.menu
        self._templates = TEMPLATES[config.tone.style]
        emoji = config.tone.emoji
        self._emoji = {"none": {}, "light": LIGHT_EMOJI, "heavy": HEAVY_EMOJI}[emoji]

    def text(self, key: Msg, **params: str) -> str:
        rendered = self._templates[key].format(**params)
        emoji = self._emoji.get(key)
        return f"{rendered} {emoji}" if emoji else rendered

    @property
    def greeting(self) -> str:
        return self._config.tone.greeting

    def product_name(self, product_id: str) -> str:
        product = self._menu.product(product_id)
        return product.name if product else product_id

    def option_label(self, choice: ModifierChoice) -> str:
        group = self._menu.group(choice.group_id)
        option = group.option(choice.option_id) if group else None
        return option.label if option else choice.option_id

    def line(self, line: CartLine) -> str:
        """ "2 x Pizza (sin aceitunas)" """
        name = f"{line.qty} x {self.product_name(line.product_id)}"
        if not line.modifiers:
            return name
        return f"{name} ({', '.join(self.option_label(m) for m in line.modifiers)})"

    def summary(self, cart: Cart) -> str:
        total = totals(cart, self._config)
        rows = [self.text(Msg.SUMMARY_TITLE)]
        rows += [f"• {self.line(line)}: {format_cop(line.line_total)}" for line in cart.lines]
        mode = effective_fulfillment(cart, self._config)
        if mode is Fulfillment.DELIVERY and cart.address is not None:
            delivery = self.text(Msg.SUMMARY_DELIVERY, address=cart.address.text)
            rows.append(f"{delivery}: {format_cop(total.delivery_fee)}")
        elif mode is Fulfillment.PICKUP:
            rows.append(self.text(Msg.SUMMARY_PICKUP))
        rows.append(f"Total: {format_cop(total.total)}")
        if cart.payment is not None:
            method = self._config.payment_method(cart.payment.method_id)
            label = method.label if method else cart.payment.method_id
            change = cart.payment.cash_change_for
            rows.append(f"Pago: {label}" + (f" (con {format_cop(change)})" if change else ""))
        eta = self.eta(cart)
        if eta:
            rows.append(f"Tiempo estimado: {eta}")
        return "\n".join(rows)

    def eta(self, cart: Cart) -> str:
        mode = effective_fulfillment(cart, self._config)
        fulfillment = self._config.fulfillment
        options: dict[Fulfillment | None, EtaRange | None] = {
            Fulfillment.DELIVERY: fulfillment.delivery.eta_minutes,
            Fulfillment.PICKUP: fulfillment.pickup.eta_minutes,
        }
        eta = options.get(mode)
        return f"{eta.min} a {eta.max} minutos" if eta else ""

    def menu(self) -> str:
        sections = []
        for category in self._menu.categories:
            items = [f"• {p.name}: {format_cop(p.price)}" for p in category.products if p.available]
            if items:
                sections.append("\n".join([category.name, *items]))
        return "\n\n".join(sections)

    def hours(self) -> str:
        hours = self._config.restaurant.hours
        if not hours:
            return self.text(Msg.HOURS_UNKNOWN)
        rows = [
            f"{', '.join(WEEKDAYS[d.value] for d in row.days)} de {row.open} a {row.close}"
            for row in hours
        ]
        return self.text(Msg.HOURS, hours="; ".join(rows))

    def yes_no(self) -> tuple[QuickReply, ...]:
        return self.replies([self.text(Msg.YES), self.text(Msg.NO)])

    @staticmethod
    def replies(titles: Iterable[str]) -> tuple[QuickReply, ...]:
        return tuple(QuickReply(title=title, payload=title) for title in titles)
