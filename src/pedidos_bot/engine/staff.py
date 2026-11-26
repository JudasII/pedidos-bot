"""The restaurant side: what the staff chat is told, and what staff can do about an order.

Staff read these messages, so their text is Spanish. A notification is built from the order
alone (ADR-0004): it never looks at the conversation the order came from.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage, QuickReply
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import Fulfillment, effective_fulfillment, format_phone
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


class StaffReply(FrozenModel):
    """A staff member writes to a customer in a handoff."""

    external_id: str
    code: str
    text: str
    at: datetime


class StaffReturn(FrozenModel):
    """A staff member hands the customer back to the bot."""

    external_id: str
    code: str
    at: datetime


StaffAction = StaffDecision | StaffReply | StaffReturn

_COMMAND = re.compile(r"^/(responder|devolver)(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_CODE = re.compile(r"[0-9A-Fa-f]{4}")
_CODE_IN_TEXT = re.compile(r"#([0-9A-Fa-f]{4})\b")


def parse_staff(inbound: InboundMessage) -> StaffAction | None:
    """What a message from the staff chat asks for. None if it asks for nothing.

    A tap on ✓/✗ decides an order. In a handoff, a reply to any of the bot's messages about
    the customer (they all carry `#CODE`) writes to them, and so does `/responder CODE text`;
    `/devolver CODE`, or `/devolver` as a reply, hands them back to the bot.
    """
    if inbound.kind is MessageKind.INTERACTIVE_REPLY:
        return _decision(inbound)
    text = (inbound.text or "").strip()
    if inbound.kind is not MessageKind.TEXT or not text:
        return None
    quoted = _CODE_IN_TEXT.search(inbound.reply_to_text or "")
    replied_to = quoted.group(1).upper() if quoted else None
    command = _COMMAND.match(text)
    if command is None:
        if replied_to is None:
            return None  # chatter among the staff
        return StaffReply(
            external_id=inbound.external_id, code=replied_to, text=text, at=inbound.sent_at
        )
    rest = (command.group(2) or "").strip()
    first, _, body = rest.partition(" ")
    code, said = replied_to, rest
    if _CODE.fullmatch(first):
        code, said = first.upper(), body.strip()
    if code is None:
        return None
    if command.group(1).lower() == "devolver":
        return StaffReturn(external_id=inbound.external_id, code=code, at=inbound.sent_at)
    if not said:
        return None
    return StaffReply(external_id=inbound.external_id, code=code, text=said, at=inbound.sent_at)


def _decision(inbound: InboundMessage) -> StaffDecision | None:
    if not inbound.reply_payload:
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
    if cart.customer_name is not None or cart.phone is not None:
        contact = [cart.customer_name or "", format_phone(cart.phone) if cart.phone else ""]
        rows.append("Cliente: " + " · ".join(part for part in contact if part))
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


_NOT_TEXT = {
    MessageKind.VOICE: "[nota de voz]",
    MessageKind.IMAGE: "[foto]",
    MessageKind.DOCUMENT: "[documento]",
    MessageKind.STICKER: "[sticker]",
    MessageKind.UNSUPPORTED: "[un mensaje que no puedo mostrar]",
}


def what_they_sent(inbound: InboundMessage) -> str:
    """A customer message as staff can read it. Media is named, a location becomes a map link."""
    if inbound.kind is MessageKind.LOCATION and inbound.location is not None:
        point = inbound.location
        return f"📍 https://maps.google.com/?q={point.latitude},{point.longitude}"
    if inbound.kind in _NOT_TEXT:
        caption = f" {inbound.text}" if inbound.text else ""
        return f"{_NOT_TEXT[inbound.kind]}{caption}"
    return inbound.content


ORDER_STATUS = {
    OrderStatus.PENDING_VALIDATION: "esperando aprobación",
    OrderStatus.CONFIRMED: "confirmado",
    OrderStatus.REJECTED: "rechazado",
    OrderStatus.WITHDRAWN: "retirado",
}


def handoff_notice(
    code: str, said: str, items: list[str], order: str | None = None
) -> OutboundMessage:
    """`order` names the order the customer is most likely talking about, if any."""
    rows = [f"🙋 Cliente #{code} pide hablar con una persona.", f"Escribió: “{said}”"]
    if order is not None:
        rows.append(order)
    elif items:
        rows.append("Pedido en curso: " + ", ".join(items))
    rows.append(
        f"Responde a este mensaje para escribirle (o /responder {code} tu mensaje). "
        f"Para devolverlo al asistente: /devolver {code}."
    )
    return OutboundMessage(text="\n".join(rows))


def relay_notice(code: str, inbound: InboundMessage) -> OutboundMessage:
    return OutboundMessage(text=f"💬 #{code}: {what_they_sent(inbound)}")


def returned_notice(code: str) -> OutboundMessage:
    return OutboundMessage(text=f"✅ #{code} volvió al asistente automático.")


def customer_left_notice(code: str, *, cancelled: bool) -> OutboundMessage:
    if cancelled:
        return OutboundMessage(text=f"❌ #{code} canceló el pedido.")
    return OutboundMessage(text=f"↩️ #{code} volvió al asistente por su cuenta.")


def timeout_notice(code: str, minutes: int) -> OutboundMessage:
    text = f"⏱️ #{code}: nadie respondió en {minutes} minutos; el asistente automático retomó."
    return OutboundMessage(text=text)


def not_in_handoff_notice(code: str) -> OutboundMessage:
    return OutboundMessage(text=f"#{code} ya no está con una persona: no le envié nada.")
