"""The state of a conversation between turns. Everything here must survive a restart."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

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
    """An item waiting for the customer to say which option.

    The item stays out of the cart until then: added earlier, it could merge with an identical
    line, and the answer would change units the customer never mentioned.
    """

    kind: Literal["modifier"] = "modifier"
    product_id: str
    candidates: tuple[ModifierChoice, ...]
    quantity: int = 1
    chosen: tuple[ModifierChoice, ...] = ()
    """Options already settled for this item."""
    modifiers: tuple[str, ...] = ()
    """Modifier texts still to be resolved."""
    note: str = ""


Clarification = Annotated[ProductClarification | ModifierClarification, Field(discriminator="kind")]


class ResumeOffer(_Model):
    """The bot asked a returning customer whether to go on."""

    cart: Cart | None = None
    """The cart to recover in a new conversation; None continues the current one."""
    message: str = ""
    """What the customer said on returning, handled if they choose to go on."""


class Conversation(_Model):
    id: str = Field(default_factory=lambda: uuid4().hex)
    """Identifies this order session; a chat gets a new one for each order."""
    channel: str
    chat_ref: str
    last_inbound_at: datetime | None = None
    menu_version: str = ""
    """The menu this cart was priced with."""
    resume_offer: ResumeOffer | None = None
    lifecycle: Lifecycle = Lifecycle()
    cart: Cart = Cart()
    follows_confirmed: bool = False
    """The previous order of this chat was confirmed, so "cancel" may be about that one."""
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
