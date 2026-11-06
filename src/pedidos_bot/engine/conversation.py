"""The state of a conversation between turns. Everything here must survive a restart."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from pedidos_bot.domain.cart import Cart, MissingChoice, ModifierChoice
from pedidos_bot.engine.lifecycle import Lifecycle
from pedidos_bot.engine.policy import Step


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductClarification(_Model):
    """An item waiting for the customer to say which product, with what they already asked."""

    kind: Literal["product"] = "product"
    candidates: tuple[str, ...]
    quantity: int = 1
    modifiers: tuple[str, ...] = ()
    note: str = ""


class ModifierClarification(_Model):
    """A line waiting for the customer to say which option."""

    kind: Literal["modifier"] = "modifier"
    line_id: int
    product_id: str
    candidates: tuple[ModifierChoice, ...]


Clarification = Annotated[ProductClarification | ModifierClarification, Field(discriminator="kind")]


class Conversation(_Model):
    channel: str
    chat_ref: str
    lifecycle: Lifecycle = Lifecycle()
    cart: Cart = Cart()
    greeted: bool = False
    items_done: bool = False
    """The customer finished adding items."""
    upsell_offered: bool = False
    change_asked: bool = False
    misunderstood: int = 0
    """Consecutive turns the bot could not understand."""
    clarifications: tuple[Clarification, ...] = ()
    """Questions still owed to the customer, oldest first."""
    asked: Step | None = None
    """The last question asked, to read a short answer in context."""
    asked_choice: MissingChoice | None = None
    options: tuple[str, ...] = ()
    """Payloads of the last options offered, so a numbered answer can be mapped back."""
