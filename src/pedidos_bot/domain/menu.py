"""Menu model: products, categories and modifier groups.

These models carry no restaurant-specific data; a restaurant's menu is loaded from its
YAML configuration (see `pedidos_bot.config`).
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from pedidos_bot.models import FrozenModel

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=64)]
"""Stable machine identifier: lowercase letters, digits and underscores."""

Price = Annotated[int, Field(ge=0)]
"""Amount in the restaurant's currency minor-less unit (e.g. whole COP)."""


class ModifierGroupType(StrEnum):
    REMOVE = "remove"
    """Remove an ingredient ("sin champiñones"). Usually free, multi-select."""
    ADD = "add"
    """Paid or free add-ons ("aceitunas extra"). Multi-select."""
    CHOOSE_ONE = "choose_one"
    """Pick exactly one option, e.g. size or flavor."""


class ModifierOption(FrozenModel):
    id: Identifier
    label: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    price: Price = 0


class ModifierGroup(FrozenModel):
    id: Identifier
    label: str = Field(min_length=1)
    type: ModifierGroupType
    required: bool = False
    max_select: int | None = Field(default=None, ge=1)
    options: tuple[ModifierOption, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [o.id for o in self.options]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate option ids in group '{self.id}': {dupes}")
        if self.type is ModifierGroupType.CHOOSE_ONE and self.max_select not in (None, 1):
            raise ValueError(f"choose_one group '{self.id}' cannot have max_select > 1")
        return self

    @property
    def max_selections(self) -> int:
        if self.type is ModifierGroupType.CHOOSE_ONE:
            return 1
        return self.max_select if self.max_select is not None else len(self.options)

    def option(self, option_id: str) -> ModifierOption | None:
        return next((o for o in self.options if o.id == option_id), None)


class Product(FrozenModel):
    id: Identifier
    name: str = Field(min_length=1)
    price: Price
    aliases: tuple[str, ...] = ()
    description: str = ""
    modifier_groups: tuple[Identifier, ...] = ()
    available: bool = True


class Category(FrozenModel):
    id: Identifier
    name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    """Generic words for the category ("pizza", "pasta"). They never resolve to a single
    product on their own; they produce a clarification listing the category's products."""
    products: tuple[Product, ...] = Field(min_length=1)


class Menu(FrozenModel):
    """A restaurant's full menu with cross-reference validation."""

    modifier_groups: tuple[ModifierGroup, ...] = ()
    categories: tuple[Category, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        group_ids = [g.id for g in self.modifier_groups]
        _ensure_unique("modifier group", group_ids)
        _ensure_unique("category", [c.id for c in self.categories])
        _ensure_unique("product", [p.id for p in self.products()])
        known = set(group_ids)
        for product in self.products():
            unknown = [g for g in product.modifier_groups if g not in known]
            if unknown:
                raise ValueError(
                    f"product '{product.id}' references unknown modifier groups: {unknown}"
                )
        return self

    def products(self) -> Iterator[Product]:
        for category in self.categories:
            yield from category.products

    def product(self, product_id: str) -> Product | None:
        return next((p for p in self.products() if p.id == product_id), None)

    def group(self, group_id: str) -> ModifierGroup | None:
        return next((g for g in self.modifier_groups if g.id == group_id), None)

    def category_of(self, product_id: str) -> Category | None:
        return next(
            (c for c in self.categories if any(p.id == product_id for p in c.products)), None
        )

    def groups_for(self, product: Product) -> tuple[ModifierGroup, ...]:
        return tuple(g for g in self.modifier_groups if g.id in product.modifier_groups)


def _ensure_unique(kind: str, ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate {kind} ids: {dupes}")
