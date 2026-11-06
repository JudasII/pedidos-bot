"""Runs one customer message through the pipeline, inside one atomic turn."""

from __future__ import annotations

from dataclasses import dataclass

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.engine.actions import Action, SetAddress, Unknown
from pedidos_bot.engine.conversation import Conversation
from pedidos_bot.engine.lifecycle import LifecycleState
from pedidos_bot.engine.nlu import Nlu
from pedidos_bot.engine.policy import Step
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.responder import Responder
from pedidos_bot.engine.shortcuts import detect, is_confirm
from pedidos_bot.engine.templates import Msg
from pedidos_bot.engine.turn import Turn
from pedidos_bot.llm.base import LLMProvider, LLMUsage
from pedidos_bot.storage.base import ConversationRepository, OutboxEntry


@dataclass(frozen=True)
class TurnResult:
    conversation: Conversation | None = None
    reply: OutboundMessage | None = None
    resolved_without_llm: bool = True
    usage: LLMUsage | None = None
    duplicate: bool = False


class ConversationService:
    def __init__(
        self, config: RestaurantConfig, llm: LLMProvider, repository: ConversationRepository
    ) -> None:
        self._config = config
        self._repository = repository
        self._resolver = MenuResolver(config)
        self._responder = Responder(config)
        self._nlu = Nlu(llm, config)

    async def handle(self, inbound: InboundMessage) -> TurnResult:
        async with self._repository.turn(inbound) as scope:
            if scope is None:
                return TurnResult(duplicate=True)
            result = await self._process(scope.conversation, inbound)
            assert result.conversation is not None
            scope.save(result.conversation)
            if result.reply is not None:
                key = f"{inbound.channel}:{inbound.external_id}"
                scope.enqueue(OutboxEntry(inbound.chat_ref, result.reply, key))
            return result

    async def _process(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        state = conversation.lifecycle.state
        if state is LifecycleState.HANDOFF:
            return TurnResult(conversation)  # a person is answering
        if state is LifecycleState.CONFIRMED and is_confirm(inbound.content):
            reply = OutboundMessage(text=self._responder.text(Msg.ALREADY_CONFIRMED))
            return TurnResult(conversation, reply)
        if conversation.lifecycle.is_terminal:
            conversation = Conversation(
                channel=conversation.channel, chat_ref=conversation.chat_ref
            )
        inbound = _read_numbered_option(inbound, conversation)
        turn = Turn(conversation, self._config, self._resolver, self._responder)
        if turn.answer_pending(inbound.content):
            return self._finish(turn)
        shortcut = detect(inbound, self._resolver)
        if shortcut is not None:
            turn.apply(shortcut)
            return self._finish(turn)
        understanding = await self._nlu.interpret(inbound.content)
        turn.apply(_with_address_fallback(understanding.actions, conversation, inbound.content))
        return self._finish(turn, llm=True, usage=understanding.usage)

    @staticmethod
    def _finish(turn: Turn, *, llm: bool = False, usage: LLMUsage | None = None) -> TurnResult:
        reply = turn.reply()
        return TurnResult(turn.conversation, reply, resolved_without_llm=not llm, usage=usage)


def _read_numbered_option(inbound: InboundMessage, conversation: Conversation) -> InboundMessage:
    """Map "2" to the second option offered, for channels that showed a numbered list."""
    text = inbound.content
    options = conversation.options
    if inbound.kind is MessageKind.TEXT and text.isdigit() and 1 <= int(text) <= len(options):
        return inbound.model_copy(update={"text": options[int(text) - 1]})
    return inbound


def _with_address_fallback(
    actions: tuple[Action, ...], conversation: Conversation, text: str
) -> tuple[Action, ...]:
    """Addresses have no fixed form: if we asked for one, an unclear answer is the address."""
    if conversation.asked is Step.ADDRESS and all(isinstance(a, Unknown) for a in actions):
        return (SetAddress(address=text),)
    return actions
