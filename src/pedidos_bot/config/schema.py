"""Per-restaurant configuration schema.

One YAML file per restaurant describes everything restaurant-specific: identity, staff
notification chat, fulfillment modes, payment methods, operating modes, session policy,
tone, menu and business rules. The core never hardcodes any of it.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pedidos_bot.domain.menu import Category, Identifier, Menu, ModifierGroup, Price

EnvVarName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class OpeningHours(_Model):
    days: tuple[Weekday, ...] = Field(min_length=1)
    open: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    close: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class StaffNotification(_Model):
    channel: Literal["telegram"] = "telegram"
    chat_id_env: EnvVarName
    """Name of the environment variable holding the staff chat id (never the id itself)."""


class RestaurantInfo(_Model):
    id: Identifier
    name: str = Field(min_length=1)
    timezone: str = "America/Bogota"
    currency: Literal["COP"] = "COP"
    hours: tuple[OpeningHours, ...] = ()
    notify: StaffNotification

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError) as exc:
            raise ValueError(f"unknown timezone '{value}'") from exc
        return value


class EtaRange(_Model):
    min: int = Field(ge=1)
    max: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.max < self.min:
            raise ValueError("eta max must be >= min")
        return self


class DeliveryOptions(_Model):
    enabled: bool = False
    fee: Price = 0
    eta_minutes: EtaRange | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.enabled and self.eta_minutes is None:
            raise ValueError("enabled delivery requires eta_minutes")
        return self


class PickupOptions(_Model):
    enabled: bool = False
    eta_minutes: EtaRange | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.enabled and self.eta_minutes is None:
            raise ValueError("enabled pickup requires eta_minutes")
        return self


class FulfillmentConfig(_Model):
    delivery: DeliveryOptions = DeliveryOptions()
    pickup: PickupOptions = PickupOptions()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not (self.delivery.enabled or self.pickup.enabled):
            raise ValueError("at least one fulfillment mode (delivery or pickup) must be enabled")
        return self


class PaymentMethod(_Model):
    id: Identifier
    label: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    instructions: str = ""
    ask_change_for: bool = False
    """Ask the customer which bill they will pay with (cash only)."""


class Modes(_Model):
    shadow: bool = True
    """A human must confirm each order before it counts as valid."""
    paraphrase_llm: bool = False


class SessionPolicy(_Model):
    resume_silently_minutes: int = Field(default=120, ge=1)
    resume_prompt_hours: int = Field(default=24, ge=1)
    handoff_timeout_minutes: int = Field(default=30, ge=1)


class ToneStyle(StrEnum):
    INFORMAL = "informal"
    FORMAL = "formal"


class Tone(_Model):
    style: ToneStyle = ToneStyle.INFORMAL
    emoji: Literal["none", "light", "heavy"] = "light"
    greeting: str = Field(min_length=1)


class UpsellRule(_Model):
    type: Literal["upsell"]
    after_categories: tuple[Identifier, ...] = Field(min_length=1)
    """When the cart has items from these categories..."""
    suggest_categories: tuple[Identifier, ...] = Field(min_length=1)
    """...and none from these, suggest them once."""


class MaxQtyPerLineRule(_Model):
    type: Literal["max_qty_per_line"]
    value: int = Field(ge=1)


class AgeNoticeRule(_Model):
    type: Literal["age_notice"]
    categories: tuple[Identifier, ...] = Field(min_length=1)
    message: str = Field(min_length=1)


Rule = Annotated[UpsellRule | MaxQtyPerLineRule | AgeNoticeRule, Field(discriminator="type")]

DEFAULT_MAX_QTY_PER_LINE = 20


class RestaurantConfig(_Model):
    restaurant: RestaurantInfo
    fulfillment: FulfillmentConfig
    payment_methods: tuple[PaymentMethod, ...] = Field(min_length=1)
    modes: Modes = Modes()
    session: SessionPolicy = SessionPolicy()
    tone: Tone
    modifier_groups: tuple[ModifierGroup, ...] = ()
    categories: tuple[Category, ...] = Field(min_length=1)
    rules: tuple[Rule, ...] = ()

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        # Building the Menu validates product/group/category cross-references.
        menu = self.menu
        pm_ids = [p.id for p in self.payment_methods]
        dupes = sorted({i for i in pm_ids if pm_ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate payment method ids: {dupes}")
        category_ids = {c.id for c in menu.categories}
        for index, rule in enumerate(self.rules):
            referenced: tuple[str, ...] = ()
            if isinstance(rule, UpsellRule):
                referenced = rule.after_categories + rule.suggest_categories
            elif isinstance(rule, AgeNoticeRule):
                referenced = rule.categories
            unknown = sorted(set(referenced) - category_ids)
            if unknown:
                raise ValueError(
                    f"rules[{index}] ({rule.type}) references unknown categories: {unknown}"
                )
        return self

    @cached_property
    def menu(self) -> Menu:
        return Menu(modifier_groups=self.modifier_groups, categories=self.categories)

    @property
    def max_qty_per_line(self) -> int:
        for rule in self.rules:
            if isinstance(rule, MaxQtyPerLineRule):
                return rule.value
        return DEFAULT_MAX_QTY_PER_LINE

    def payment_method(self, method_id: str) -> PaymentMethod | None:
        return next((p for p in self.payment_methods if p.id == method_id), None)
