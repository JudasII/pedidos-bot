"""Runs one customer message through the pipeline, inside one atomic turn."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from time import perf_counter

import structlog

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import CartLine, revalidate
from pedidos_bot.domain.order import Order, OrderStatus
from pedidos_bot.engine.actions import Action, SetAddress, Unknown
from pedidos_bot.engine.conversation import Conversation, ResumeOffer
from pedidos_bot.engine.lifecycle import Event, LifecycleState
from pedidos_bot.engine.nlu import Nlu
from pedidos_bot.engine.policy import Step
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.responder import Responder, join_options
from pedidos_bot.engine.shortcuts import detect, is_confirm, is_deny
from pedidos_bot.engine.staff import (
    StaffDecision,
    Verdict,
    decision_notice,
    order_notification,
    stale_notice,
    withdrawn_notice,
)
from pedidos_bot.engine.templates import Msg
from pedidos_bot.engine.turn import Turn
from pedidos_bot.llm.base import LLMProvider, LLMUsage
from pedidos_bot.observability.masking import hash_customer, mask_text
from pedidos_bot.storage.base import ConversationRepository, OutboxEntry, TurnScope

_logger = structlog.get_logger()


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
    withdrawn_order: str | None = None
    resolved_without_llm: bool = True
    usage: LLMUsage | None = None
    duplicate: bool = False
    actions: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


class ConversationService:
    def __init__(
        self,
        config: RestaurantConfig,
        llm: LLMProvider,
        repository: ConversationRepository,
        pii_salt: str | None = None,
        staff_chat: str | None = None,
    ) -> None:
        self._config = config
        self._staff_chat = staff_chat
        """Where the restaurant reads its orders. None: nobody is told (tests, the REPL)."""
        self._pii_salt = pii_salt or secrets.token_hex(16)
        """Without a configured salt, hashes are still safe but only stable in this process."""
        self._repository = repository
        self._resolver = MenuResolver(config)
        self._responder = Responder(config)
        self._nlu = Nlu(llm, config)

    async def handle(self, inbound: InboundMessage) -> TurnResult:
        started = perf_counter()
        log = _logger.bind(
            turn_id=f"{inbound.channel}:{inbound.external_id}",
            customer=hash_customer(inbound.customer_ref, self._pii_salt),
        )
        try:
            async with self._repository.turn(inbound) as scope:
                if scope is None:
                    log.info("duplicate_message")
                    return TurnResult(duplicate=True)
                before = scope.conversation
                log = log.bind(conversation_id=before.id, state_before=before.lifecycle.state.value)
                result = await self._process(before, inbound)
                assert result.conversation is not None
                scope.save(result.conversation)
                key = f"{inbound.channel}:{inbound.external_id}"
                if result.reply is not None:
                    scope.enqueue(OutboxEntry(inbound.chat_ref, result.reply, key))
                if result.order is not None:
                    order = await self._keeping_its_id(result.order)
                    result = replace(result, order=order)
                    scope.place_order(order)
                    self._tell_staff(scope, order_notification(order, self._config), key)
                if result.withdrawn_order is not None:
                    scope.withdraw_order(result.withdrawn_order)
                    withdrawn = await self._repository.load_order(result.withdrawn_order)
                    if withdrawn is not None:
                        self._tell_staff(scope, withdrawn_notice(withdrawn), f"{key}:withdrawn")
        except Exception as exc:
            # The type only: messages of validation errors can quote what the customer wrote.
            log.error("turn_failed", error=type(exc).__name__, latency_ms=_since(started))
            raise
        assert result.conversation is not None
        log.info(
            "turn",
            conversation_id=result.conversation.id,
            order_id=result.order.id if result.order else None,
            message=_loggable(inbound, before, result.actions),
            state_after=result.conversation.lifecycle.state.value,
            actions=result.actions,
            notices=result.notices,
            resolved_without_llm=result.resolved_without_llm,
            latency_ms=_since(started),
        )
        return result

    async def _keeping_its_id(self, order: Order) -> Order:
        """A revised order is the same order: staff must see the code they already know."""
        if order.revision == 1:
            return order
        previous = await self._repository.load_order(order.idempotency_key)
        return order if previous is None else order.model_copy(update={"id": previous.id})

    def _tell_staff(self, scope: TurnScope, message: OutboundMessage, key: str) -> None:
        if self._staff_chat is not None:
            scope.enqueue(OutboxEntry(self._staff_chat, message, f"{key}:staff"))

    async def handle_staff(self, decision: StaffDecision) -> bool:
        """Apply a staff decision to its order. False if it changed nothing.

        The decision counts only for the revision staff saw: if the customer changed or
        cancelled the order meanwhile, staff are told and nothing happens.
        """
        log = _logger.bind(turn_id=f"staff:{decision.external_id}", verdict=decision.verdict.value)
        order = await self._repository.load_order(decision.order_key)
        if order is None or order.channel is None or order.chat_ref is None:
            log.warning("staff_decision_without_order")
            return False
        approved = decision.verdict is Verdict.APPROVE
        event = Event.STAFF_APPROVED if approved else Event.STAFF_REJECTED
        # The customer's conversation is locked through a message that stands for the action.
        action = InboundMessage(
            channel=order.channel,
            external_id=decision.external_id,
            chat_ref=order.chat_ref,
            customer_ref=order.chat_ref,
            sent_at=decision.at,
        )
        key = f"{order.channel}:{decision.external_id}"
        async with self._repository.turn(action) as scope:
            if scope is None:
                log.info("duplicate_message")
                return False
            conversation = scope.conversation
            current = (
                conversation.id == order.idempotency_key
                and conversation.order_revision == decision.revision
                and order.status is OrderStatus.PENDING_VALIDATION
                and conversation.lifecycle.can(event)
            )
            if not current:
                self._tell_staff(scope, stale_notice(order.code), key)
                log.info("staff_decision", order_id=order.id, applied=False)
                return False
            lifecycle = conversation.lifecycle.apply(event)
            cleared = {"lifecycle": lifecycle, "asked": None, "asked_choice": None, "options": ()}
            scope.save(conversation.model_copy(update=cleared))
            status = OrderStatus.CONFIRMED if approved else OrderStatus.REJECTED
            scope.place_order(order.model_copy(update={"status": status}))
            if approved:
                text = self._responder.text(Msg.CONFIRMED, eta=self._responder.eta(order.cart))
            else:
                text = self._responder.text(Msg.STAFF_REJECTED)
            scope.enqueue(OutboxEntry(order.chat_ref, OutboundMessage(text=text), key))
            self._tell_staff(scope, decision_notice(order, decision.verdict), key)
        log.info("staff_decision", order_id=order.id, applied=True, state_after=lifecycle.state)
        return True

    async def _process(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        result = await self._respond(conversation, inbound)
        assert result.conversation is not None
        # A cart keeps the version it was priced with until it is revalidated.
        updated = result.conversation
        same_cart = updated.id == conversation.id and conversation.cart.lines
        version = conversation.menu_version if same_cart else ""
        stamped = updated.model_copy(
            update={
                "last_inbound_at": inbound.sent_at,
                "menu_version": version or self._config.menu_version,
            }
        )
        return replace(result, conversation=stamped)

    async def _respond(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        in_handoff = conversation.lifecycle.state is LifecycleState.HANDOFF
        if inbound.kind is MessageKind.STICKER and not in_handoff:
            # A sticker asks for nothing: say what we can read and leave the order as it is.
            return TurnResult(
                conversation, OutboundMessage(text=self._responder.text(Msg.TEXT_ONLY))
            )
        offer = conversation.resume_offer
        if offer is not None and offer.cart is not None and self._menu_changed(conversation):
            stale = conversation.model_copy(update={"cart": offer.cart, "resume_offer": None})
            return await self._offer_to_recover(stale, inbound)
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
        if self._menu_changed(conversation) or gap >= timedelta(hours=session.resume_prompt_hours):
            return Comeback.RECOVER
        if gap >= timedelta(minutes=session.resume_silently_minutes):
            return Comeback.ASK
        return Comeback.NONE

    def _menu_changed(self, conversation: Conversation) -> bool:
        return conversation.menu_version != self._config.menu_version

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
        held = offer.message
        if not held or is_confirm(held) or is_deny(held):
            return self._continue(waiting)  # a bare yes or no was for a question long gone
        replay = {"kind": MessageKind.TEXT, "text": held, "reply_payload": None}
        return await self._run_turn(waiting, inbound.model_copy(update=replay))

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
            turn.conversation,
            reply,
            turn.order,
            turn.withdrawn_order,
            resolved_without_llm=not llm,
            usage=usage,
            actions=turn.actions,
            notices=tuple(turn.notices),
        )


def _since(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def _loggable(inbound: InboundMessage, conversation: Conversation, actions: tuple[str, ...]) -> str:
    """The message, safe to log. Addresses have no fixed form, so a pattern isn't enough."""
    if conversation.asked is Step.ADDRESS or "set_address" in actions:
        return "[address]"
    return mask_text(inbound.content)


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
