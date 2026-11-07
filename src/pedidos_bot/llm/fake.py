"""Providers for deterministic tests and the development REPL. No network, no model.

`ScriptedLLM` replays fixed answers. `RuleBasedLLM` is a small parser of common phrasing: far
weaker than a real model, but it follows the same contract, so the engine can't tell.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

from pedidos_bot.engine.actions import (
    Action,
    AddItem,
    AskQuestion,
    Greeting,
    NluOutput,
    RemoveItem,
    SetAddress,
    SetFulfillment,
    SetPayment,
    Unknown,
    UpdateItem,
)
from pedidos_bot.engine.resolver import MatchStatus, MenuResolver
from pedidos_bot.llm.base import LLMRequest, LLMResult, LLMUsage


def _result(text: str, request: LLMRequest, provider: str) -> LLMResult:
    words = sum(len(m.content.split()) for m in request.messages)
    usage = LLMUsage(input_tokens=words, output_tokens=len(text.split()))
    return LLMResult(text=text, usage=usage, latency_ms=0, model="fake", provider=provider)


def _dump(actions: Sequence[Action]) -> str:
    return NluOutput(actions=tuple(actions)).model_dump_json()


class ScriptedLLM:
    """Answers each request with the next scripted turn: actions, or raw text."""

    def __init__(self, turns: Iterable[Sequence[Action] | str] = ()) -> None:
        self._turns = list(turns)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError(f"unexpected LLM call: {request.messages[-1].content!r}")
        turn = self._turns.pop(0)
        return _result(turn if isinstance(turn, str) else _dump(turn), request, "scripted")


_NUMBER_WORDS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
_QUANTITY = re.compile(r"^(\d+|un|una|uno|dos|tres|cuatro|cinco)\s+(.+)$")
_SEGMENTS = re.compile(r",|\by\b")
_GREETING = re.compile(r"^(hola|buenas|buenos dias|buenas tardes|buenas noches)\b[\s!.]*")
_ORDER_VERBS = re.compile(r"^(quiero|quisiera|me das|dame|regalame|me regalas|para mi)\s+")
_REMOVE = re.compile(
    r"^(quita|quitale|quitame|saca|sacame|borra|elimina)\s+(?:(?:el|la|los|las)\s+)?"
)
_SET_QUANTITY = re.compile(r"^(?:mejor\s+)?(?:que sean|deja|dejalo en|cambia a)\s+(.+)$")
_MODIFIER = re.compile(r"^(sin|con)\b")
_VARIANT = re.compile(r"^(?:un|una|uno)\s+((?:sin|con)\b.+)$")
_ADDRESS = re.compile(
    r"\b(calle|cl|carrera|cra|kr|avenida|av|diagonal|dg|transversal|tv|barrio|apto)\b"
)
_PAYMENT = re.compile(r"^(?:pago|pagamos|pagar|pagaria)\s+(?:con|en|por)?\s*(.+)$")
_PRICE = re.compile(r"(?:cuanto (?:cuesta|vale|valen|cuestan|sale)|precio de)\s+(.+?)\??$")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _quantity(segment: str) -> tuple[int, str]:
    match = _QUANTITY.match(segment)
    if match is None:
        return 1, segment
    word = match.group(1)
    return (int(word) if word.isdigit() else _NUMBER_WORDS[word]), match.group(2)


class RuleBasedLLM:
    """Parses simple orders. Unclear segments become `Unknown`, as a cautious model would."""

    def __init__(self, resolver: MenuResolver) -> None:
        self._resolver = resolver

    async def complete(self, request: LLMRequest) -> LLMResult:
        return _result(_dump(self.parse(request.messages[-1].content)), request, "rules")

    def parse(self, text: str) -> list[Action]:
        plain = _plain(text)
        if "?" in plain:
            return [_question(plain)]
        actions: list[Action] = []
        greeting = _GREETING.match(plain)
        if greeting:
            actions.append(Greeting())
            plain = plain[greeting.end() :]
        for segment in (s.strip() for s in _SEGMENTS.split(plain)):
            if segment:
                self._segment(segment, actions)
        return actions or [Unknown()]

    def _segment(self, segment: str, actions: list[Action]) -> None:
        segment = _ORDER_VERBS.sub("", segment)
        last = actions[-1] if actions else None
        if isinstance(last, AddItem):
            variant = _VARIANT.match(segment)
            if variant and last.quantity > 1:
                actions[-1] = last.model_copy(update={"quantity": last.quantity - 1})
                actions.append(AddItem(product=last.product, modifiers=(variant.group(1),)))
                return
            if _MODIFIER.match(segment):
                modifiers = (*last.modifiers, segment)
                actions[-1] = last.model_copy(update={"modifiers": modifiers})
                return
        actions.append(self._action(segment))

    def _action(self, segment: str) -> Action:
        remove = _REMOVE.match(segment)
        if remove:
            target = segment[remove.end() :]
            quantity, product = _quantity(target)
            return RemoveItem(product=product, quantity=quantity if product != target else None)
        set_quantity = _SET_QUANTITY.match(segment)
        if set_quantity:
            quantity, product = _quantity(set_quantity.group(1))
            return UpdateItem(product=product, quantity=quantity)
        if _ADDRESS.search(segment):
            return SetAddress(address=re.sub(r"^(a|en)\s+(la\s+)?", "", segment))
        if "domicilio" in segment:
            return SetFulfillment(mode="delivery")
        if "recoger" in segment or "para llevar" in segment:
            return SetFulfillment(mode="pickup")
        payment = _PAYMENT.match(segment)
        if payment:
            return SetPayment(method=payment.group(1))
        quantity, product = _quantity(segment)
        if self._resolver.resolve_item(product).product.status is MatchStatus.NOT_FOUND:
            return Unknown()
        return AddItem(product=product, quantity=quantity)


def _question(plain: str) -> Action:
    price = _PRICE.search(plain)
    if price:
        return AskQuestion(topic="price", product=price.group(1))
    if re.search(r"\b(menu|carta|que tienen|que venden)\b", plain):
        return AskQuestion(topic="menu")
    if re.search(r"\b(hora|horario|abren|cierran)\b", plain):
        return AskQuestion(topic="hours")
    return AskQuestion(topic="other")
