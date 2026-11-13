"""A customer talking to the real engine, with only the LLM replaced."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    InboundMessage,
    MessageKind,
    OutboundMessage,
)
from pedidos_bot.channels.memory import WHATSAPP_LIKE, InMemoryChannel
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.engine.lifecycle import LifecycleState
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.service import ConversationService, TurnResult
from pedidos_bot.engine.staff import parse_staff
from pedidos_bot.llm.base import LLMProvider
from pedidos_bot.llm.fake import RuleBasedLLM
from pedidos_bot.storage.base import ConversationRepository, deliver_pending
from pedidos_bot.storage.memory import InMemoryRepository


class Chat:
    def __init__(
        self,
        config: RestaurantConfig,
        llm: LLMProvider | None = None,
        capabilities: ChannelCapabilities = WHATSAPP_LIKE,
        repository: ConversationRepository | None = None,
        first_message_id: int = 1,
        staff_chat: str | None = "staff",
    ) -> None:
        self.repository = repository or InMemoryRepository()
        self.channel = InMemoryChannel(capabilities)
        self.staff_chat = staff_chat
        self.service = ConversationService(
            config,
            llm or RuleBasedLLM(MenuResolver(config)),
            self.repository,
            staff_chat=staff_chat,
        )
        self._ids = count(first_message_id)
        self._now = datetime(2026, 11, 6, 12, tzinfo=UTC)
        self.last: TurnResult | None = None
        self._conversation: Conversation | None = None

    def message(
        self, text: str | None, kind: MessageKind = MessageKind.TEXT, external_id: str | None = None
    ) -> InboundMessage:
        return InboundMessage(
            channel="test",
            external_id=external_id or str(next(self._ids)),
            chat_ref="chat",
            customer_ref="customer",
            sent_at=self._now,
            kind=kind,
            text=text,
        )

    async def send(self, message: InboundMessage) -> str:
        """Deliver a message and return what the customer sees ("" if nothing)."""
        before = len(self.channel.sent)
        await self.channel.receive(message)
        self.last = await self.service.handle(message)
        self._conversation = self.last.conversation or self._conversation
        await deliver_pending(self.repository, self.channel)
        return self._seen_since(before)

    def _seen_since(self, before: int) -> str:
        new = [sent for sent in self.channel.sent[before:] if sent.chat_ref == "chat"]
        return "\n".join(sent.message.text for sent in new)

    @property
    def staff(self) -> list[OutboundMessage]:
        """Everything the restaurant's chat has received."""
        return [sent.message for sent in self.channel.sent if sent.chat_ref == self.staff_chat]

    async def tap(self, title: str, on: OutboundMessage | None = None) -> str:
        """A cashier taps a button of a staff message (the last one with buttons by default).

        Returns what the customer sees.
        """
        message = on or next(m for m in reversed(self.staff) if m.quick_replies)
        payload = next(r.payload for r in message.quick_replies if title in r.title)
        assert self.staff_chat is not None
        action = InboundMessage(
            channel="test",
            external_id=str(next(self._ids)),
            chat_ref=self.staff_chat,
            customer_ref="cashier",
            sent_at=self._now,
            kind=MessageKind.INTERACTIVE_REPLY,
            reply_payload=payload,
        )
        decision = parse_staff(action)
        assert decision is not None
        before = len(self.channel.sent)
        await self.service.handle_staff(decision)
        loaded = await self.repository.load("test", "chat")
        self._conversation = loaded or self._conversation
        await deliver_pending(self.repository, self.channel)
        return self._seen_since(before)

    def wait(self, **delta: float) -> None:
        self._now += timedelta(**delta)

    async def say(self, text: str) -> str:
        return await self.send(self.message(text))

    @property
    def reply(self) -> OutboundMessage:
        return next(s.message for s in reversed(self.channel.sent) if s.chat_ref == "chat")

    @property
    def conversation(self) -> Conversation:
        assert self._conversation is not None
        return self._conversation

    @property
    def state(self) -> LifecycleState:
        return self.conversation.lifecycle.state

    @property
    def lines(self) -> list[tuple[str, int, set[str]]]:
        return [
            (line.product_id, line.qty, {m.option_id for m in line.modifiers})
            for line in self.conversation.cart.lines
        ]
