"""From chat exports to masked transcripts, all inside one private folder.

    <out>/raw/      the exports as they arrived
    <out>/masked/   one JSON file per conversation, without names, phones or addresses

Masking is best effort. It removes what has a shape (phones, street addresses, emails,
links) and the names of the people in the chat. It can't know a name someone mentions, or
an address given as "next to the bakery". So masked transcripts stay private too: nothing
derived from them goes into the repository without a person rewriting it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pedidos_bot.chats.whatsapp import Kind, ParsedChat, parse
from pedidos_bot.models import FrozenModel
from pedidos_bot.observability.masking import hash_customer, mask_text

RESTAURANT = "restaurant"
CUSTOMER = "customer"
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_LINK = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_MIN_NAME_PART = 3
"""Shorter parts of a name ("de", "la") are ordinary words."""


class ChatImportError(Exception):
    """The exports can't be imported as asked. The message is human-readable."""


class Line(FrozenModel):
    at: datetime
    role: str
    """`restaurant`, `customer`, or `customer_2`... when several people write in a group."""
    kind: Kind = "text"
    text: str = ""


class Transcript(FrozenModel):
    source: Literal["whatsapp"] = "whatsapp"
    chat_id: str
    """A hash of who the customers are: the same chat imported twice gets the same id."""
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Imported:
    export: Path
    chat_id: str | None
    """None if nothing could be read from the export."""
    messages: int = 0
    notices: int = 0
    stray_lines: int = 0
    kinds: tuple[tuple[str, int], ...] = ()


def mask(chat: ParsedChat, restaurant: Iterable[str], salt: str, export: str = "") -> Transcript:
    """Replace who wrote with a role, and what identifies a customer with a placeholder.

    The restaurant's own name stays in the text: it is often a word of the menu.
    `export` names the chat when no customer wrote in it.
    """
    staff = set(restaurant)
    customers = sorted(chat.senders - staff)
    roles = {name: RESTAURANT for name in staff}
    for position, name in enumerate(customers, 1):
        roles[name] = CUSTOMER if position == 1 else f"{CUSTOMER}_{position}"
    parts = {part for name in customers for part in re.findall(r"[^\W\d_]+", name)}
    long_parts = sorted((p for p in parts if len(p) >= _MIN_NAME_PART), key=len, reverse=True)
    names = re.compile(rf"\b(?:{'|'.join(map(re.escape, long_parts))})\b", re.IGNORECASE)

    def clean(text: str) -> str:
        text = _EMAIL.sub("[email]", _LINK.sub("[link]", text))
        text = mask_text(text)
        return names.sub("[name]", text) if long_parts else text

    lines = tuple(
        Line(at=m.at, role=roles[m.sender], kind=m.kind, text=clean(m.text)) for m in chat.messages
    )
    identity = "|".join(customers) or export
    return Transcript(chat_id=hash_customer(identity, salt), lines=lines)


def find_exports(paths: Iterable[Path]) -> list[Path]:
    """The `.txt` and `.zip` files in `paths`, looking inside folders."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.suffix.lower() in (".txt", ".zip"))
        elif path.is_file():
            found.append(path)
        else:
            raise ChatImportError(f"{path}: not found")
    return found


def read_export(path: Path) -> str:
    """The chat text of an export. A `.zip` (chat with media) is read without unpacking it."""
    if path.suffix.lower() != ".zip":
        return path.read_bytes().decode("utf-8", errors="replace")
    try:
        with zipfile.ZipFile(path) as archive:
            texts = [name for name in archive.namelist() if name.lower().endswith(".txt")]
            if not texts:
                raise ChatImportError(f"{path}: no chat text inside")
            return archive.read(texts[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as error:
        raise ChatImportError(f"{path}: not a zip file") from error


def import_exports(
    paths: Iterable[Path],
    out: Path,
    *,
    salt: str,
    restaurant: Iterable[str] = (),
    month_first: bool = False,
) -> list[Imported]:
    """Store each export under `out/raw` and write its masked transcript to `out/masked`."""
    exports = find_exports(paths)
    chats = [(path, parse(read_export(path), month_first=month_first)) for path in exports]
    staff = set(restaurant) or _in_every_chat([chat for _, chat in chats if chat.messages])
    if not staff:
        raise ChatImportError(
            "can't tell who the restaurant is: pass its name as it appears in the chats "
            "(--restaurant), or import several chats at once"
        )
    raw, masked = out / "raw", out / "masked"
    raw.mkdir(parents=True, exist_ok=True)
    masked.mkdir(parents=True, exist_ok=True)
    results = []
    for path, chat in chats:
        _store(path, raw)
        if not chat.messages:
            results.append(Imported(path, None, stray_lines=chat.stray_lines))
            continue
        transcript = mask(chat, staff, salt, export=path.name)
        target = masked / f"{transcript.chat_id}.json"
        target.write_text(transcript.model_dump_json(indent=2) + "\n", encoding="utf-8")
        kinds = Counter(line.kind for line in transcript.lines)
        results.append(
            Imported(
                path,
                transcript.chat_id,
                messages=len(transcript.lines),
                notices=chat.notices,
                stray_lines=chat.stray_lines,
                kinds=tuple(sorted(kinds.items())),
            )
        )
    return results


def _in_every_chat(chats: list[ParsedChat]) -> set[str]:
    """Whoever writes in all the chats is the restaurant. One chat alone can't tell."""
    if len(chats) < 2:
        return set()
    return set.intersection(*(chat.senders for chat in chats))


def _store(export: Path, raw: Path) -> None:
    """Keep the export as it arrived. The content hash keeps equal names apart."""
    if raw in export.resolve().parents:
        return
    digest = hashlib.sha256(export.read_bytes()).hexdigest()[:8]
    shutil.copy2(export, raw / f"{digest}-{export.name}")
