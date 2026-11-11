"""What a turn writes to the logs. Customer data here is made up."""

from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from pedidos_bot.config.loader import load_restaurant
from pedidos_bot.engine.actions import AddItem
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.service import ConversationService
from pedidos_bot.llm.fake import RuleBasedLLM, ScriptedLLM
from pedidos_bot.observability.masking import hash_customer
from tests.e2e.harness import Chat

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "restaurants"
MINIMAL = load_restaurant("minimal_demo", CONFIG_DIR)
BURGER = load_restaurant("burger_demo", CONFIG_DIR)


async def _logs(chat: Chat, *messages: str) -> list[dict[str, Any]]:
    with capture_logs() as logs:
        for message in messages:
            await chat.say(message)
    return logs


async def test_a_turn_logs_what_happened() -> None:
    chat = Chat(MINIMAL)
    [log] = await _logs(chat, "un suizo")
    assert log["event"] == "turn"
    assert log["conversation_id"] == chat.conversation.id
    assert log["turn_id"] == "test:1"
    assert log["message"] == "un suizo"
    assert log["state_before"] == log["state_after"] == "ordering"
    assert log["actions"] == ("add_item",)
    assert log["resolved_without_llm"] is False
    assert log["latency_ms"] >= 0
    assert log["order_id"] is None


async def test_confirming_logs_the_order_and_the_new_state() -> None:
    chat = Chat(MINIMAL)
    logs = await _logs(chat, "un suizo", "no", "efectivo", "sí")
    order = await chat.repository.load_order(chat.conversation.id)
    assert order is not None
    assert logs[-1]["order_id"] == order.id
    assert logs[-1]["state_after"] == "confirmed"


async def test_rejected_requests_are_logged() -> None:
    chat = Chat(BURGER, ScriptedLLM([[AddItem(product="sushi")]]))
    [log] = await _logs(chat, "un sushi")
    assert log["notices"] == ("product_not_found",)


async def test_customer_data_stays_out_of_the_logs() -> None:
    chat = Chat(BURGER)
    logs = await _logs(chat, "mi numero es 3001234567", "una doble", "no", "domicilio")
    logs += await _logs(chat, "donde doña Marta frente al parque")
    text = str(logs)
    assert "3001234567" not in text
    assert "Marta" not in text
    assert logs[0]["message"] == "mi numero es [phone]"
    assert logs[-1]["message"] == "[address]"


async def test_a_message_with_an_address_is_masked_whole() -> None:
    chat = Chat(BURGER)
    [log] = await _logs(chat, "una doble, a la calle del rio frente al parque, calle 3")
    assert log["message"] == "[address]"


async def test_the_customer_is_logged_as_a_salted_hash() -> None:
    chat = Chat(MINIMAL)
    chat.service = ConversationService(
        MINIMAL, RuleBasedLLM(MenuResolver(MINIMAL)), chat.repository, pii_salt="salt"
    )
    [log] = await _logs(chat, "hola")
    assert log["customer"] == hash_customer("customer", "salt")


async def test_a_failed_turn_is_logged_without_its_details() -> None:
    chat = Chat(MINIMAL, ScriptedLLM())
    with capture_logs() as logs, pytest.raises(AssertionError):
        await chat.say("llamame al 3001234567")
    [log] = logs
    assert log["event"] == "turn_failed"
    assert log["log_level"] == "error"
    assert log["error"] == "AssertionError"
    assert "3001234567" not in str(log)


async def test_a_duplicate_is_logged() -> None:
    chat = Chat(MINIMAL)
    message = chat.message("hola", external_id="same")
    await chat.send(message)
    with capture_logs() as logs:
        await chat.send(message)
    assert [log["event"] for log in logs] == ["duplicate_message"]
