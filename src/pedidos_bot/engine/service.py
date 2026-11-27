"""Runs one customer message through the pipeline, inside one atomic turn."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from time import perf_counter

import structlog

from pedidos_bot.channels.base import InboundMessage, MessageKind, OutboundMessage, QuickReply
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import CartLine, revalidate
from pedidos_bot.domain.order import Order, OrderStatus
from pedidos_bot.engine.actions import Action, SetAddress, Unknown
from pedidos_bot.engine.conversation import Conversation, Handoff, ResumeOffer
from pedidos_bot.engine.lifecycle import Event, Lifecycle, LifecycleState
from pedidos_bot.engine.nlu import Nlu
from pedidos_bot.engine.policy import Step
from pedidos_bot.engine.resolver import MenuResolver
from pedidos_bot.engine.responder import Responder, join_options
from pedidos_bot.engine.shortcuts import detect, is_back, is_cancel, is_confirm, is_deny
from pedidos_bot.engine.staff import (
    ORDER_STATUS,
    StaffAction,
    StaffDecision,
    StaffReply,
    StaffReturn,
    Verdict,
    customer_left_notice,
    decision_notice,
    handoff_notice,
    not_in_handoff_notice,
    order_notification,
    relay_notice,
    returned_notice,
    stale_notice,
    timeout_notice,
    what_they_sent,
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
    staff: tuple[OutboundMessage, ...] = ()
    """Messages for the staff chat, besides the order notifications."""


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
                result = await self._starting_handoff(
                    scope, await self._process(before, inbound), inbound
                )
                assert result.conversation is not None
                scope.save(result.conversation)
                key = f"{inbound.channel}:{inbound.external_id}"
                for number, message in enumerate(result.staff):
                    self._tell_staff(scope, message, f"{key}:note{number}")
                if result.reply is not None:
                    scope.enqueue(OutboxEntry(inbound.chat_ref, result.reply, key))
                if result.order is not None:
                    scope.place_order(result.order)
                    self._tell_staff(scope, order_notification(result.order, self._config), key)
                if result.withdrawn_order is not None:
                    scope.withdraw_order(result.withdrawn_order)
                    withdrawn = await scope.load_order(result.withdrawn_order)
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
            reply=_loggable_reply(result.reply, result.conversation),
            state_after=result.conversation.lifecycle.state.value,
            actions=result.actions,
            notices=result.notices,
            resolved_without_llm=result.resolved_without_llm,
            latency_ms=_since(started),
        )
        return result

    def _tell_staff(self, scope: TurnScope, message: OutboundMessage, key: str) -> None:
        if self._staff_chat is not None:
            scope.enqueue(OutboxEntry(self._staff_chat, message, f"{key}:staff"))

    # --- handoff ------------------------------------------------------------------------

    async def _starting_handoff(
        self, scope: TurnScope, result: TurnResult, inbound: InboundMessage
    ) -> TurnResult:
        """A turn that hands the customer to a person records it and tells the staff chat."""
        conversation = result.conversation
        assert conversation is not None
        if conversation.lifecycle.state is not LifecycleState.HANDOFF or conversation.handoff:
            return result
        handoff = Handoff(code=conversation.code, since=inbound.sent_at)
        items = [self._responder.line(line) for line in conversation.cart.lines]
        order = await scope.load_order(conversation.id)
        latest = order is not None
        if order is None and conversation.last_order:
            order = await scope.load_order(conversation.last_order)
        about = None
        if order is not None:
            lines = ", ".join(self._responder.line(line) for line in order.cart.lines)
            label = "Pedido en curso" if latest else "Último pedido"
            about = f"{label}: {order.code} ({ORDER_STATUS[order.status]}) — {lines}"
        notice = handoff_notice(handoff.code, what_they_sent(inbound), items, about)
        reply = result.reply
        if reply is not None:  # the way back, so the customer is never stuck with nobody
            back = self._responder.text(Msg.BACK_TO_BOT)
            reply = reply.model_copy(
                update={
                    "text": f"{reply.text}\n\n{self._responder.text(Msg.HANDOFF_HINT)}",
                    "quick_replies": (QuickReply(title=back, payload=back),),
                }
            )
        return replace(
            result,
            conversation=conversation.model_copy(update={"handoff": handoff}),
            reply=reply,
            staff=(*result.staff, notice),
        )

    def _handoff_expired(self, conversation: Conversation, now: datetime) -> bool:
        """Nobody from the restaurant wrote for the configured time. A handoff saved without
        its details (before the relay existed) has nobody on it: it is over."""
        handoff = conversation.handoff
        if handoff is None:
            return True
        timeout = timedelta(minutes=self._config.session.handoff_timeout_minutes)
        return now - handoff.quiet_since >= timeout

    @staticmethod
    def _end_handoff(conversation: Conversation) -> Conversation:
        lifecycle = conversation.lifecycle
        back = lifecycle.apply(Event.HANDOFF_ENDED) if lifecycle.before_handoff else Lifecycle()
        return conversation.model_copy(update={"lifecycle": back, "handoff": None})

    def _back_to_the_bot(self, conversation: Conversation, note: Msg) -> TurnResult:
        """End the handoff and go on with the order, saying why first."""
        result = self._continue(self._end_handoff(conversation))
        return _with_note(result, self._responder.text(note))

    async def _in_handoff(self, conversation: Conversation, inbound: InboundMessage) -> TurnResult:
        code = conversation.code
        if is_back(inbound.content):
            result = self._back_to_the_bot(conversation, Msg.BOT_BACK)
            return replace(result, staff=(customer_left_notice(code, cancelled=False),))
        if is_cancel(inbound.content):
            result = await self._respond(self._end_handoff(conversation), inbound)
            return replace(result, staff=(customer_left_notice(code, cancelled=True),))
        if not self._handoff_expired(conversation, inbound.sent_at):
            return TurnResult(conversation, staff=(relay_notice(conversation.code, inbound),))
        result = await self._respond(self._end_handoff(conversation), inbound)
        if result.reply is not None:
            result = _with_note(result, self._responder.text(Msg.HANDOFF_TIMEOUT))
        minutes = self._config.session.handoff_timeout_minutes
        return replace(result, staff=(timeout_notice(conversation.code, minutes), *result.staff))

    async def expire_handoffs(self, now: datetime) -> int:
        """Give back to the bot every handoff nobody answered in time. Returns how many."""
        expired = 0
        for waiting in await self._repository.in_handoff():
            if not self._handoff_expired(waiting, now):
                continue
            quiet = waiting.handoff.quiet_since.isoformat() if waiting.handoff else "unknown"
            tick = InboundMessage(
                channel=waiting.channel,
                external_id=f"handoff-timeout:{waiting.id}:{quiet}",
                chat_ref=waiting.chat_ref,
                customer_ref=waiting.chat_ref,
                sent_at=now,
            )
            key = f"{tick.channel}:{tick.external_id}"
            async with self._repository.turn(tick) as scope:
                if scope is None:
                    continue
                current = scope.conversation
                if current.lifecycle.state is not LifecycleState.HANDOFF:
                    continue
                if not self._handoff_expired(current, now):
                    continue
                result = self._back_to_the_bot(current, Msg.HANDOFF_TIMEOUT)
                assert result.conversation is not None and result.reply is not None
                scope.save(result.conversation)
                scope.enqueue(OutboxEntry(current.chat_ref, result.reply, key))
                minutes = self._config.session.handoff_timeout_minutes
                self._tell_staff(scope, timeout_notice(current.code, minutes), key)
                expired += 1
            _logger.info("handoff_expired", conversation_id=waiting.id)
        return expired

    async def handle_staff(self, action: StaffAction) -> bool:
        """Apply what someone did in the staff chat. False if it changed nothing."""
        if isinstance(action, StaffDecision):
            return await self._decide(action)
        return await self._from_staff(action)

    async def _from_staff(self, action: StaffReply | StaffReturn) -> bool:
        """A message for a customer in a handoff, or giving them back to the bot."""
        log = _logger.bind(turn_id=f"staff:{action.external_id}", code=action.code)
        conversation = await self._repository.load_by_code(action.code)
        if conversation is None:
            log.warning("staff_message_without_conversation")
            return False
        # The customer's conversation is locked through a message that stands for the action.
        stand_in = InboundMessage(
            channel=conversation.channel,
            external_id=action.external_id,
            chat_ref=conversation.chat_ref,
            customer_ref=conversation.chat_ref,
            sent_at=action.at,
        )
        key = f"{conversation.channel}:{action.external_id}"
        async with self._repository.turn(stand_in) as scope:
            if scope is None:
                log.info("duplicate_message")
                return False
            current = scope.conversation
            handoff = current.handoff
            if current.lifecycle.state is not LifecycleState.HANDOFF or handoff is None:
                self._tell_staff(scope, not_in_handoff_notice(action.code), key)
                log.info("staff_message", applied=False)
                return False
            if isinstance(action, StaffReply):
                awake = handoff.model_copy(update={"last_staff_at": action.at})
                scope.save(current.model_copy(update={"handoff": awake}))
                message = OutboundMessage(text=action.text)
                scope.enqueue(OutboxEntry(current.chat_ref, message, key))
            else:
                result = self._back_to_the_bot(current, Msg.BOT_BACK)
                assert result.conversation is not None and result.reply is not None
                scope.save(result.conversation)
                scope.enqueue(OutboxEntry(current.chat_ref, result.reply, key))
                self._tell_staff(scope, returned_notice(action.code), key)
        log.info("staff_message", applied=True, returned=isinstance(action, StaffReturn))
        return True

    async def _decide(self, decision: StaffDecision) -> bool:
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
            scope.save(conversation.model_copy(update={"lifecycle": lifecycle}))
            status = OrderStatus.CONFIRMED if approved else OrderStatus.REJECTED
            scope.place_order(order.model_copy(update={"status": status}))
            if approved:
                eta = self._responder.eta(order.cart)
                text = self._responder.text(Msg.CONFIRMED, eta=eta, code=order.code)
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
        if conversation.lifecycle.state is LifecycleState.HANDOFF:
            return await self._in_handoff(conversation, inbound)
        if inbound.kind is MessageKind.STICKER:
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
            last_order = conversation.id if confirmed else None
            conversation = _new_conversation(conversation).model_copy(
                update={"follows_confirmed": confirmed, "last_order": last_order}
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
    if conversation.asked is Step.NAME:
        return "[name]"
    return mask_text(inbound.content)


def _loggable_reply(reply: OutboundMessage | None, conversation: Conversation) -> str | None:
    """What the bot answered, safe to log. The summary repeats the address as written."""
    if reply is None:
        return None
    text = reply.text
    address = conversation.cart.address
    if address is not None:
        for part in (address.text, address.details):
            if part:
                text = text.replace(part, "[address]")
    if conversation.cart.customer_name:
        text = text.replace(conversation.cart.customer_name, "[name]")
    return mask_text(text)


def _with_note(result: TurnResult, note: str) -> TurnResult:
    assert result.reply is not None
    text = f"{note}\n\n{result.reply.text}"
    return replace(result, reply=result.reply.model_copy(update={"text": text}))


def _new_conversation(previous: Conversation) -> Conversation:
    return Conversation(
        channel=previous.channel, chat_ref=previous.chat_ref, disclosed=previous.disclosed
    )


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
