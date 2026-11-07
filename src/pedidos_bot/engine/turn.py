"""One turn of a conversation: applies what the customer said and works out the reply.

Pure logic with no I/O. Rejected input never raises: it becomes a note in the reply.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from pedidos_bot.channels.base import OutboundMessage, QuickReply
from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain import cart as carts
from pedidos_bot.domain.cart import Cart, CartError, CartLine, Fulfillment, ModifierChoice
from pedidos_bot.domain.money import format_cop
from pedidos_bot.domain.order import Order, OrderSource, create_order
from pedidos_bot.engine.actions import (
    Action,
    AddItem,
    AskHuman,
    AskQuestion,
    CancelOrder,
    Confirm,
    Deny,
    Greeting,
    RemoveItem,
    SetAddress,
    SetFulfillment,
    SetPayment,
    UpdateItem,
)
from pedidos_bot.engine.conversation import (
    Clarification,
    Conversation,
    ModifierClarification,
    ProductClarification,
)
from pedidos_bot.engine.lifecycle import Event, LifecycleState
from pedidos_bot.engine.policy import NextStep, Step, next_step
from pedidos_bot.engine.resolver import Match, MatchStatus, MenuResolver
from pedidos_bot.engine.responder import Responder, join_options
from pedidos_bot.engine.shortcuts import is_confirm, is_deny, is_done_adding
from pedidos_bot.engine.templates import Msg

MAX_MISUNDERSTOOD = 2
"""Consecutive turns without understanding before a person takes over."""

_SLOT_ACTIONS = (SetFulfillment, SetAddress, SetPayment)
_AMOUNT = re.compile(r"(?:(?:pago )?con |un billete de |de )?\$?\s*(\d[\d.,]*)\s*(mil|k)?")
_EXACT_CASH = re.compile(r"\b(exacto|exacta|justo|completo|sencillo)\b")


def parse_amount(text: str) -> int | None:
    """A cash amount in the ways people write it: 50000, 50.000, $50.000, 50 mil, 50k."""
    match = _AMOUNT.fullmatch(text.lower().strip())
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) * (1000 if match.group(2) else 1)


class Turn:
    def __init__(
        self,
        conversation: Conversation,
        config: RestaurantConfig,
        resolver: MenuResolver,
        responder: Responder,
    ) -> None:
        self.conversation = conversation
        self.order: Order | None = None
        """Set when the customer confirms: the order this turn must save."""
        self._config = config
        self._resolver = resolver
        self._responder = responder
        self._notes: list[str] = []
        self._added: list[str] = []
        self._final: str | None = None
        self._understood = False
        self._cart_changed = False
        self._asking_for_more = False

    @property
    def _cart(self) -> Cart:
        return self.conversation.cart

    def _update(self, **fields: Any) -> None:
        self.conversation = self.conversation.model_copy(update=fields)

    def _event(self, event: Event) -> None:
        self._update(lifecycle=self.conversation.lifecycle.apply(event))

    def _set_cart(self, cart: Cart) -> None:
        self._update(cart=cart)
        self._cart_changed = True
        self._understood = True

    def _note(self, key: Msg, **params: str) -> None:
        """Tell the customer something. Having an answer for them means we understood."""
        self._notes.append(self._responder.text(key, **params))
        self._understood = True

    # --- answers to the question just asked -----------------------------------------------

    def answer_pending(self, text: str) -> bool:
        """Read `text` as the answer to what was asked. False if it isn't one."""
        pending = self.conversation.clarifications
        if pending and self._answer_clarification(pending[0], text):
            return True
        if self.conversation.asked is Step.ANYTHING_ELSE and is_done_adding(text):
            self._update(items_done=True)
            self._understood = True
            return True
        if self.conversation.asked is Step.REQUIRED_CHOICE:
            return self._answer_required_choice(text)
        if self.conversation.asked is Step.CASH_CHANGE:
            return self._answer_cash_change(text)
        return False

    def _pop_clarification(self) -> None:
        self._update(clarifications=self.conversation.clarifications[1:])
        self._understood = True

    def _answer_clarification(self, head: Clarification, text: str) -> bool:
        if is_deny(text):
            self._pop_clarification()
            if isinstance(head, ModifierClarification):
                self._add_clarified(head)
            return True
        if isinstance(head, ProductClarification):
            return self._answer_product(head, text)
        return self._answer_modifier(head, text)

    def _answer_product(self, head: ProductClarification, text: str) -> bool:
        item = self._resolver.resolve_item(text)
        ids = [c.id for c in item.product.candidates]
        if len(head.candidates) == 1 and is_confirm(text):
            ids = list(head.candidates)
        elif len(ids) != 1 or ids[0] not in head.candidates:
            return False  # it may be another request, so its words must not be lost
        self._pop_clarification()
        queries = [*head.modifiers, *(m.query for m in item.modifiers)]
        modifiers = [self._resolver.resolve_modifier(ids[0], q) for q in queries]
        self._add_line(ids[0], head.quantity, modifiers, head.note)
        return True

    def _answer_modifier(self, head: ModifierClarification, text: str) -> bool:
        if len(head.candidates) == 1 and is_confirm(text):
            choice = head.candidates[0]
        else:
            match = self._resolver.resolve_modifier(head.product_id, text)
            ids = [c.id for c in match.candidates]
            options = [c for c in head.candidates if c.option_id in ids]
            if len(options) != 1 or match.leftover:
                return False
            choice = options[0]
        self._pop_clarification()
        self._add_clarified(head, choice)
        return True

    def _add_clarified(
        self, head: ModifierClarification, choice: ModifierChoice | None = None
    ) -> None:
        modifiers = [self._resolver.resolve_modifier(head.product_id, q) for q in head.modifiers]
        chosen = (*head.chosen, choice) if choice else head.chosen
        self._add_line(head.product_id, head.quantity, modifiers, head.note, chosen)

    def _answer_required_choice(self, text: str) -> bool:
        asked = self.conversation.asked_choice
        if asked is None:
            return False
        match = self._resolver.resolve_modifier(asked.product_id, text)
        best = match.best
        if best is None or best.group_id != asked.group_id or match.leftover:
            return False
        choice = ModifierChoice(group_id=asked.group_id, option_id=best.id)
        self._add_modifier(asked.line_id, asked.product_id, choice)
        return True

    def _answer_cash_change(self, text: str) -> bool:
        payment = self._cart.payment
        if payment is None:
            return False
        amount = parse_amount(text)
        if amount is not None:
            cart = carts.set_payment(self._cart, self._config, payment.method_id, amount)
            self._set_cart(cart)
            return True
        if _EXACT_CASH.search(text.lower()) or is_confirm(text) or is_deny(text):
            self._understood = True
            return True
        return False

    # --- actions --------------------------------------------------------------------------

    def apply(self, actions: Sequence[Action]) -> None:
        """Apply the turn's actions. Leaving the order wins; the customer's decision goes last."""
        if any(isinstance(a, CancelOrder) for a in actions):
            self._cancel()
            return
        if any(isinstance(a, AskHuman) for a in actions):
            self._start_handoff()
            return
        owed = len(self.conversation.clarifications)
        for action in actions:
            self._apply_one(action)
        reopened = self._cart_changed or len(self.conversation.clarifications) > owed
        if reopened and self.conversation.lifecycle.can(Event.CHANGE_REQUESTED):
            self._event(Event.CHANGE_REQUESTED)
        if self._cart.lines and any(isinstance(a, _SLOT_ACTIONS) for a in actions):
            self._update(items_done=True)
        decisions = [a for a in actions if isinstance(a, Confirm | Deny)]
        if decisions and not self._cart_changed:
            self._decide(confirmed=isinstance(decisions[-1], Confirm))

    def _cancel(self) -> None:
        if self.conversation.follows_confirmed and not self._cart.lines:
            # The bot can't undo a confirmed order, and must not claim it did.
            self._note(Msg.ALREADY_CONFIRMED)
            self._start_handoff()
            return
        self._event(Event.CANCEL)
        self._final = self._responder.text(Msg.CANCELLED)

    def _start_handoff(self) -> None:
        self._event(Event.HANDOFF_REQUESTED)
        self._update(misunderstood=0)
        self._final = self._responder.text(Msg.HANDOFF)

    def _apply_one(self, action: Action) -> None:
        match action:
            case AddItem():
                self._add_item(action)
            case RemoveItem():
                self._remove_item(action)
            case UpdateItem():
                self._update_item(action)
            case SetFulfillment():
                self._set_fulfillment(Fulfillment(action.mode))
            case SetAddress():
                self._set_address(action)
            case SetPayment():
                self._set_payment(action)
            case AskQuestion():
                self._answer_question(action)
            case Greeting():
                self._understood = True

    def _add_item(self, action: AddItem) -> None:
        item = self._resolver.resolve_item(action.product, action.modifiers)
        best = item.product.best
        if best is not None:
            self._add_line(best.id, action.quantity, item.modifiers, action.note)
        elif item.product.status is MatchStatus.AMBIGUOUS:
            clarification = ProductClarification(
                candidates=tuple(c.id for c in item.product.candidates),
                quantity=action.quantity,
                modifiers=tuple(m.query for m in item.modifiers),
                note=action.note,
            )
            self._queue(clarification)
        else:
            self._note(Msg.PRODUCT_NOT_FOUND, text=action.product)

    def _queue(self, clarification: Clarification) -> None:
        self._update(clarifications=(*self.conversation.clarifications, clarification))
        self._understood = True

    def _add_line(
        self,
        product_id: str,
        quantity: int,
        modifiers: Iterable[Match],
        note: str,
        chosen: Sequence[ModifierChoice] = (),
    ) -> None:
        matches = tuple(modifiers)
        choices = [
            *chosen,
            *(
                ModifierChoice(group_id=m.best.group_id, option_id=m.best.id)
                for m in matches
                if m.best is not None and m.best.group_id is not None
            ),
        ]
        unclear = next((m for m in matches if m.status is MatchStatus.AMBIGUOUS), None)
        if unclear is not None:
            self._queue(
                ModifierClarification(
                    product_id=product_id,
                    candidates=tuple(
                        ModifierChoice(group_id=c.group_id, option_id=c.id)
                        for c in unclear.candidates
                        if c.group_id is not None
                    ),
                    quantity=quantity,
                    chosen=tuple(choices),
                    modifiers=tuple(
                        m.query for m in matches if m is not unclear and m.best is None
                    ),
                    note=note,
                )
            )
            return
        try:
            cart, line = carts.add_item(
                self._cart, self._config, product_id, quantity, choices, note
            )
        except CartError:
            name = self._responder.product_name(product_id)
            self._note(Msg.INVALID_CHANGE, product=name)
            return
        self._set_cart(cart)
        self._added.append(self._responder.line(line))
        name = self._responder.product_name(product_id)
        for match in matches:
            if match.status is MatchStatus.NOT_FOUND:
                self._note(Msg.MODIFIER_NOT_FOUND, text=match.query, product=name)

    def _add_modifier(self, line_id: int, product_id: str, choice: ModifierChoice) -> None:
        try:
            cart = carts.update_line(self._cart, self._config, line_id, add_modifiers=[choice])
        except CartError:
            self._note(Msg.INVALID_CHANGE, product=self._responder.product_name(product_id))
            return
        self._set_cart(cart)

    def _find_line(self, text: str) -> CartLine | None:
        """The one line `text` refers to, or None after telling the customer why not."""
        item = self._resolver.resolve_item(text)
        ids = {c.id for c in item.product.candidates}
        wanted = set(item.choices)
        lines = [
            line
            for line in self._cart.lines
            if line.product_id in ids and wanted <= set(line.modifiers)
        ]
        if len(lines) == 1:
            return lines[0]
        if lines:
            self._note(Msg.LINE_UNCLEAR, product=self._responder.product_name(lines[0].product_id))
        else:
            self._note(Msg.NOT_IN_CART, text=text)
        return None

    def _remove_item(self, action: RemoveItem) -> None:
        line = self._find_line(action.product)
        if line is None:
            return
        description = self._responder.line(line)
        if action.quantity is not None and action.quantity < line.qty:
            qty = line.qty - action.quantity
            self._set_cart(carts.update_line(self._cart, self._config, line.line_id, qty=qty))
        else:
            self._set_cart(carts.remove_line(self._cart, line.line_id))
        if not self._cart.lines:
            self._update(items_done=False)
        self._note(Msg.REMOVED, item=description)

    def _update_item(self, action: UpdateItem) -> None:
        line = self._find_line(action.product)
        if line is None:
            return
        add = self._choices(line, action.add_modifiers)
        remove = self._choices(line, action.remove_modifiers)
        try:
            cart = carts.update_line(
                self._cart,
                self._config,
                line.line_id,
                qty=action.quantity,
                add_modifiers=add,
                remove_modifiers=remove,
            )
        except CartError:
            self._note(Msg.INVALID_CHANGE, product=self._responder.product_name(line.product_id))
            return
        self._set_cart(cart)
        updated = cart.line(line.line_id)
        if updated is not None:
            self._note(Msg.UPDATED, item=self._responder.line(updated))

    def _choices(self, line: CartLine, texts: Iterable[str]) -> list[ModifierChoice]:
        choices = []
        for text in texts:
            best = self._resolver.resolve_modifier(line.product_id, text).best
            if best is None or best.group_id is None:
                name = self._responder.product_name(line.product_id)
                self._note(Msg.MODIFIER_NOT_FOUND, text=text, product=name)
            else:
                choices.append(ModifierChoice(group_id=best.group_id, option_id=best.id))
        return choices

    def _set_fulfillment(self, mode: Fulfillment) -> None:
        try:
            self._set_cart(carts.set_fulfillment(self._cart, self._config, mode))
        except CartError:
            self._note(Msg.FULFILLMENT_UNAVAILABLE)

    def _set_address(self, action: SetAddress) -> None:
        """An address means delivery, even if the customer had chosen pickup before."""
        # TODO(#41): after choosing pickup this is ambiguous, so ask before switching.
        try:
            cart = carts.set_fulfillment(self._cart, self._config, Fulfillment.DELIVERY)
        except CartError:
            self._note(Msg.FULFILLMENT_UNAVAILABLE)
            return
        self._set_cart(carts.set_address(cart, action.address, action.details))

    def _set_payment(self, action: SetPayment) -> None:
        best = self._resolver.resolve_payment(action.method).best
        if best is None:
            self._note(Msg.PAYMENT_UNKNOWN, text=action.method)
            return
        cart = carts.set_payment(self._cart, self._config, best.id, action.cash_change_for)
        self._set_cart(cart)

    def _answer_question(self, action: AskQuestion) -> None:
        self._understood = True
        match action.topic:
            case "menu":
                self._notes.append(self._responder.menu())
            case "hours":
                self._notes.append(self._responder.hours())
            case "price":
                self._answer_price(action.product)
            case _:
                self._note(Msg.QUESTION_UNKNOWN)

    def _answer_price(self, text: str) -> None:
        candidates = self._resolver.resolve_product(text).candidates
        if not candidates:
            self._note(Msg.PRODUCT_NOT_FOUND, text=text)
        for candidate in candidates:
            product = self._config.menu.product(candidate.id)
            if product is not None:
                self._note(Msg.PRICE, product=product.name, price=format_cop(product.price))

    def _decide(self, *, confirmed: bool) -> None:
        state = self.conversation.lifecycle.state
        if state is LifecycleState.AWAITING_CONFIRMATION:
            self._understood = True
            if not confirmed:
                self._event(Event.CHANGE_REQUESTED)
                self._final = self._responder.text(Msg.ASK_CHANGE)
            elif self._config.modes.shadow:
                self._place_order()
                self._event(Event.SENT_TO_STAFF)
                self._final = self._responder.text(Msg.SENT_TO_STAFF)
            else:
                self._place_order()
                self._event(Event.CUSTOMER_CONFIRMED)
                eta = self._responder.eta(self._cart)
                self._final = self._responder.text(Msg.CONFIRMED, eta=eta)
        elif self.conversation.asked is Step.ANYTHING_ELSE:
            self._understood = True
            if confirmed:
                self._asking_for_more = True  # "sí" to "anything else?" means there is more
            else:
                self._update(items_done=True)

    def _place_order(self) -> None:
        self.order = create_order(
            self._cart,
            self._config,
            source=OrderSource.CHAT,
            idempotency_key=self.conversation.id,
            conversation_id=self.conversation.id,
        )

    # --- reply ----------------------------------------------------------------------------

    def reply(self) -> OutboundMessage:
        """Close the turn: settle the counters, then ask whatever comes next."""
        if self._added:
            added = self._responder.text(Msg.ADDED, item=join_options(self._added, "y"))
            self._notes.insert(0, added)
        waiting = self.conversation.lifecycle.state is LifecycleState.PENDING_STAFF_CONFIRMATION
        if self._understood or self._final is not None or waiting:
            self._update(misunderstood=0)
        else:
            self._update(misunderstood=self.conversation.misunderstood + 1)
            if self.conversation.misunderstood >= MAX_MISUNDERSTOOD:
                self._start_handoff()
            elif not self._notes:
                # TODO(#42): offer a person here, before the customer gets frustrated.
                self._note(Msg.NOT_UNDERSTOOD)
        if self._final is not None:
            self._update(asked=None, asked_choice=None, options=())
            return OutboundMessage(text="\n\n".join([*self._notes, self._final]))
        if waiting:
            return OutboundMessage(
                text="\n\n".join([*self._notes, self._responder.text(Msg.STILL_PENDING)])
            )
        question, replies = self._next_question()
        self._update(greeted=True, options=tuple(reply.payload for reply in replies))
        return OutboundMessage(text="\n\n".join([*self._notes, question]), quick_replies=replies)

    def _next_question(self) -> tuple[str, tuple[QuickReply, ...]]:
        if self.conversation.clarifications:
            self._update(asked=None, asked_choice=None)
            return self._clarifying_question(self.conversation.clarifications[0])
        if self._asking_for_more:
            return self._responder.text(Msg.ASK_MORE), ()
        step = next_step(
            self._cart,
            self._config,
            items_done=self.conversation.items_done,
            upsell_offered=self.conversation.upsell_offered,
            change_asked=self.conversation.change_asked,
        )
        self._update(asked=step.step, asked_choice=step.choice)
        return self._question(step)

    def _clarifying_question(self, head: Clarification) -> tuple[str, tuple[QuickReply, ...]]:
        responder = self._responder
        if isinstance(head, ProductClarification):
            names = [responder.product_name(c) for c in head.candidates]
            if len(names) == 1:
                return responder.text(Msg.PRODUCT_YES_NO, product=names[0]), responder.yes_no()
            text = responder.text(Msg.PRODUCT_CHOICE, options=join_options(names))
            return text, responder.replies(names)
        product = responder.product_name(head.product_id)
        labels = [responder.option_label(c) for c in head.candidates]
        if len(labels) == 1:
            text = responder.text(Msg.MODIFIER_YES_NO, product=product, option=labels[0])
            return text, responder.yes_no()
        text = responder.text(Msg.MODIFIER_CHOICE, product=product, options=join_options(labels))
        return text, responder.replies(labels)

    def _question(self, next_: NextStep) -> tuple[str, tuple[QuickReply, ...]]:
        responder = self._responder
        match next_.step:
            case Step.ITEMS:
                if self.conversation.greeted:
                    return responder.text(Msg.ASK_ITEMS), ()
                return responder.greeting, ()
            case Step.REQUIRED_CHOICE:
                return self._required_choice_question(next_)
            case Step.ANYTHING_ELSE:
                return self._anything_else(next_), ()
            case Step.FULFILLMENT:
                options = [responder.text(Msg.OPTION_DELIVERY), responder.text(Msg.OPTION_PICKUP)]
                return responder.text(Msg.ASK_FULFILLMENT), responder.replies(options)
            case Step.ADDRESS:
                return responder.text(Msg.ASK_ADDRESS), ()
            case Step.PAYMENT:
                labels = [m.label for m in self._config.payment_methods]
                return responder.text(Msg.ASK_PAYMENT), responder.replies(labels)
            case Step.CASH_CHANGE:
                self._update(change_asked=True)
                return responder.text(Msg.ASK_CASH_CHANGE), ()
            case Step.CONFIRM:
                if self.conversation.lifecycle.can(Event.ORDER_COMPLETE):
                    self._event(Event.ORDER_COMPLETE)
                summary = responder.summary(self._cart)
                question = responder.text(Msg.CONFIRM_QUESTION)
                return f"{summary}\n\n{question}", responder.yes_no()

    def _required_choice_question(self, next_: NextStep) -> tuple[str, tuple[QuickReply, ...]]:
        choice = next_.choice
        assert choice is not None
        group = self._config.menu.group(choice.group_id)
        assert group is not None
        text = self._responder.text(
            Msg.ASK_CHOICE,
            group=group.label.lower(),
            product=self._responder.product_name(choice.product_id),
        )
        return text, self._responder.replies(option.label for option in group.options)

    def _anything_else(self, next_: NextStep) -> str:
        text = self._responder.text(Msg.ANYTHING_ELSE)
        if not next_.upsell:
            return text
        self._update(upsell_offered=True)
        categories = {c.id: c.name.lower() for c in self._config.menu.categories}
        names = [categories[c] for c in next_.upsell]
        return self._responder.text(Msg.UPSELL, options=join_options(names))
