from pathlib import Path

import pytest
from pydantic import ValidationError

from pedidos_bot.config.loader import (
    ConfigError,
    config_json_schema,
    load_config,
    resolve_staff_chat_id,
)
from pedidos_bot.config.schema import RestaurantConfig
from tests.factories import config_dict, make_config


def _errors(data: dict[str, object]) -> str:
    with pytest.raises(ValidationError) as exc:
        RestaurantConfig.model_validate(data)
    return str(exc.value)


def test_valid_config_builds_menu() -> None:
    config = make_config()
    assert [p.id for p in config.menu.products()] == ["classic", "soda"]
    assert config.max_qty_per_line == 10


def test_unknown_field_is_rejected() -> None:
    data = config_dict()
    data["restaurant"]["phone"] = "123"
    assert "phone" in _errors(data)


def test_product_with_unknown_modifier_group() -> None:
    data = config_dict()
    data["categories"][0]["products"][0]["modifier_groups"] = ["nope"]
    assert "unknown modifier groups: ['nope']" in _errors(data)


def test_duplicate_product_ids() -> None:
    data = config_dict()
    data["categories"][1]["products"][0]["id"] = "classic"
    assert "duplicate product ids: ['classic']" in _errors(data)


def test_duplicate_option_ids_in_group() -> None:
    data = config_dict()
    data["modifier_groups"][0]["options"][1]["id"] = "no_onion"
    assert "duplicate option ids" in _errors(data)


def test_choose_one_cannot_allow_multiple() -> None:
    data = config_dict()
    data["modifier_groups"][2]["max_select"] = 2
    assert "cannot have max_select > 1" in _errors(data)


def test_rule_with_unknown_category() -> None:
    data = config_dict(rules=[{"type": "age_notice", "categories": ["beers"], "message": "+18"}])
    assert "unknown categories: ['beers']" in _errors(data)


def test_at_least_one_fulfillment_mode() -> None:
    data = config_dict(fulfillment={"delivery": {"enabled": False}, "pickup": {"enabled": False}})
    assert "at least one fulfillment mode" in _errors(data)


def test_enabled_delivery_requires_eta() -> None:
    data = config_dict(fulfillment={"delivery": {"enabled": True, "fee": 1000}})
    assert "requires eta_minutes" in _errors(data)


def test_ids_must_be_machine_identifiers() -> None:
    data = config_dict()
    data["categories"][0]["products"][0]["id"] = "Clásica"
    assert "string should match pattern" in _errors(data).lower()


def test_negative_price_rejected() -> None:
    data = config_dict()
    data["categories"][0]["products"][0]["price"] = -1
    assert "greater than or equal to 0" in _errors(data)


def test_chat_id_env_must_be_an_env_var_name() -> None:
    data = config_dict()
    data["restaurant"]["notify"]["chat_id_env"] = "-1001234"
    assert "chat_id_env" in _errors(data)


def test_load_config_reports_readable_errors(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("restaurant:\n  id: x\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    message = str(exc.value)
    assert message.startswith(f"{path}: ")
    assert "  - restaurant.name: Field required" in message
    assert "  - tone: Field required" in message


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("restaurant: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file not found"):
        load_config(tmp_path / "missing.yaml")


def test_staff_chat_id_comes_from_environment() -> None:
    config = make_config()
    assert resolve_staff_chat_id(config, {"TEST_RESTO_STAFF_CHAT_ID": " -100123 "}) == "-100123"
    with pytest.raises(ConfigError, match="TEST_RESTO_STAFF_CHAT_ID"):
        resolve_staff_chat_id(config, {})


def test_json_schema_covers_top_level_sections() -> None:
    schema = config_json_schema()
    assert set(schema["required"]) >= {"restaurant", "fulfillment", "payment_methods", "tone"}
