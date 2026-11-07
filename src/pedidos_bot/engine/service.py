"""Runs one customer message through the pipeline, inside one atomic turn."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import CartLine, revalidate
from pedidos_bot.domain.order import Order
from pedidos_bot.engine.actions import Action, SetAddress, Unknown
from pedidos_bot.engine.conversation import Conversation, ResumeOffer
from pedidos_bot.engine.lifecycle import LifecycleState
from pedidos_bot.engine.nlu import Nlu
from pedidos_bot.engine.policy import Step
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.responder import Responder, join_options
from pedidos_bot.engine.shortcuts import detect, is_confirm, is_deny
from pedidos_bot.engine.templates import Msg
from pedidos_bot.engine.turn import Turn
from pedidos_bot.llm.base import LLMProvider, LLMUsage
from pedidos_bot.storage.base import ConversationRepository, OutboxEntry


class Comeback(StrEnum):
    NONE = "none"
    ASK = "ask"
    RECOVER = "recover"


_RESUMABLE = (LifecycleState.ORDERING, LifecycleState.AWAITING_CONFIRMATION)


@dataclass(frozen=True)
class TurnResult:
    conversation: Conversation | None = None
    reply: OutboundMessage | None = None
    order: Order | None = None
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
            if result.order is not None:
                scope.place_order(result.order)
            return result

    async def _process(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        result = await self._respond(conversation, inbound)
        assert result.conversation is not None
        stamped = result.conversation.model_copy(
            update={
                "last_inbound_at": inbound.sent_at,
                "menu_version": self._config.menu_version,
            }
        )
        return replace(result, conversation=stamped)

    async def _respond(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        offer = conversation.resume_offer
        if offer is not None:
            return await self._answer_resume_offer(conversation, offer, inbound)
        comeback = self._comeback(conversation, inbound)
        if comeback is Comeback.ASK:
            return self._offer_to_continue(conversation, inbound)
        if comeback is Comeback.RECOVER:
            return await self._offer_to_recover(conversation, inbound)
        return await self._run_turn(conversation, inbound)

    def _comeback(self, conversation: Conversation, inbound: InboundMessage) -> Comeback:
        """Whether a returning customer must be asked before going on."""
        last = conversation.last_inbound_at
        working = conversation.lifecycle.state in _RESUMABLE and conversation.cart.lines
        is_text = inbound.kind in (MessageKind.TEXT, MessageKind.INTERACTIVE_REPLY)
        if last is None or not working or not is_text:
            return Comeback.NONE
        session = self._config.session
        gap = inbound.sent_at - last
        if conversation.menu_version != self._config.menu_version or gap >= timedelta(
            hours=session.resume_prompt_hours
        ):
            return Comeback.RECOVER
        if gap >= timedelta(minutes=session.resume_silently_minutes):
            return Comeback.ASK
        return Comeback.NONE

    def _offer_to_continue(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        items = self._items(conversation.cart.lines)
        text = self._responder.text(Msg.RESUME_PROMPT, items=items)
        offer = ResumeOffer(message=inbound.content)
        return self._ask_resume(conversation.model_copy(update={"resume_offer": offer}), text)

    async def _offer_to_recover(
        self, conversation: Conversation, inbound: InboundMessage
    ) -> TurnResult:
        cart, dropped = revalidate(conversation.cart, self._config)
        fresh = _new_conversation(conversation).model_copy(
            update={
                "greeted": True,
                "items_done": conversation.items_done,
                "upsell_offered": True,
                "change_asked": conversation.change_asked,
            }
        )
        notes = []
        if dropped:
            notes.append(self._responder.text(Msg.NO_LONGER_AVAILABLE, items=self._items(dropped)))
        if not cart.lines:
            result = await self._run_turn(fresh, inbound)
            return _with_note(result, notes[0]) if notes else result
        notes.append(self._responder.text(Msg.RECOVER_PROMPT, items=self._items(cart.lines)))
        offer = ResumeOffer(cart=cart, message=inbound.content)
        return self._ask_resume(
            fresh.model_copy(update={"resume_offer": offer}), "\n\n".join(notes)
        )

    def _ask_resume(self, conversation: Conversation, text: str) -> TurnResult:
        replies = self._responder.yes_no()
        options = tuple(reply.payload for reply in replies)
        return TurnResult(
            conversation.model_copy(update={"options": options}),
            OutboundMessage(text=text, quick_replies=replies),
        )

    async def _answer_resume_offer(
        self, conversation: Conversation, offer: ResumeOffer, inbound: InboundMessage
    ) -> TurnResult:
        inbound = _read_numbered_option(inbound, conversation)
        waiting = conversation.model_copy(update={"resume_offer": None, "options": ()})
        if is_deny(inbound.content):
            fresh = _new_conversation(waiting).model_copy(update={"greeted": True})
            return self._continue(fresh)
        if not is_confirm(inbound.content):
            return await self._run_turn(waiting, inbound)
        if offer.cart is not None:
            waiting = waiting.model_copy(update={"cart": offer.cart})
        if offer.message:
            return await self._run_turn(waiting, inbound.model_copy(update={"text": offer.message}))
        return self._continue(waiting)

    def _continue(self, conversation: Conversation) -> TurnResult:
        turn = Turn(conversation, self._config, self._resolver, self._responder)
        turn.resume()
        return self._finish(turn)

    def _items(self, lines: Iterable[CartLine]) -> str:
        return join_options([self._responder.line(line) for line in lines], "y")

    async def _run_turn(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        state = conversation.lifecycle.state
        if state is LifecycleState.HANDOFF:
            return TurnResult(conversation)  # a person is answering
        if state is LifecycleState.CONFIRMED and is_confirm(inbound.content):
            reply = OutboundMessage(text=self._responder.text(Msg.ALREADY_CONFIRMED))
            return TurnResult(conversation, reply)
        if conversation.lifecycle.is_terminal:
            confirmed = state is LifecycleState.CONFIRMED
            conversation = _new_conversation(conversation).model_copy(
                update={"follows_confirmed": confirmed}
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
        return TurnResult(
            turn.conversation, reply, turn.order, resolved_without_llm=not llm, usage=usage
        )


def _with_note(result: TurnResult, note: str) -> TurnResult:
    assert result.reply is not None
    text = f"{note}\n\n{result.reply.text}"
    return replace(result, reply=result.reply.model_copy(update={"text": text}))


def _new_conversation(previous: Conversation) -> Conversation:
    return Conversation(channel=previous.channel, chat_ref=previous.chat_ref)


def _read_numbered_option(inbound: InboundMessage, conversation: Conversation) -> InboundMessage:
    """Map "2" to the second option offered, for channels that showed a numbered list."""
    text = inbound.content
    options = conversation.options
    if inbound.kind is MessageKind.TEXT and text.isdecimal() and 1 <= int(text) <= len(options):
        return inbound.model_copy(update={"text": options[int(text) - 1]})
    return inbound


def _with_address_fallback(
    actions: tuple[Action, ...], conversation: Conversation, text: str
) -> tuple[Action, ...]:
    """Addresses have no fixed form: if we asked for one, an unclear answer is the address."""
    unclear = all(isinstance(a, Unknown) for a in actions)
    if conversation.asked is Step.ADDRESS and unclear and text:
        return (SetAddress(address=text),)
    return actions
