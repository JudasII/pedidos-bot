"""Exact answers of the resolver: how it matched, with what score and what it left over.

`pedidos-bot resolve` prints the method and scores, and the turn uses the leftover words, so
these are behavior, not internals. Each test pins what mutation testing showed unguarded.
"""

from pedidos_bot.engine.resolver import MatchMethod, MatchStatus, MenuResolver
from tests.factories import config_dict, make_config, sample_config

BURGER = MenuResolver(sample_config("burger_demo"))


def _ids(match: object) -> list[str]:
    return [c.id for c in match.candidates]  # type: ignore[attr-defined]


def test_a_leading_quantity_is_not_part_of_the_name() -> None:
    match = BURGER.resolve_product("2 sencillas")
    assert (match.status, match.method, match.candidates[0].score) == (
        MatchStatus.RESOLVED, MatchMethod.EXACT, 100.0,
    )  # fmt: skip


def test_only_fillers_name_nothing() -> None:
    match = BURGER.resolve_product("una")
    assert (match.status, match.method, match.candidates) == (
        MatchStatus.NOT_FOUND,
        MatchMethod.NONE,
        (),
    )


def test_a_shared_name_keeps_the_words_after_it() -> None:
    match = BURGER.resolve_product("hamburguesa sin cebolla")
    assert (match.status, match.method, match.leftover) == (
        MatchStatus.AMBIGUOUS, MatchMethod.CONTAINS, "sin cebolla",
    )  # fmt: skip
    assert _ids(match) == ["hamburguesa_sencilla", "hamburguesa_doble", "hamburguesa_especial"]
    assert {c.score for c in match.candidates} == {100.0}


def test_two_different_names_in_one_phrase_are_two_candidates_and_no_leftover() -> None:
    match = BURGER.resolve_item("sencilla con papas").product
    assert (match.status, match.method, match.leftover) == (
        MatchStatus.AMBIGUOUS, MatchMethod.CONTAINS, "",
    )  # fmt: skip
    assert _ids(match) == ["hamburguesa_sencilla", "papas_fritas"]


def test_a_category_name_offers_its_products() -> None:
    match = BURGER.resolve_product("cervezas")
    assert (match.status, match.method) == (MatchStatus.AMBIGUOUS, MatchMethod.EXACT)
    assert _ids(match) == ["club_colombia", "aguila", "corona"]


def test_a_product_is_known_by_its_name_even_without_aliases() -> None:
    data = config_dict()
    data["categories"][1]["products"][0]["name"] = "Refresco de la casa"
    match = MenuResolver(make_config(**data)).resolve_product("refresco de la casa")
    assert (match.status, match.method) == (MatchStatus.RESOLVED, MatchMethod.EXACT)


def test_an_option_is_known_by_its_label_even_without_aliases() -> None:
    match = MenuResolver(make_config()).resolve_modifier("classic", "queso extra")
    assert (match.method, match.best is not None and match.best.id) == (MatchMethod.EXACT, "cheese")


def test_fuzzy_scores_are_rounded_to_one_decimal() -> None:
    match = BURGER.resolve_product("sencila")
    assert (match.method, match.candidates[0].score) == (MatchMethod.FUZZY, 93.3)


def test_a_typo_keeps_the_words_after_the_name() -> None:
    item = BURGER.resolve_item("sencila sin cebolla")
    assert item.product.leftover == "sin cebolla"
    assert [(m.query, m.best.id if m.best else None) for m in item.modifiers] == [
        ("sin cebolla", "sin_cebolla"),
    ]


def test_an_option_that_names_a_product_asks_which_one_and_keeps_the_option() -> None:
    item = BURGER.resolve_item("hamburguesa con doble carne")
    assert (item.product.status, item.product.method) == (
        MatchStatus.AMBIGUOUS,
        MatchMethod.CONTAINS,
    )
    assert _ids(item.product) == [
        "hamburguesa_sencilla",
        "hamburguesa_doble",
        "hamburguesa_especial",
    ]
    assert {c.score for c in item.product.candidates} == {100.0}
    assert [(m.query, m.best.id if m.best else None) for m in item.modifiers] == [
        ("doble carne", "extra_carne"),
    ]


def test_a_second_product_word_used_as_an_option_is_read_as_the_option() -> None:
    item = BURGER.resolve_item("papas sencilla")
    assert (item.product.status, _ids(item.product)) == (MatchStatus.RESOLVED, ["papas_fritas"])
    assert item.product.method is MatchMethod.EXACT
    assert [(m.query, m.best.id if m.best else None) for m in item.modifiers] == [
        ("sencilla", "papas_personal"),
    ]


def test_several_options_in_one_text_are_split() -> None:
    item = BURGER.resolve_item("sencilla", ["sin cebolla con tocineta"])
    assert [(m.query, m.best.id if m.best else None) for m in item.modifiers] == [
        ("sin cebolla", "sin_cebolla"), ("tocineta", "extra_tocineta"),
    ]  # fmt: skip
