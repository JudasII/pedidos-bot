"""A process restart in the middle of an order, against real PostgreSQL."""

import pytest

from pedidos_bot.channels.memory import InMemoryChannel
from pedidos_bot.engine.lifecycle import LifecycleState
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.service import ConversationService
from pedidos_bot.llm.fake import RuleBasedLLM
from pedidos_bot.storage.base import deliver_pending
from pedidos_bot.storage.postgres import PostgresRepository
from tests.e2e.harness import Chat
from tests.factories import sample_config

CONFIG = sample_config("burger_demo")
pytestmark = pytest.mark.postgres


async def test_the_order_continues_after_a_restart(database_url: str) -> None:
    async with PostgresRepository.open(database_url) as repository:
        chat = Chat(CONFIG, repository=repository)
        await chat.say("una sencilla sin cebolla")
        await chat.say("no")

    async with PostgresRepository.open(database_url) as repository:
        chat = Chat(CONFIG, repository=repository, first_message_id=100)
        await chat.say("para recoger")
        reply = await chat.say("nequi")
        assert "(sin cebolla)" in reply
        assert chat.state is LifecycleState.AWAITING_CONFIRMATION
        await chat.say("sí")
        order = await repository.load_order(chat.conversation.id)
        assert order is not None and order.cart.lines[0].qty == 1


async def test_replies_still_go_out_if_the_process_died_before_sending(database_url: str) -> None:
    async with PostgresRepository.open(database_url) as repository:
        service = ConversationService(CONFIG, RuleBasedLLM(MenuResolver(CONFIG)), repository)
        await service.handle(Chat(CONFIG).message("una sencilla"))
        # the process dies here, with the reply still in the outbox

    async with PostgresRepository.open(database_url) as repository:
        channel = InMemoryChannel()
        assert await deliver_pending(repository, channel) == 1
        assert "Anotado" in channel.sent[0].message.text
        assert await deliver_pending(repository, channel) == 0


async def test_staff_decide_on_an_order_stored_in_postgres(database_url: str) -> None:
    async with PostgresRepository.open(database_url) as repository:
        chat = Chat(CONFIG, repository=repository)
        for text in ("una sencilla", "no", "para recoger", "nequi", "sí"):
            await chat.say(text)
        assert chat.state is LifecycleState.PENDING_STAFF_CONFIRMATION
        key = chat.conversation.id
        sent = await repository.load_order(key)
        await chat.tap("Rechazar")
        rejected = await repository.load_order(key)
        await chat.say("agrega una doble")
        await chat.say("sí")
        assert "confirmado" in await chat.tap("Aprobar")
        approved = await repository.load_order(key)
    assert sent is not None and rejected is not None and approved is not None
    assert [o.status.value for o in (sent, rejected, approved)] == [
        "pending_validation",
        "rejected",
        "confirmed",
    ]
    assert approved.id == sent.id and approved.revision == 2
    assert chat.state is LifecycleState.CONFIRMED
