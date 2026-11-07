"""Deterministic answers that skip the LLM: fast, free, and exact where mistakes cost most.

A shortcut only fires when the whole message is the phrase, so "no" matches but "no quiero
cebolla" goes to the LLM. These are Spanish phrases because customers write them.
"""

from __future__ import annotations

import re

from pedidos_bot.channels.base import InboundMessage, MessageKind
from pedidos_bot.engine.actions import (
    Action,
    AskHuman,
    CancelOrder,
    Confirm,
    Deny,
    SetFulfillment,
    SetPayment,
)
from pedidos_bot.engine.resolver import MatchMethod, MenuResolver, normalize


def _key(text: str) -> str:
    """Normalize and collapse stretched letters, so "siiii" and "dalee" still match."""
    return re.sub(r"(.)\1+", r"\1", normalize(text))


def _phrases(*phrases: str) -> frozenset[str]:
    return frozenset(_key(phrase) for phrase in phrases)


# TODO(#44): accept combined affirmations, not only this fixed list.
_CONFIRM = _phrases(
    "sí", "si señor", "si señora", "si por favor", "si porfa", "si gracias", "dale", "dale pues",
    "de una", "listo", "listo pues", "ok", "okey", "okay", "claro", "claro que si", "confirmo",
    "confirmar", "confirmado", "hágale", "hagale pues", "sisas", "correcto", "perfecto", "va",
    "bueno", "así está bien", "todo bien",
)  # fmt: skip
_DENY = _phrases(
    "no", "nop", "nel", "no gracias", "no señor", "no señora", "nada", "nada más",
    "eso es todo", "sería todo", "ya no más", "no más",
)  # fmt: skip
# TODO(#43): check in testing whether these are clear enough signs of intent.
_DONE_ADDING = _phrases("listo", "listo pues", "así está bien", "todo bien")
"""Confirmations that also mean "nothing more", unlike a plain "sí"."""
_CANCEL = _phrases(
    "cancelar", "cancela", "cancelalo", "cancela todo", "cancelar pedido", "cancela el pedido",
    "ya no quiero", "ya no quiero nada", "olvídalo", "olvidalo mejor", "déjalo así",
)  # fmt: skip
_HUMAN = _phrases(
    "asesor", "un asesor", "hablar con un asesor", "persona", "una persona",
    "hablar con una persona", "quiero hablar con alguien", "humano", "un humano", "ayuda humana",
    "pásame con alguien", "pásame con una persona", "operador",
)  # fmt: skip
_DELIVERY = _phrases("domicilio", "a domicilio", "para domicilio", "envío", "que me lo traigan")
_PICKUP = _phrases("recoger", "para recoger", "paso a recoger", "yo recojo", "para llevar")
# TODO(#44): decide which other emoji confirm, and in which contexts.
_CONFIRM_EMOJI = frozenset("👍👌✅")


def is_confirm(text: str) -> bool:
    return _key(text) in _CONFIRM or (bool(text) and set(text) <= _CONFIRM_EMOJI)


def is_done_adding(text: str) -> bool:
    return _key(text) in _DONE_ADDING


def is_deny(text: str) -> bool:
    return _key(text) in _DENY


def detect(message: InboundMessage, resolver: MenuResolver) -> tuple[Action, ...] | None:
    """The actions for `message` if a shortcut covers it, otherwise None."""
    if message.kind not in (MessageKind.TEXT, MessageKind.INTERACTIVE_REPLY):
        return (AskHuman(),)
    text = message.content
    key = _key(text)
    if is_confirm(text):
        return (Confirm(),)
    if not key:
        return None
    for phrases, action in (
        (_DENY, Deny()),
        (_CANCEL, CancelOrder()),
        (_HUMAN, AskHuman()),
        (_DELIVERY, SetFulfillment(mode="delivery")),
        (_PICKUP, SetFulfillment(mode="pickup")),
    ):
        if key in phrases:
            return (action,)
    if resolver.resolve_payment(text).method is MatchMethod.EXACT:
        return (SetPayment(method=text),)
    return None
