"""The action contract. Customer wording is Spanish on purpose."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pedidos_bot.engine.actions import (
    AddItem,
    Confirm,
    NluOutput,
    SetFulfillment,
    actions_json_schema,
)

ACTION_TYPES = {
    "add_item",
    "remove_item",
    "update_item",
    "set_fulfillment",
    "set_address",
    "set_payment",
    "confirm",
    "deny",
    "cancel_order",
    "ask_human",
    "ask_question",
    "greeting",
    "unknown",
}


def test_parses_a_multi_action_answer() -> None:
    raw = """{"actions": [
        {"type": "add_item", "product": "sencilla", "quantity": 2, "modifiers": ["sin cebolla"]},
        {"type": "set_fulfillment", "mode": "pickup"},
        {"type": "confirm"}
    ]}"""
    assert NluOutput.model_validate_json(raw).actions == (
        AddItem(product="sencilla", quantity=2, modifiers=("sin cebolla",)),
        SetFulfillment(mode="pickup"),
        Confirm(),
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"actions": [{"type": "fly_to_moon"}]}',
        '{"actions": [{"type": "add_item"}]}',
        '{"actions": [{"type": "add_item", "product": "coca", "quantity": 0}]}',
        '{"actions": [{"type": "add_item", "product": "coca", "price": 1000}]}',
        '{"actions": [{"type": "set_fulfillment", "mode": "drone"}]}',
        "no soy json",
    ],
)
def test_rejects_invalid_answers(raw: str) -> None:
    with pytest.raises(ValidationError):
        NluOutput.model_validate_json(raw)


def test_schema_covers_every_action_and_never_prices_or_ids() -> None:
    schema = actions_json_schema()
    types = {d["properties"]["type"]["const"] for d in schema["$defs"].values()}
    assert types == ACTION_TYPES
    fields = {f for d in schema["$defs"].values() for f in d["properties"]}
    assert not {f for f in fields if "price" in f or f.endswith("_id")}


def test_schema_requires_every_field_so_models_emit_them() -> None:
    add_item = actions_json_schema()["$defs"]["AddItem"]
    assert set(add_item["required"]) == set(add_item["properties"])


def test_schema_doc_is_up_to_date() -> None:
    doc = Path(__file__).resolve().parents[3] / "docs" / "actions.schema.json"
    assert json.loads(doc.read_text(encoding="utf-8")) == actions_json_schema(), (
        "run: uv run pedidos-bot nlu schema --output docs/actions.schema.json"
    )
