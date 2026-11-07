"""The repository contract, run against every implementation."""

import asyncio
from datetime import UTC, datetime

import psycopg
import pytest

from pedidos_bot.channels.base import InboundMessage, OutboundMessage
from pedidos_bot.domain.cart import Cart, Fulfillment, add_item, set_fulfillment, set_payment
from pedidos_bot.domain.order import Order, OrderSource, create_order
from pedidos_bot.storage.base import ConversationRepository, OutboxEntry
from pedidos_bot.storage.postgres import MIGRATIONS_DIR, PostgresRepository, migrate
from tests.factories import make_config


def _inbound(external_id: str, chat_ref: str = "chat") -> InboundMessage:
    return InboundMessage(
        channel="test",
        external_id=external_id,
        chat_ref=chat_ref,
        customer_ref="customer",
        sent_at=datetime(2026, 11, 6, tzinfo=UTC),
        text="hola",
    )


def _entry(key: str) -> OutboxEntry:
    return OutboxEntry("chat", OutboundMessage(text=key), key)


def _order(qty: int) -> Order:
    config = make_config()
    cart, _ = add_item(Cart(), config, "classic", qty=qty)
    cart = set_fulfillment(cart, config, Fulfillment.PICKUP)
    cart = set_payment(cart, config, "transfer")
    return create_order(cart, config, source=OrderSource.CHAT, idempotency_key="key")


async def _count_turn(repository: ConversationRepository, external_id: str) -> None:
    """Increments `misunderstood`, with a pause so concurrent turns would overlap."""
    async with repository.turn(_inbound(external_id)) as scope:
        assert scope is not None
        current = scope.conversation.misunderstood
        await asyncio.sleep(0.01)
        scope.save(scope.conversation.model_copy(update={"misunderstood": current + 1}))


async def test_a_turn_saves_the_conversation_and_outbox_together(
    repository: ConversationRepository,
) -> None:
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None
        scope.save(scope.conversation.model_copy(update={"greeted": True}))
        scope.enqueue(_entry("k1"))
    saved = await repository.load("test", "chat")
    assert saved is not None and saved.greeted
    assert [e.dedupe_key for e in await repository.pending_outbound()] == ["k1"]


async def test_a_failed_turn_leaves_nothing_and_can_be_retried(
    repository: ConversationRepository,
) -> None:
    with pytest.raises(RuntimeError):
        async with repository.turn(_inbound("1")) as scope:
            assert scope is not None
            scope.save(scope.conversation.model_copy(update={"greeted": True}))
            scope.enqueue(_entry("k1"))
            raise RuntimeError("boom")
    assert await repository.load("test", "chat") is None
    assert await repository.pending_outbound() == []
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None


async def test_a_repeated_message_is_dropped(repository: ConversationRepository) -> None:
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None
    async with repository.turn(_inbound("1")) as scope:
        assert scope is None


async def test_turns_of_one_conversation_run_one_at_a_time(
    repository: ConversationRepository,
) -> None:
    await asyncio.gather(*(_count_turn(repository, str(i)) for i in range(4)))
    saved = await repository.load("test", "chat")
    assert saved is not None and saved.misunderstood == 4


async def test_conversations_are_independent(repository: ConversationRepository) -> None:
    async with repository.turn(_inbound("1", "a")) as scope:
        assert scope is not None
        scope.save(scope.conversation.model_copy(update={"greeted": True}))
    async with repository.turn(_inbound("2", "b")) as scope:
        assert scope is not None and not scope.conversation.greeted


async def test_sent_messages_leave_the_outbox(repository: ConversationRepository) -> None:
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None
        scope.enqueue(_entry("k1"))
        scope.enqueue(_entry("k2"))
    await repository.mark_sent("k1")
    assert [e.dedupe_key for e in await repository.pending_outbound()] == ["k2"]


async def test_saving_an_order_with_the_same_key_revises_it(
    repository: ConversationRepository,
) -> None:
    first = _order(qty=1)
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None
        scope.place_order(first)
    async with repository.turn(_inbound("2")) as scope:
        assert scope is not None
        scope.place_order(_order(qty=2))
    saved = await repository.load_order("key")
    assert saved is not None
    assert saved.id == first.id
    assert saved.cart.lines[0].qty == 2


async def test_an_order_from_a_failed_turn_is_not_saved(
    repository: ConversationRepository,
) -> None:
    with pytest.raises(RuntimeError):
        async with repository.turn(_inbound("1")) as scope:
            assert scope is not None
            scope.place_order(_order(qty=1))
            raise RuntimeError("boom")
    assert await repository.load_order("key") is None


@pytest.mark.postgres
async def test_migrations_run_once(database_url: str) -> None:
    names = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    async with await psycopg.AsyncConnection.connect(database_url) as connection:
        assert await migrate(connection) == names
        assert await migrate(connection) == []


@pytest.mark.postgres
async def test_opening_twice_is_safe(database_url: str) -> None:
    async with PostgresRepository.open(database_url), PostgresRepository.open(database_url):
        pass
