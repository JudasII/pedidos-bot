"""The rule-based fake. Customer wording is Spanish on purpose."""

import pytest

from pedidos_bot.engine.actions import (
    Action,
    AddItem,
    AskQuestion,
    Greeting,
    RemoveItem,
    SetAddress,
    SetFulfillment,
    SetPayment,
    Unknown,
    UpdateItem,
)
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.llm.base import LLMMessage, LLMRequest
from pedidos_bot.llm.fake import RuleBasedLLM, ScriptedLLM
from tests.factories import sample_config

RULES = RuleBasedLLM(MenuResolver(sample_config("burger_demo")))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hola", [Greeting()]),
        ("una coca grande", [AddItem(product="coca grande")]),
        ("quiero 2 sencillas", [AddItem(product="sencillas", quantity=2)]),
        (
            "2 sencillas, una sin cebolla, y una coca grande",
            [
                AddItem(product="sencillas"),
                AddItem(product="sencillas", modifiers=("sin cebolla",)),
                AddItem(product="coca grande"),
            ],
        ),
        (
            "una doble sin tomate y con tocineta",
            [AddItem(product="doble sin tomate", modifiers=("con tocineta",))],
        ),
        (
            "una doble, a la calle 10, pago en efectivo",
            [
                AddItem(product="doble"),
                SetAddress(address="calle 10"),
                SetPayment(method="efectivo"),
            ],
        ),
        ("a domicilio", [SetFulfillment(mode="delivery")]),
        ("paso a recoger", [SetFulfillment(mode="pickup")]),
        ("quita la coca", [RemoveItem(product="coca")]),
        ("mejor que sean 3 sencillas", [UpdateItem(product="sencillas", quantity=3)]),
        ("¿qué tienen en el menú?", [AskQuestion(topic="menu")]),
        ("¿a qué hora cierran?", [AskQuestion(topic="hours")]),
        ("¿cuánto vale la doble?", [AskQuestion(topic="price", product="la doble")]),
        ("asdf qwer", [Unknown()]),
    ],
)
def test_parses_common_phrasing(text: str, expected: list[Action]) -> None:
    assert RULES.parse(text) == expected


async def test_scripted_llm_fails_loudly_when_called_unexpectedly() -> None:
    request = LLMRequest(messages=(LLMMessage(role="user", content="hola"),))
    with pytest.raises(AssertionError, match="unexpected LLM call"):
        await ScriptedLLM().complete(request)
