"""A fake Telegram Bot API for tests, served through httpx's mock transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

TOKEN = "123:not-a-real-token"
SENT_AT = 1_794_000_000


def make_update(
    update_id: int, chat_id: int = 7, chat_type: str = "private", **content: Any
) -> Any:
    message = {
        "message_id": update_id * 10,
        "date": SENT_AT,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": chat_id, "first_name": "Cliente"},
        **(content or {"text": "hola"}),
    }
    return {"update_id": update_id, "message": message}


def make_tap(update_id: int, data: str, chat_id: int = -100, user_id: int = 55) -> Any:
    """A tap on an inline button of a message in `chat_id`."""
    callback = {
        "id": f"cb{update_id}",
        "from": {"id": user_id, "first_name": "Cajero"},
        "message": {"message_id": 1, "date": SENT_AT, "chat": {"id": chat_id, "type": "group"}},
        "data": data,
    }
    return {"update_id": update_id, "callback_query": callback}


class FakeTelegram:
    """Keeps updates until they are confirmed with a higher offset, like the real one."""

    def __init__(self, *updates: Any) -> None:
        self.updates = list(updates)
        self.offsets: list[int | None] = []
        self.sent: list[dict[str, Any]] = []
        self.acknowledged: list[str] = []
        self.failures: list[httpx.Response | Exception] = []
        """Answers to give instead of the real one, oldest first."""

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            offset = body.get("offset")
            self.offsets.append(offset)
            if offset is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            if not self.updates:
                await asyncio.sleep(0.005)  # stands in for the long poll
            return ok(list(self.updates))
        if request.url.path.endswith("/answerCallbackQuery"):
            self.acknowledged.append(body["callback_query_id"])
            return ok(True)
        assert request.url.path.endswith("/sendMessage")
        self.sent.append(body)
        return ok({"message_id": 900 + len(self.sent)})


def ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def refusal(status: int, **parameters: Any) -> httpx.Response:
    body = {"ok": False, "error_code": status, "description": "no", "parameters": parameters}
    return httpx.Response(status, json=body)


async def until(condition: Callable[[], object]) -> None:
    """Wait for something the code under test does on its own schedule."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.005)
