"""Rendered text, verified with both sample configs and their tones."""

import pytest

from pedidos_bot.channels.base import OutboundMessage, degrade
from pedidos_bot.channels.memory import WHATSAPP_LIKE
from pedidos_bot.config.schema import ToneStyle
from pedidos_bot.domain.cart import (
    Cart,
    Fulfillment,
    ModifierChoice,
    add_item,
    set_address,
    set_fulfillment,
    set_payment,
)
from pedidos_bot.engine.responder import Responder, join_options
from pedidos_bot.engine.templates import TEMPLATES, Msg
from tests.factories import sample_config

BURGER = sample_config("burger_demo")
MINIMAL = sample_config("minimal_demo")


def test_every_tone_defines_every_message() -> None:
    for style in ToneStyle:
        assert set(TEMPLATES[style]) == set(Msg)


def test_summary_shows_lines_modifiers_fee_total_payment_and_eta() -> None:
    no_onion = ModifierChoice(group_id="quitar", option_id="sin_cebolla")
    cart, _ = add_item(Cart(), BURGER, "hamburguesa_sencilla", qty=2, modifiers=[no_onion])
    cart, _ = add_item(cart, BURGER, "agua")
    cart = set_fulfillment(cart, BURGER, Fulfillment.DELIVERY)
    cart = set_address(cart, "Calle 10 # 5-20")
    cart = set_payment(cart, BURGER, "cash", cash_change_for=50000)
    assert Responder(BURGER).summary(cart).splitlines() == [
        "Este es tu pedido:",
        "• 2 x Hamburguesa Sencilla (sin cebolla): $36.000",
        "• 1 x Agua: $3.000",
        "Domicilio a Calle 10 # 5-20: $5.000",
        "Total: $44.000",
        "Pago: Efectivo (con $50.000)",
        "Tiempo estimado: 40 a 55 minutos",
    ]


def test_summary_for_pickup_in_the_formal_tone() -> None:
    cart, _ = add_item(Cart(), MINIMAL, "limonada")
    cart = set_payment(cart, MINIMAL, "efectivo")
    assert Responder(MINIMAL).summary(cart).splitlines() == [
        "Este es su pedido:",
        "• 1 x Limonada Natural: $5.000",
        "Recoge en el local",
        "Total: $5.000",
        "Pago: Efectivo",
        "Tiempo estimado: 10 a 15 minutos",
    ]


@pytest.mark.parametrize(
    ("config", "expected"),
    [(BURGER, "¿Cómo vas a pagar?"), (MINIMAL, "¿Cómo desea pagar?")],
)
def test_the_tone_comes_from_the_config(config: object, expected: str) -> None:
    assert Responder(config).text(Msg.ASK_PAYMENT) == expected  # type: ignore[arg-type]


def test_emoji_follows_the_config() -> None:
    assert Responder(BURGER).text(Msg.CONFIRMED, eta="20 minutos").endswith("✅")
    assert "✅" not in Responder(MINIMAL).text(Msg.CONFIRMED, eta="20 minutos")


def test_menu_lists_categories_and_prices() -> None:
    menu = Responder(MINIMAL).menu()
    assert "Perros calientes\n• Perro Sencillo: $9.000" in menu
    assert "Refrescos" in menu


def test_hours() -> None:
    assert "viernes, sábado de 12:00 a 23:30" in Responder(BURGER).hours()
    assert Responder(MINIMAL).hours() == "No tengo el horario disponible."


@pytest.mark.parametrize(
    ("options", "expected"),
    [(["a"], "a"), (["a", "b"], "a o b"), (["a", "b", "c"], "a, b o c"), ([], "")],
)
def test_join_options(options: list[str], expected: str) -> None:
    assert join_options(options) == expected


def test_long_payment_options_degrade_to_a_numbered_list() -> None:
    responder = Responder(BURGER)
    message = OutboundMessage(
        text=responder.text(Msg.ASK_PAYMENT),
        quick_replies=responder.replies(m.label for m in BURGER.payment_methods),
    )
    assert "2. Transferencia (Nequi / Bancolombia)" in degrade(message, WHATSAPP_LIKE).text
