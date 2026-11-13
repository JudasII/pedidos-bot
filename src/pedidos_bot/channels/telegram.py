"""Telegram for customers, over the Bot API with long polling.

Delivery guarantees, and their limits:

- **Inbound is at-least-once.** An update is confirmed to Telegram only after its turn has
  committed, so a crash mid-turn means Telegram delivers it again. The update id is the
  message's `external_id`, and the repository drops the repeat. Telegram keeps the position
  of the last confirmed update, so nothing about it is stored here.
- **Outbound is at-least-once too, and Telegram can't dedupe.** `sendMessage` has no
  idempotency key: a crash after it succeeds and before the outbox marks the message sent
  means the customer gets it twice. We accept that over losing a reply.
- **Rate limits.** A 429 becomes a `ChannelError` with `retry_after`, which the outbox waits
  out instead of using its own backoff.

Messages of one chat are handled in order; different chats run at the same time, so a slow
turn only holds up its own chat. Private chats are customers. The one group that is read is
the restaurant's staff chat; what comes from it carries that chat as its `chat_ref`, and
whoever receives the messages tells the two apart by it.

Customers get reply keyboards, whose buttons send their title as plain text: no callbacks,
so no stale taps and no payload size limit. Buttons whose payload differs from their title
(the staff's) become inline buttons. A tap on one arrives as an interactive reply and is
acknowledged to Telegram at once, before its turn runs; the turn must cope with stale taps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from pedidos_bot.channels.base import (
    ChannelCapabilities,
    ChannelError,
    GeoPoint,
    InboundHandler,
    InboundMessage,
    MessageKind,
    OutboundMessage,
    SendResult,
    degrade,
)

CHANNEL = "telegram"
API_URL = "https://api.telegram.org"
POLL_SECONDS = 30
"""How long Telegram holds a `getUpdates` call open when there is nothing new."""
MAX_TURN_ATTEMPTS = 3
"""Tries for one update before it is dropped, so a message that always fails can't block."""
_MAX_ERROR_WAIT = 30.0
_BAD_TOKEN_STATUS = (401, 404)

_logger = structlog.get_logger()
# httpx logs each request's URL at INFO, and a Bot API URL contains the token.
logging.getLogger("httpx").setLevel(logging.WARNING)


class BadTokenError(ChannelError):
    """Telegram doesn't know the bot token. Retrying can't help."""


class TelegramChannel:
    capabilities = ChannelCapabilities(max_buttons=8, max_button_title=40)

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        *,
        staff_chat: str | None = None,
        retry_wait: float = 1.0,
    ) -> None:
        self._base = f"{API_URL}/bot{token}"
        self._client = client
        self._staff_chat = staff_chat
        self._acknowledging: set[asyncio.Task[None]] = set()
        self._retry_wait = retry_wait
        """Seconds before retrying a failed poll or turn; it grows with each failure."""
        self._stopping = asyncio.Event()
        self._progress = asyncio.Event()
        self._queues: dict[str, asyncio.Queue[tuple[int, InboundMessage]]] = {}
        self._workers: set[asyncio.Task[None]] = set()
        self._unfinished: set[int] = set()
        self._last_seen: int | None = None

    # --- inbound --------------------------------------------------------------------------

    async def start(self, on_inbound: InboundHandler) -> None:
        """Poll until `stop` is called, then let the turns in progress finish."""
        failures = 0
        while not self._stopping.is_set():
            try:
                updates = await self._stoppable(self._get_updates())
            except BadTokenError:
                raise
            except ChannelError as error:
                failures += 1
                _logger.warning("telegram_poll_failed", error=str(error), failures=failures)
                asked = error.retry_after.total_seconds() if error.retry_after else 0.0
                backoff = min(self._retry_wait * failures, _MAX_ERROR_WAIT)
                await self._stoppable(asyncio.sleep(max(asked, backoff)))
                continue
            failures = 0
            fresh = [u for u in updates or [] if self._is_new(u["update_id"])]
            for update in fresh:
                self._accept(update, on_inbound)
            if not fresh and self._unfinished:
                # Telegram answers at once while updates are unconfirmed: don't spin on it.
                self._progress.clear()
                await self._stoppable(self._progress.wait(), patience=self._retry_wait)
        if self._workers or self._acknowledging:
            await asyncio.gather(*self._workers, *self._acknowledging)

    def stop(self) -> None:
        self._stopping.set()

    async def _stoppable[T](self, work: Awaitable[T], patience: float | None = None) -> T | None:
        """The result of `work`, or None if the channel stops or `patience` seconds pass first."""
        task = asyncio.ensure_future(work)
        stop = asyncio.ensure_future(self._stopping.wait())
        try:
            await asyncio.wait({task, stop}, timeout=patience, return_when=asyncio.FIRST_COMPLETED)
            return task.result() if task.done() else None
        finally:
            for pending in (task, stop):
                if not pending.done():
                    pending.cancel()

    async def _get_updates(self) -> list[dict[str, Any]]:
        kinds = ["message", "callback_query"]
        params: dict[str, Any] = {"timeout": POLL_SECONDS, "allowed_updates": kinds}
        offset = self._confirmed_offset()
        if offset is not None:
            params["offset"] = offset
        result = await self._call("getUpdates", params, patience=POLL_SECONDS + 10)
        return list(result)

    def _confirmed_offset(self) -> int | None:
        """The first update Telegram must keep: everything before it has committed."""
        if self._unfinished:
            return min(self._unfinished)
        return None if self._last_seen is None else self._last_seen + 1

    def _is_new(self, update_id: int) -> bool:
        return self._last_seen is None or update_id > self._last_seen

    def _accept(self, update: dict[str, Any], on_inbound: InboundHandler) -> None:
        update_id = update["update_id"]
        self._last_seen = update_id
        if "callback_query" in update:
            self._acknowledge(update["callback_query"])
        try:
            inbound = to_inbound(update, self._staff_chat)
        except (KeyError, TypeError, ValueError) as error:
            # Skipped and confirmed: an update we can't read must not stop the polling.
            _logger.error(
                "telegram_update_unreadable", update_id=update_id, error=type(error).__name__
            )
            return
        if inbound is None:
            return
        self._unfinished.add(update_id)
        queue = self._queues.get(inbound.chat_ref)
        if queue is None:
            queue = self._queues[inbound.chat_ref] = asyncio.Queue()
            worker = asyncio.create_task(self._drain(inbound.chat_ref, queue, on_inbound))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
        queue.put_nowait((update_id, inbound))

    def _acknowledge(self, callback: dict[str, Any]) -> None:
        """Stop the button's spinner now; the answer to the tap comes as a message."""

        async def answer() -> None:
            try:
                await self._call("answerCallbackQuery", {"callback_query_id": callback.get("id")})
            except ChannelError as error:
                _logger.warning("telegram_acknowledge_failed", error=str(error))

        task = asyncio.create_task(answer())
        self._acknowledging.add(task)
        task.add_done_callback(self._acknowledging.discard)

    async def _drain(
        self,
        chat_ref: str,
        queue: asyncio.Queue[tuple[int, InboundMessage]],
        on_inbound: InboundHandler,
    ) -> None:
        """Run one chat's messages in order. Ends when the chat has nothing waiting."""
        try:
            while not queue.empty():
                update_id, inbound = queue.get_nowait()
                await self._run_turn(update_id, inbound, on_inbound)
                self._unfinished.discard(update_id)
                self._progress.set()
        finally:
            del self._queues[chat_ref]

    async def _run_turn(
        self, update_id: int, inbound: InboundMessage, on_inbound: InboundHandler
    ) -> None:
        for attempt in range(1, MAX_TURN_ATTEMPTS + 1):
            try:
                await on_inbound(inbound)
                return
            except Exception as error:
                # The type only: the error's text can quote the customer or a URL.
                _logger.error(
                    "telegram_turn_failed",
                    update_id=update_id,
                    attempt=attempt,
                    error=type(error).__name__,
                )
                if attempt < MAX_TURN_ATTEMPTS:
                    await asyncio.sleep(self._retry_wait * attempt)
        _logger.error("telegram_update_dropped", update_id=update_id)

    # --- outbound -------------------------------------------------------------------------

    async def send(self, chat_ref: str, message: OutboundMessage, *, dedupe_key: str) -> SendResult:
        """Send a message. `dedupe_key` can't be honored: Telegram has no idempotent send."""
        message = degrade(message, self.capabilities)
        markup: dict[str, Any]
        if any(reply.payload != reply.title for reply in message.quick_replies):
            row = [{"text": r.title, "callback_data": r.payload} for r in message.quick_replies]
            markup = {"inline_keyboard": [row]}
        elif message.quick_replies:
            markup = {
                "keyboard": [[{"text": reply.title}] for reply in message.quick_replies],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            }
        else:
            markup = {"remove_keyboard": True}
        params = {"chat_id": chat_ref, "text": message.text, "reply_markup": markup}
        result = await self._call("sendMessage", params)
        return SendResult(external_id=str(result["message_id"]))

    def can_send_freeform(self, last_inbound_at: datetime, now: datetime) -> bool:
        return True

    # --- HTTP -----------------------------------------------------------------------------

    async def _call(self, method: str, params: dict[str, Any], patience: float = 10.0) -> Any:
        """Call a Bot API method. Errors never carry the URL, which contains the token."""
        try:
            response = await self._client.post(
                f"{self._base}/{method}", json=params, timeout=patience
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ChannelError(f"{method}: {type(error).__name__}") from None
        if body.get("ok"):
            return body["result"]
        wait = (body.get("parameters") or {}).get("retry_after")
        retry_after = timedelta(seconds=wait) if wait is not None else None
        refusal = BadTokenError if response.status_code in _BAD_TOKEN_STATUS else ChannelError
        raise refusal(f"{method}: http {response.status_code}", retry_after=retry_after)


def to_inbound(update: dict[str, Any], staff_chat: str | None = None) -> InboundMessage | None:
    """Map an update to a message from a customer or from the staff chat.

    None if it is neither: another group, an edit, a button tapped outside the staff chat.
    """
    if "callback_query" in update:
        return _tap_to_inbound(update, staff_chat)
    message = update.get("message")
    if not message:
        return None
    from_staff = staff_chat is not None and str(message["chat"]["id"]) == staff_chat
    if message["chat"].get("type") != "private" and not from_staff:
        return None
    fields: dict[str, Any]
    if "text" in message:
        fields = {"kind": MessageKind.TEXT, "text": message["text"]}
    elif "voice" in message or "audio" in message:
        media = message.get("voice") or message["audio"]
        fields = {"kind": MessageKind.VOICE, "media_ref": media["file_id"]}
    elif "location" in message:  # a venue carries a location too
        point = message["location"]
        location = GeoPoint(latitude=point["latitude"], longitude=point["longitude"])
        fields = {"kind": MessageKind.LOCATION, "location": location}
    elif "photo" in message:
        largest = message["photo"][-1]  # sizes come smallest first
        fields = {"kind": MessageKind.IMAGE, "media_ref": largest["file_id"]}
    elif "document" in message:
        fields = {"kind": MessageKind.DOCUMENT, "media_ref": message["document"]["file_id"]}
    elif "sticker" in message:
        fields = {"kind": MessageKind.STICKER, "media_ref": message["sticker"]["file_id"]}
    else:
        fields = {"kind": MessageKind.UNSUPPORTED}
    if "caption" in message:
        fields["text"] = message["caption"]
    chat_id = str(message["chat"]["id"])
    sender = message.get("from") or {}
    return InboundMessage(
        channel=CHANNEL,
        external_id=str(update["update_id"]),
        chat_ref=chat_id,
        customer_ref=str(sender.get("id", chat_id)),
        sent_at=datetime.fromtimestamp(message["date"], UTC),
        raw=update,
        **fields,
    )


def _tap_to_inbound(update: dict[str, Any], staff_chat: str | None) -> InboundMessage | None:
    callback = update["callback_query"]
    chat_id = str(callback["message"]["chat"]["id"]) if callback.get("message") else None
    if staff_chat is None or chat_id != staff_chat or "data" not in callback:
        return None
    return InboundMessage(
        channel=CHANNEL,
        external_id=str(update["update_id"]),
        chat_ref=chat_id,
        customer_ref=str(callback["from"]["id"]),
        sent_at=datetime.now(UTC),  # a tap carries no time of its own
        kind=MessageKind.INTERACTIVE_REPLY,
        reply_payload=callback["data"],
        raw=update,
    )
