"""The shipped restaurant configs must validate, and none of their data may leak into src/."""

import re
from pathlib import Path

import pytest

from pedidos_bot.config.loader import ConfigError, load_restaurant
from pedidos_bot.config.schema import RestaurantConfig, ToneStyle

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "config" / "restaurants"
RESTAURANTS = sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))


def test_at_least_two_restaurants_ship() -> None:
    assert {"burger_demo", "minimal_demo"} <= set(RESTAURANTS)


@pytest.mark.parametrize("restaurant_id", RESTAURANTS)
def test_sample_config_validates(restaurant_id: str) -> None:
    config = load_restaurant(restaurant_id, CONFIG_DIR)
    assert config.restaurant.id == restaurant_id


def test_file_name_must_match_restaurant_id(tmp_path: Path) -> None:
    source = (CONFIG_DIR / "minimal_demo.yaml").read_text(encoding="utf-8")
    (tmp_path / "other.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(ConfigError, match="file name must match"):
        load_restaurant("other", tmp_path)


def test_burger_demo_has_the_required_menu() -> None:
    menu = load_restaurant("burger_demo", CONFIG_DIR).menu
    by_category = {c.id: [p.id for p in c.products] for c in menu.categories}
    assert len(by_category["hamburguesas"]) == 3
    assert {"chicken_tenders", "onion_flower", "aros_cebolla", "papas_fritas"} <= set(
        by_category["acompanantes"]
    )
    for category in ("malteadas", "bebidas", "cervezas"):
        assert by_category[category]


def test_minimal_demo_differs_from_burger_demo() -> None:
    burger = load_restaurant("burger_demo", CONFIG_DIR)
    minimal = load_restaurant("minimal_demo", CONFIG_DIR)
    assert burger.fulfillment.delivery.enabled and not minimal.fulfillment.delivery.enabled
    assert len(minimal.payment_methods) == 1 < len(burger.payment_methods)
    assert (burger.tone.style, minimal.tone.style) == (ToneStyle.INFORMAL, ToneStyle.FORMAL)
    assert burger.modes.shadow != minimal.modes.shadow


def _restaurant_terms(config: RestaurantConfig) -> set[str]:
    terms = {config.restaurant.id, config.restaurant.name}
    for product in config.menu.products():
        terms |= {product.id, product.name}
    for group in config.modifier_groups:
        terms.add(group.id)
        terms |= {option.id for option in group.options}
    terms |= {c.id for c in config.categories}
    return {t.lower() for t in terms if len(t) >= 4}


def test_no_restaurant_data_is_hardcoded_in_src() -> None:
    terms: set[str] = set()
    for restaurant_id in RESTAURANTS:
        terms |= _restaurant_terms(load_restaurant(restaurant_id, CONFIG_DIR))
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(terms)) + r")\b")
    leaks = []
    for source in (ROOT / "src").rglob("*.py"):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line.lower()):
                leaks.append(f"{source.relative_to(ROOT)}:{lineno}: {match.group(0)!r}")
    assert not leaks, "restaurant data hardcoded in src/:\n" + "\n".join(leaks)
