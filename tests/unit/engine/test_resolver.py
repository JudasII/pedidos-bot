"""Menu resolver against the shipped configs. Customer wording is Spanish on purpose."""

from pathlib import Path

import pytest

from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.engine.resolver import MatchStatus, MenuResolver, normalize

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "restaurants"
BURGER = MenuResolver(load_restaurant("burger_demo", CONFIG_DIR))
MINIMAL = MenuResolver(load_restaurant("minimal_demo", CONFIG_DIR))


@pytest.mark.parametrize(
    ("text", "product_id"),
    [
        # exact aliases, plurals, articles and quantities
        ("sencilla", "hamburguesa_sencilla"),
        ("una sencilla", "hamburguesa_sencilla"),
        ("2 sencillas", "hamburguesa_sencilla"),
        ("la doble", "hamburguesa_doble"),
        ("dobles", "hamburguesa_doble"),
        ("Hamburguesa Especial", "hamburguesa_especial"),
        ("la de la casa", "hamburguesa_especial"),
        ("tres coronas", "corona"),
        ("unas papitas", "papas_fritas"),
        # typos
        ("hamburgesa sensilla", "hamburguesa_sencilla"),
        ("sensilla", "hamburguesa_sencilla"),
        ("chiken tenders", "chicken_tenders"),
        ("aros de sebolla", "aros_cebolla"),
        ("malteda", "malteada"),
        ("hamburguesa espesial", "hamburguesa_especial"),
        # slang and synonyms
        ("deditos de pollo", "chicken_tenders"),
        ("flor de cebolla", "onion_flower"),
        ("onion rings", "aros_cebolla"),
        ("papas a la francesa", "papas_fritas"),
        ("milkshake", "malteada"),
        ("una coca", "coca_cola"),
        ("cocacola", "coca_cola"),
        ("aguilita", "aguila"),
        ("coronita", "corona"),
        ("club", "club_colombia"),
        # longest alias wins
        ("coca zero", "coca_cola_zero"),
        ("coca cola zero bien fria", "coca_cola_zero"),
        ("cerveza club colombia", "club_colombia"),
        # accents and punctuation
        ("ÁGUILA!!", "aguila"),
        ("coca-cola", "coca_cola"),
    ],
)
def test_resolves_products(text: str, product_id: str) -> None:
    match = BURGER.resolve_product(text)
    assert match.status is MatchStatus.RESOLVED, match
    assert match.best is not None and match.best.id == product_id


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hamburguesa", {"hamburguesa_sencilla", "hamburguesa_doble", "hamburguesa_especial"}),
        ("hamburgesa", {"hamburguesa_sencilla", "hamburguesa_doble", "hamburguesa_especial"}),
        ("una cerveza", {"club_colombia", "aguila", "corona"}),
        ("gaseosa", {"coca_cola", "coca_cola_zero", "colombiana", "agua"}),
    ],
)
def test_ambiguous_products_offer_candidates(text: str, expected: set[str]) -> None:
    match = BURGER.resolve_product(text)
    assert match.status is MatchStatus.AMBIGUOUS
    assert match.best is None
    assert {c.id for c in match.candidates} == expected


@pytest.mark.parametrize("text", ["pizza", "perro caliente", "sushi", "queso", "", "   ", "2"])
def test_unknown_products_are_rejected(text: str) -> None:
    assert BURGER.resolve_product(text).status is MatchStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("text", "modifiers", "product_id", "expected"),
    [
        ("malteada de fresa", [], "malteada", {"sabor_fresa"}),
        ("malteda de chocolate", [], "malteada", {"sabor_chocolate"}),
        ("una coca grande", [], "coca_cola", {"gaseosa_litro_y_medio"}),
        ("coca zero personal", [], "coca_cola_zero", {"gaseosa_personal"}),
        ("papas familiares", [], "papas_fritas", {"papas_familiar"}),
        (
            "sencilla sin cebolla y con tocineta",
            [],
            "hamburguesa_sencilla",
            {"sin_cebolla", "extra_tocineta"},
        ),
        (
            "doble",
            ["sin tomate", "doble queso"],
            "hamburguesa_doble",
            {"sin_tomate", "extra_queso"},
        ),
        ("hamburguesa sencilla", [], "hamburguesa_sencilla", set()),
        ("chiken tenders", ["bbq"], "chicken_tenders", {"salsa_bbq"}),
    ],
)
def test_resolve_item_with_modifiers(
    text: str, modifiers: list[str], product_id: str, expected: set[str]
) -> None:
    item = BURGER.resolve_item(text, modifiers)
    assert item.product.best is not None and item.product.best.id == product_id
    assert {c.option_id for c in item.choices} == expected
    assert item.unresolved_modifiers == ()


def test_unknown_modifier_is_reported_not_guessed() -> None:
    item = BURGER.resolve_item("sencilla", ["con piña"])
    assert item.choices == ()
    assert [m.status for m in item.unresolved_modifiers] == [MatchStatus.NOT_FOUND]


def test_modifier_from_another_product_is_not_found() -> None:
    # "fresa" is a milkshake flavor, not a burger option.
    assert BURGER.resolve_modifier("hamburguesa_sencilla", "fresa").status is MatchStatus.NOT_FOUND


def test_negation_mismatch_never_resolves() -> None:
    match = BURGER.resolve_modifier("hamburguesa_sencilla", "cebolla")
    assert match.status is MatchStatus.AMBIGUOUS
    assert BURGER.resolve_modifier("hamburguesa_sencilla", "sin cebola").best is not None


@pytest.mark.parametrize(
    ("text", "method_id"),
    [
        ("efectivo", "cash"),
        ("en efectivo", "cash"),
        ("efectibo", "cash"),
        ("pago por nequi", "transfer"),
        ("transferencia", "transfer"),
        ("daviplata", "transfer"),
        ("con tarjeta", "card_on_delivery"),
        ("datáfono", "card_on_delivery"),
    ],
)
def test_resolves_payment_methods(text: str, method_id: str) -> None:
    match = BURGER.resolve_payment(text)
    assert match.best is not None and match.best.id == method_id


def test_unknown_payment_method() -> None:
    assert BURGER.resolve_payment("bitcoin").status is MatchStatus.NOT_FOUND


def test_minimal_restaurant_uses_its_own_menu() -> None:
    assert MINIMAL.resolve_product("un perrito").best.id == "perro_sencillo"  # type: ignore[union-attr]
    assert MINIMAL.resolve_product("perro suizo").best.id == "perro_suizo"  # type: ignore[union-attr]
    assert MINIMAL.resolve_product("hamburguesa").status is MatchStatus.NOT_FOUND
    assert MINIMAL.resolve_payment("nequi").status is MatchStatus.NOT_FOUND


def test_unavailable_products_are_not_resolvable() -> None:
    from tests.factories import config_dict, make_config

    data = config_dict()
    data["categories"][0]["products"][0]["available"] = False
    resolver = MenuResolver(make_config(**data))
    assert resolver.resolve_product("clasica").status is MatchStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Hamburguesas  Dobles!", "hamburguesa doble"), ("Piña, ÑAME", "pina name"), ("los", "los")],
)
def test_normalize(text: str, expected: str) -> None:
    assert normalize(text) == expected
