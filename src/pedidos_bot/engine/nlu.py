"""Turns a customer message into actions through the LLM.

A bad answer is retried once with the validation error. If the model still fails, or the
provider is down, the turn gets a single `Unknown` action: the conversation never crashes.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.actions import Action, NluOutput, Unknown, actions_json_schema
from pedidos_bot.llm.base import LLMError, LLMMessage, LLMProvider, LLMRequest, LLMUsage

MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class NluResult:
    actions: tuple[Action, ...]
    usage: LLMUsage
    latency_ms: int
    attempts: int
    failed: bool = False


def system_prompt(config: RestaurantConfig) -> str:
    """Placeholder prompt until the real model arrives; it never includes IDs or prices."""
    products = ", ".join(p.name for p in config.menu.products() if p.available)
    return (
        f"Eres el asistente de pedidos de {config.restaurant.name}. "
        "Convierte el mensaje del cliente en acciones JSON según el esquema, "
        "usando sus propias palabras. Nunca inventes productos ni precios. "
        f"Menú: {products}."
    )


class Nlu:
    def __init__(self, llm: LLMProvider, config: RestaurantConfig) -> None:
        self._llm = llm
        self._system = LLMMessage(role="system", content=system_prompt(config))
        self._schema = actions_json_schema()

    async def interpret(self, text: str) -> NluResult:
        messages = [self._system, LLMMessage(role="user", content=text)]
        usage, latency = LLMUsage(), 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = LLMRequest(messages=tuple(messages), json_schema=self._schema)
            try:
                result = await self._llm.complete(request)
            except LLMError:
                return NluResult((Unknown(),), usage, latency, attempt, failed=True)
            usage, latency = usage + result.usage, latency + result.latency_ms
            try:
                output = NluOutput.model_validate_json(result.text)
            except ValidationError as exc:
                messages += [
                    LLMMessage(role="assistant", content=result.text),
                    LLMMessage(role="user", content=f"JSON inválido, corrígelo: {exc}"),
                ]
                continue
            return NluResult(output.actions or (Unknown(),), usage, latency, attempt)
        return NluResult((Unknown(),), usage, latency, MAX_ATTEMPTS, failed=True)
