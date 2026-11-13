"""Customers are told they talk to a bot and how to reach a person (#42)."""

import pytest

from pedidos_bot.channels.base import MessageKind
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.lifecycle import LifecycleState as S
from tests.e2e.harness import Chat
from tests.factories import sample_config

BURGER = sample_config("burger_demo")
MINIMAL = sample_config("minimal_demo")
BOTH = pytest.mark.parametrize("config", [BURGER, MINIMAL], ids=["informal", "formal"])
ORDER = {BURGER.restaurant.id: "una sencilla", MINIMAL.restaurant.id: "un perrito"}


@BOTH
async def test_the_first_message_says_it_is_an_automated_assistant(
    config: RestaurantConfig,
) -> None:
    chat = Chat(config)
    first = await chat.say("hola")
    assert f"asistente automático de {config.restaurant.name}" in first
    assert "“asesor”" in first
    assert first.endswith(config.tone.greeting)
    assert "asistente automático" not in await chat.say(ORDER[config.restaurant.id])


@BOTH
async def test_a_customer_who_opens_with_an_order_is_told_too(config: RestaurantConfig) -> None:
    chat = Chat(config)
    assert "asistente automático" in await chat.say(ORDER[config.restaurant.id])
    assert chat.lines


@BOTH
async def test_the_first_misunderstanding_offers_a_person(config: RestaurantConfig) -> None:
    chat = Chat(config)
    await chat.say("hola")
    reply = await chat.say("xyzzy plugh")
    assert "entendí" in reply and "persona" in reply and "“asesor”" in reply
    assert chat.state is S.ORDERING
    await chat.say("asesor")
    assert chat.state is S.HANDOFF


async def test_the_start_command_greets_without_the_llm() -> None:
    chat = Chat(BURGER)
    reply = await chat.say("/start")
    assert reply.endswith(BURGER.tone.greeting)
    assert "entendí" not in reply
    assert chat.last is not None and chat.last.resolved_without_llm
    assert chat.conversation.misunderstood == 0


@BOTH
async def test_a_sticker_is_answered_and_changes_nothing(config: RestaurantConfig) -> None:
    chat = Chat(config)
    await chat.say(ORDER[config.restaurant.id])
    lines = chat.lines
    reply = await chat.send(chat.message(None, MessageKind.STICKER))
    assert "mensajes de texto" in reply
    assert chat.state is S.ORDERING
    assert chat.lines == lines
    assert chat.conversation.misunderstood == 0


async def test_a_sticker_during_a_handoff_gets_no_answer() -> None:
    chat = Chat(BURGER)
    await chat.say("asesor")
    assert await chat.send(chat.message(None, MessageKind.STICKER)) == ""
    assert chat.state is S.HANDOFF
