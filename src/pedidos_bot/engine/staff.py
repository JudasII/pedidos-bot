"""The restaurant side: what the staff chat is told, and what staff can do about an order.

Staff read these messages, so their text is Spanish. A notification is built from the order
alone (ADR-0004): it never looks at the conversation the order came from.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage, QuickReply
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import Fulfillment, effective_fulfillment
from pedidos_bot.domain.money import format_cop
from pedidos_bot.domain.order import Order, OrderStatus
from pedidos_bot.engine.responder import Responder
from pedidos_bot.models import FrozenModel


class Verdict(StrEnum):
    APPROVE = "ok"
    REJECT = "no"


_BUTTONS = {Verdict.APPROVE: "✓ Aprobar", Verdict.REJECT: "✗ Rechazar"}


class StaffDecision(FrozenModel):
    """A staff member approved or rejected one revision of an order."""

    external_id: str
    """The channel's id for this action; a repeated id is a duplicate delivery."""
    order_key: str
    revision: int
    verdict: Verdict
    at: datetime


def _payload(order: Order, verdict: Verdict) -> str:
    return f"{verdict.value}:{order.idempotency_key}:{order.revision}"


def parse_staff(inbound: InboundMessage) -> StaffDecision | None:
    """What a message from the staff chat asks for. None if it asks for nothing."""
    if inbound.kind is not MessageKind.INTERACTIVE_REPLY or not inbound.reply_payload:
        return None
    verdict, _, rest = inbound.reply_payload.partition(":")
    key, _, revision = rest.rpartition(":")
    if verdict not in Verdict or not key or not revision.isdecimal():
        return None
    return StaffDecision(
        external_id=inbound.external_id,
        order_key=key,
        revision=int(revision),
        verdict=Verdict(verdict),
        at=inbound.sent_at,
    )


def order_notification(order: Order, config: RestaurantConfig) -> OutboundMessage:
    """The order as the cashier needs it, with the buttons to decide if it awaits them."""
    responder = Responder(config)
    cart = order.cart
    title = f"🧾 Pedido {order.code}"
    if order.revision > 1:
        title += f" (cambio {order.revision - 1})"
    rows = [title]
    for line in cart.lines:
        rows.append(f"• {responder.line(line)}: {format_cop(line.line_total)}")
        if line.note:
            rows.append(f"   Nota: {line.note}")
    if effective_fulfillment(cart, config) is Fulfillment.DELIVERY and cart.address is not None:
        rows.append(f"Domicilio: {cart.address.text} ({format_cop(order.totals.delivery_fee)})")
        if cart.address.details:
            rows.append(f"   {cart.address.details}")
    else:
        rows.append("Recoge en el local")
    rows.append(f"Total: {format_cop(order.totals.total)}")
    if cart.payment is not None:
        method = config.payment_method(cart.payment.method_id)
        label = method.label if method else cart.payment.method_id
        change = cart.payment.cash_change_for
        rows.append(f"Pago: {label}" + (f" (paga con {format_cop(change)})" if change else ""))
    if order.status is not OrderStatus.PENDING_VALIDATION:
        rows.append("Confirmado con el cliente.")
        return OutboundMessage(text="\n".join(rows))
    buttons = tuple(
        QuickReply(title=_BUTTONS[verdict], payload=_payload(order, verdict)) for verdict in Verdict
    )
    return OutboundMessage(text="\n".join(rows), quick_replies=buttons)


def withdrawn_notice(order: Order) -> OutboundMessage:
    text = f"⚠️ Pedido {order.code}: el cliente lo canceló o lo está cambiando. No lo preparen."
    return OutboundMessage(text=text)


def decision_notice(order: Order, verdict: Verdict) -> OutboundMessage:
    done = "aprobado ✅" if verdict is Verdict.APPROVE else "rechazado ❌"
    return OutboundMessage(text=f"Pedido {order.code} {done}. Ya le avisé al cliente.")


def stale_notice(code: str) -> OutboundMessage:
    return OutboundMessage(text=f"El pedido {code} ya no está pendiente: no cambié nada.")
