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
        # A word shared by several product names identifies none of them.
        ("cebolla", {"aros_cebolla", "onion_flower"}),
        ("cola", {"coca_cola", "coca_cola_zero"}),
    ],
)
def test_ambiguous_products_offer_candidates(text: str, expected: set[str]) -> None:
    match = BURGER.resolve_product(text)
    assert match.status is MatchStatus.AMBIGUOUS
    assert match.best is None
    assert {c.id for c in match.candidates} == expected


@pytest.mark.parametrize(
    "text", ["pizza", "perro caliente", "sushi", "queso", "", "   ", "2", "de", "sin"]
)
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
        (
            "sencilla con tocineta sin cebolla",
            [],
            "hamburguesa_sencilla",
            {"extra_tocineta", "sin_cebolla"},
        ),
        ("doble sin tomate tocineta", [], "hamburguesa_doble", {"sin_tomate", "extra_tocineta"}),
        (
            "sencilla",
            ["con tocineta y sin cebolla"],
            "hamburguesa_sencilla",
            {"extra_tocineta", "sin_cebolla"},
        ),
        ("coca 400", [], "coca_cola", {"gaseosa_personal"}),
        ("coca 1.5", [], "coca_cola", {"gaseosa_litro_y_medio"}),
        ("coca litro y medio", [], "coca_cola", {"gaseosa_litro_y_medio"}),
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


@pytest.mark.parametrize(
    ("text", "product_id", "expected"),
    [
        # "sencilla" names a burger, but here it is the size of the fries.
        ("papas sencillas", "papas_fritas", {"papas_personal"}),
        ("unas papas sencillas", "papas_fritas", {"papas_personal"}),
    ],
)
def test_word_that_is_a_modifier_of_the_other_product(
    text: str, product_id: str, expected: set[str]
) -> None:
    item = BURGER.resolve_item(text)
    assert item.product.best is not None and item.product.best.id == product_id
    assert {c.option_id for c in item.choices} == expected
    assert item.unresolved_modifiers == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sencilla con coca", {"hamburguesa_sencilla", "coca_cola"}),
        ("doble con papas", {"hamburguesa_doble", "papas_fritas"}),
        # Modifiers follow their product: these are burgers with fries, not small fries.
        ("sencilla con papas", {"hamburguesa_sencilla", "papas_fritas"}),
        ("2 sencillas con papas", {"hamburguesa_sencilla", "papas_fritas"}),
        ("papas sencillas con tocineta", {"hamburguesa_sencilla", "papas_fritas"}),
        (
            "sencilla con cerveza",
            {"hamburguesa_sencilla", "club_colombia", "aguila", "corona"},
        ),
        ("sencilla con coca y papas", {"hamburguesa_sencilla", "coca_cola", "papas_fritas"}),
        ("sencilla con tocineta papas", {"hamburguesa_sencilla", "papas_fritas"}),
        ("una doble y una sencilla", {"hamburguesa_sencilla", "hamburguesa_doble"}),
    ],
)
def test_two_products_in_one_item_are_ambiguous(text: str, expected: set[str]) -> None:
    item = BURGER.resolve_item(text)
    assert item.product.status is MatchStatus.AMBIGUOUS
    assert {c.id for c in item.product.candidates} == expected


@pytest.mark.parametrize(
    ("text", "product_id", "expected", "unresolved"),
    [
        # A leftover word that names another product can still be a real modifier here.
        ("sencilla con doble carne", "hamburguesa_sencilla", {"extra_carne"}, 0),
        ("doble con doble carne", "hamburguesa_doble", {"extra_carne"}, 0),
        ("hamburguesa doble con carne extra", "hamburguesa_doble", {"extra_carne"}, 0),
        # Words that name no product are reported as modifiers, never turned into a rival.
        ("coca cola zero bien fria", "coca_cola_zero", set(), 1),
        ("sencilla sin coca", "hamburguesa_sencilla", set(), 1),
        # "gas" is in an alias of the product, but that alias says the opposite.
        ("agua con gas", "agua", set(), 1),
        # A number the menu does not offer is reported, never rounded to the closest size.
        ("coca 2 litros", "coca_cola", set(), 1),
        ("sencilla con 2 huevos", "hamburguesa_sencilla", {"extra_huevo"}, 1),
    ],
)
def test_leftover_words_that_do_not_change_the_product(
    text: str, product_id: str, expected: set[str], unresolved: int
) -> None:
    item = BURGER.resolve_item(text)
    assert item.product.best is not None and item.product.best.id == product_id
    assert {c.option_id for c in item.choices} == expected
    assert len(item.unresolved_modifiers) == unresolved


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hamburguesa con doble carne", "extra_carne"),
        ("doble carne", "extra_carne"),
        ("hamburguesa doble carne", "extra_carne"),
    ],
)
def test_modifier_that_contains_a_product_alias_asks_which_product(
    text: str, expected: str
) -> None:
    # "doble" is a burger, but "doble carne" is extra meat on a burger not yet chosen.
    item = BURGER.resolve_item(text)
    assert item.product.status is MatchStatus.AMBIGUOUS
    assert {c.id for c in item.product.candidates} == {
        "hamburguesa_sencilla",
        "hamburguesa_doble",
        "hamburguesa_especial",
    }
    # The customer's extra must survive the clarifying question.
    assert [c.option_id for c in item.choices] == [expected]


@pytest.mark.parametrize(
    ("text", "modifiers", "expected"),
    [
        ("sencilla sin cebolla con papas", [], {"sin_cebolla"}),
        ("sencilla con coca", ["sin cebolla"], {"sin_cebolla"}),
    ],
)
def test_two_products_keep_the_modifiers_for_the_clarification(
    text: str, modifiers: list[str], expected: set[str]
) -> None:
    item = BURGER.resolve_item(text, modifiers)
    assert item.product.status is MatchStatus.AMBIGUOUS
    assert {c.option_id for c in item.choices} == expected


def test_ambiguous_modifier_keeps_the_product() -> None:
    # "cebolla" is unclear for a burger, which is a question about the burger, not a new item.
    item = BURGER.resolve_item("sencilla con cebolla")
    assert item.product.best is not None and item.product.best.id == "hamburguesa_sencilla"
    assert [m.status for m in item.unresolved_modifiers] == [MatchStatus.AMBIGUOUS]


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
