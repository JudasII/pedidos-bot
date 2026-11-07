"""The repository contract, run against every implementation."""

import asyncio
from datetime import timedelta

import psycopg
import pytest
from structlog.testing import capture_logs

from pedidos_bot.channels.base import ChannelError, InboundMessage, OutboundMessage
from pedidos_bot.domain.cart import Cart, Fulfillment, add_item, set_fulfillment, set_payment
from pedidos_bot.domain.order import Order, OrderSource, OrderStatus, create_order
from pedidos_bot.storage import base
from pedidos_bot.storage.base import ConversationRepository, OutboxEntry
from pedidos_bot.storage.postgres import MIGRATIONS_DIR, PostgresRepository, migrate
from tests.factories import make_config, make_inbound


def _inbound(external_id: str, chat_ref: str = "chat") -> InboundMessage:
    return make_inbound(external_id=external_id, chat_ref=chat_ref, text="hola")


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


async def _enqueue(repository: ConversationRepository, *keys: str) -> None:
    async with repository.turn(_inbound("-".join(keys))) as scope:
        assert scope is not None
        for key in keys:
            scope.enqueue(_entry(key))


async def _pending_keys(repository: ConversationRepository) -> list[str]:
    return [entry.dedupe_key for entry in await repository.pending_outbound()]


async def test_sent_messages_leave_the_outbox_in_order(repository: ConversationRepository) -> None:
    await _enqueue(repository, "k1", "k2")
    sent: list[str] = []

    async def send(entry: OutboxEntry) -> None:
        sent.append(entry.dedupe_key)

    assert await repository.deliver_next(send)
    assert await _pending_keys(repository) == ["k2"]
    assert await repository.deliver_next(send)
    assert not await repository.deliver_next(send)
    assert sent == ["k1", "k2"]


async def test_a_failed_send_waits_and_does_not_block_the_others(
    repository: ConversationRepository,
) -> None:
    await _enqueue(repository, "bad", "good")
    sent: list[str] = []

    async def send(entry: OutboxEntry) -> None:
        if entry.dedupe_key == "bad":
            raise ChannelError("down")
        sent.append(entry.dedupe_key)

    assert await repository.deliver_next(send)
    assert await repository.deliver_next(send)
    assert not await repository.deliver_next(send)  # "bad" is not due yet
    assert sent == ["good"]
    assert await _pending_keys(repository) == ["bad"]


async def test_a_failed_send_is_retried_when_due(
    repository: ConversationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base, "BASE_RETRY_DELAY", timedelta(0))
    await _enqueue(repository, "k1")
    attempts = 0

    async def flaky(entry: OutboxEntry) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ChannelError("down")

    assert await repository.deliver_next(flaky)
    assert await repository.deliver_next(flaky)
    assert attempts == 2
    assert await _pending_keys(repository) == []


async def test_a_message_that_keeps_failing_is_given_up(
    repository: ConversationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base, "BASE_RETRY_DELAY", timedelta(0))
    await _enqueue(repository, "k1")
    attempts = 0

    async def down(entry: OutboxEntry) -> None:
        nonlocal attempts
        attempts += 1
        raise ChannelError("down")

    while await repository.deliver_next(down):
        pass
    assert attempts == base.MAX_SEND_ATTEMPTS
    assert await _pending_keys(repository) == []


async def test_giving_up_is_logged(
    repository: ConversationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(base, "BASE_RETRY_DELAY", timedelta(0))
    await _enqueue(repository, "k1")

    async def down(entry: OutboxEntry) -> None:
        raise ChannelError("down")

    with capture_logs() as logs:
        while await repository.deliver_next(down):
            pass
    assert [log["event"] for log in logs] == ["outbound_given_up"]
    assert logs[0]["dedupe_key"] == "k1"


async def test_an_unexpected_send_error_counts_as_a_failure(
    repository: ConversationRepository,
) -> None:
    await _enqueue(repository, "bad", "good")
    sent: list[str] = []

    async def send(entry: OutboxEntry) -> None:
        if entry.dedupe_key == "bad":
            raise ValueError("a bug in the adapter")
        sent.append(entry.dedupe_key)

    while await repository.deliver_next(send):
        pass
    assert sent == ["good"]
    assert await _pending_keys(repository) == ["bad"]


async def test_two_workers_never_send_the_same_message(
    repository: ConversationRepository,
) -> None:
    await _enqueue(repository, "k1", "k2", "k3", "k4")
    sent: list[str] = []

    async def slow(entry: OutboxEntry) -> None:
        await asyncio.sleep(0.01)
        sent.append(entry.dedupe_key)

    async def worker() -> None:
        while await repository.deliver_next(slow):
            pass

    await asyncio.gather(worker(), worker())
    assert sorted(sent) == ["k1", "k2", "k3", "k4"]


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


async def test_a_withdrawn_order_keeps_its_data(repository: ConversationRepository) -> None:
    async with repository.turn(_inbound("1")) as scope:
        assert scope is not None
        scope.place_order(_order(qty=1))
    async with repository.turn(_inbound("2")) as scope:
        assert scope is not None
        scope.withdraw_order("key")
    saved = await repository.load_order("key")
    assert saved is not None
    assert saved.status is OrderStatus.WITHDRAWN
    assert saved.cart.lines[0].qty == 1


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
