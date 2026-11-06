"""The contract between the LLM and the engine: a list of typed actions.

Actions carry the customer's own words, never menu IDs or prices. The resolver turns those
words into IDs, so a model can only ever refer to what exists in the configuration.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class _Action(BaseModel):
    # Defaulted fields are required in the exported schema, so a model must emit all of them.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )


class AddItem(_Action):
    type: Literal["add_item"] = "add_item"
    product: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    modifiers: tuple[str, ...] = ()
    note: str = ""


class RemoveItem(_Action):
    type: Literal["remove_item"] = "remove_item"
    product: str = Field(min_length=1)
    quantity: int | None = Field(default=None, ge=1)
    """How many to remove; null removes the whole line."""


class UpdateItem(_Action):
    type: Literal["update_item"] = "update_item"
    product: str = Field(min_length=1)
    quantity: int | None = Field(default=None, ge=1)
    add_modifiers: tuple[str, ...] = ()
    remove_modifiers: tuple[str, ...] = ()


class SetFulfillment(_Action):
    type: Literal["set_fulfillment"] = "set_fulfillment"
    mode: Literal["delivery", "pickup"]


class SetAddress(_Action):
    type: Literal["set_address"] = "set_address"
    address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    details: str = ""


class SetPayment(_Action):
    type: Literal["set_payment"] = "set_payment"
    method: str = Field(min_length=1)
    cash_change_for: int | None = Field(default=None, ge=0)


class Confirm(_Action):
    type: Literal["confirm"] = "confirm"


class Deny(_Action):
    type: Literal["deny"] = "deny"


class CancelOrder(_Action):
    type: Literal["cancel_order"] = "cancel_order"


class AskHuman(_Action):
    type: Literal["ask_human"] = "ask_human"


class AskQuestion(_Action):
    type: Literal["ask_question"] = "ask_question"
    topic: Literal["menu", "hours", "price", "other"] = "other"
    product: str = ""
    """For `price`: the product, in the customer's words."""


class Greeting(_Action):
    type: Literal["greeting"] = "greeting"


class Unknown(_Action):
    type: Literal["unknown"] = "unknown"


Action = Annotated[
    AddItem
    | RemoveItem
    | UpdateItem
    | SetFulfillment
    | SetAddress
    | SetPayment
    | Confirm
    | Deny
    | CancelOrder
    | AskHuman
    | AskQuestion
    | Greeting
    | Unknown,
    Field(discriminator="type"),
]


class NluOutput(_Action):
    actions: tuple[Action, ...]


def actions_json_schema() -> dict[str, Any]:
    schema = NluOutput.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "pedidos-bot NLU actions"
    return schema
