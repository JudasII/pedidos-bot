"""Reads the text file WhatsApp writes with "Export chat".

The format isn't documented and changes with the phone and its language. Two shapes are
read, each with a 12 or 24 hour clock:

    12/11/26, 8:45 p. m. - Name: text          (Android)
    [12/11/26, 8:45:10 p. m.] Name: text       (iPhone)

A line that doesn't start like that continues the message before it. Lines with a date but
no "Name:" are WhatsApp's own notices and are counted, not kept. Nothing here guesses: what
can't be read is reported so a new shape is noticed instead of silently lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pedidos_bot.models import FrozenModel

Kind = Literal[
    "text", "audio", "image", "video", "sticker", "document", "location", "contact", "media",
    "deleted",
]  # fmt: skip

_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\ufeff"), None) | {0x202F: " ", 0xA0: " "}
_STAMP = (
    r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4}),? (\d{1,2}):(\d{2})(?::\d{2})?"
    r"(?: ?([ap])\.? ?m\.?)?"
)
_HEADERS = (
    re.compile(rf"^{_STAMP} - (.*)$", re.IGNORECASE),
    re.compile(rf"^\[{_STAMP}\] (.*)$", re.IGNORECASE),
)
_ATTACHMENT = re.compile(
    r"^<(?:adjunto|attached): (?P<a>.+)>$"
    r"|^(?P<b>\S+\.\w{2,5}) \((?:archivo adjunto|file attached)\)$",
    re.IGNORECASE,
)
_OMITTED = re.compile(
    r"^<?(?P<what>multimedia|media|audio|imagen|image|video|sticker|gif|documento|document"
    r"|tarjeta de contacto|contact card) (?:omitid[oa]|omitted)>?$",
    re.IGNORECASE,
)
_OMITTED_KINDS: dict[str, Kind] = {
    "multimedia": "media", "media": "media", "audio": "audio", "imagen": "image",
    "image": "image", "video": "video", "gif": "video", "sticker": "sticker",
    "documento": "document", "document": "document", "tarjeta de contacto": "contact",
    "contact card": "contact",
}  # fmt: skip
_EXTENSIONS: dict[str, Kind] = {
    "opus": "audio", "ogg": "audio", "m4a": "audio", "mp3": "audio", "aac": "audio",
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "sticker", "mp4": "video",
    "mov": "video", "3gp": "video", "vcf": "contact",
}  # fmt: skip
_LOCATION = re.compile(
    r"^(?:(?:ubicación|location):.*|https?://(?:maps\.google\.com|maps\.app\.goo\.gl)\S*)$",
    re.IGNORECASE,
)
"""A shared location. A link inside a sentence is still text."""
_DELETED = re.compile(
    r"^(?:se eliminó este mensaje|eliminaste este mensaje|this message was deleted"
    r"|you deleted this message)\.?$",
    re.IGNORECASE,
)


class ChatMessage(FrozenModel):
    at: datetime
    """Local time of the phone that exported the chat; the export carries no time zone."""
    sender: str
    kind: Kind = "text"
    text: str = ""
    """Empty for anything that isn't text: file names and links are dropped."""


@dataclass
class ParsedChat:
    messages: list[ChatMessage] = field(default_factory=list)
    notices: int = 0
    """Lines written by WhatsApp itself (encryption notice, "X changed the subject")."""
    stray_lines: int = 0
    """Lines before the first message that match nothing: a sign of an unknown format."""

    @property
    def senders(self) -> set[str]:
        return {message.sender for message in self.messages}


def parse(export: str, *, month_first: bool = False) -> ParsedChat:
    chat = ParsedChat()
    open_message = False  # whether a following plain line continues the last message
    for raw in export.splitlines():
        line = raw.translate(_INVISIBLE).strip()
        header = next((m for m in (h.match(line) for h in _HEADERS) if m), None)
        if header is None:
            if open_message:
                last = chat.messages[-1]
                if last.kind == "text":
                    joined = f"{last.text}\n{line}"
                    chat.messages[-1] = last.model_copy(update={"text": joined})
            elif line and not chat.messages and not chat.notices:
                chat.stray_lines += 1
            continue
        sender, separator, body = header.group(7).partition(": ")
        if not separator:
            chat.notices += 1
            open_message = False
            continue
        kind = _kind(body)
        text = body if kind == "text" else ""
        chat.messages.append(
            ChatMessage(at=_when(header, month_first), sender=sender, kind=kind, text=text)
        )
        open_message = True
    return chat


def _when(header: re.Match[str], month_first: bool) -> datetime:
    first, second, year, hour, minute, half = header.group(1, 2, 3, 4, 5, 6)
    day, month = (second, first) if month_first else (first, second)
    hours = int(hour)
    if half:
        hours = hours % 12 + (12 if half.lower() == "p" else 0)
    full_year = int(year) + (2000 if len(year) == 2 else 0)
    return datetime(full_year, int(month), int(day), hours, int(minute))


def _kind(body: str) -> Kind:
    attachment = _ATTACHMENT.match(body)
    if attachment:
        name = attachment.group("a") or attachment.group("b")
        return _EXTENSIONS.get(name.rsplit(".", 1)[-1].lower(), "document")
    omitted = _OMITTED.match(body)
    if omitted:
        return _OMITTED_KINDS[omitted.group("what").lower()]
    if _LOCATION.match(body):
        return "location"
    if _DELETED.match(body):
        return "deleted"
    return "text"
