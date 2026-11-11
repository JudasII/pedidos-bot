"""Shortcuts. Customer wording is Spanish on purpose."""

import pytest

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
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.shortcuts import detect
from tests.factories import make_inbound, sample_config

RESOLVER = MenuResolver(sample_config("burger_demo"))


def _message(text: str | None = None, kind: MessageKind = MessageKind.TEXT) -> InboundMessage:
    return make_inbound(text=text, kind=kind)


@pytest.mark.parametrize(
    ("text", "action"),
    [
        *[
            (t, Confirm())
            for t in [
                "sí",
                "Si",
                "SIIII",
                "dale",
                "dalee!!",
                "de una",
                "listo",
                "ok",
                "hágale",
                "👍",
            ]
        ],
        *[(t, Deny()) for t in ["no", "NO!", "nooo", "no gracias", "nada más", "eso es todo"]],
        *[(t, CancelOrder()) for t in ["cancelar", "ya no quiero", "cancela el pedido"]],
        *[(t, AskHuman()) for t in ["un asesor", "hablar con una persona", "pásame con alguien"]],
        ("a domicilio", SetFulfillment(mode="delivery")),
        ("para recoger", SetFulfillment(mode="pickup")),
        ("nequi", SetPayment(method="nequi")),
        ("en efectivo", SetPayment(method="en efectivo")),
        ("Transferencia", SetPayment(method="Transferencia")),
    ],
)
def test_colloquial_phrases(text: str, action: Action) -> None:
    assert detect(_message(text), RESOLVER) == (action,)


@pytest.mark.parametrize(
    "text",
    [
        "no quiero cebolla",
        "si, pero sin tomate",
        "listo, y una coca",
        "una sencilla",
        "nequi o efectivo",
        "",
        "   ",
        "😀",
    ],
)
def test_anything_more_goes_to_the_llm(text: str) -> None:
    assert detect(_message(text), RESOLVER) is None


@pytest.mark.parametrize(
    "kind",
    [
        MessageKind.VOICE,
        MessageKind.LOCATION,
        MessageKind.IMAGE,
        MessageKind.DOCUMENT,
        MessageKind.STICKER,
        MessageKind.UNSUPPORTED,
    ],
)
def test_non_text_messages_go_to_a_person(kind: MessageKind) -> None:
    assert detect(_message(kind=kind), RESOLVER) == (AskHuman(),)


def test_button_replies_use_the_same_phrases() -> None:
    message = _message(kind=MessageKind.INTERACTIVE_REPLY).model_copy(
        update={"reply_payload": "Sí"}
    )
    assert detect(message, RESOLVER) == (Confirm(),)
