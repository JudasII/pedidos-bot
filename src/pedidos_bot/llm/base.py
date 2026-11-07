"""The contract every LLM provider implements."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMMessage(_Model):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(_Model):
    messages: tuple[LLMMessage, ...]
    json_schema: dict[str, Any] | None = None
    temperature: float = 0.0
    max_tokens: int = 512


class LLMUsage(_Model):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LLMResult(_Model):
    text: str
    usage: LLMUsage
    latency_ms: int
    model: str
    provider: str


class LLMError(Exception):
    """The provider could not produce a completion (network, timeout, server error)."""


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResult: ...
