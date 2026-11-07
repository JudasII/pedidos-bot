"""The base for value objects: immutable, and strict about unknown fields."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
