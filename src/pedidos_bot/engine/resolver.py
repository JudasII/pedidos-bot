"""Deterministic resolver: customer words -> menu, modifier and payment-method IDs.

The LLM refers to products by the customer's own words ("una pizza mediana", "piza
hawayana"). This module maps those words to IDs from the restaurant config using aliases
and fuzzy matching. It never guesses: low confidence or a near tie returns AMBIGUOUS with
candidates so the bot can ask, and anything unknown returns NOT_FOUND.

Matching runs in three passes, stopping at the first that yields a result:
1. exact match against a normalized alias, name or label;
2. containment: the longest alias appearing as whole words inside the query;
3. fuzzy scoring (rapidfuzz) with a resolve threshold and a minimum gap to the runner-up.

Those passes judge one alias at a time. `MenuResolver.resolve_item` then checks the whole
phrase: if leftover words name another product, or a modifier alias uses the words taken as
the product, the phrase is only RESOLVED when a single reading explains every word.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache

from rapidfuzz import fuzz

from pedidos_bot.config.schema import RestaurantConfig
from pedidos_bot.domain.cart import ModifierChoice

RESOLVE_SCORE = 85.0
"""Minimum fuzzy score to accept a match on its own."""
MIN_GAP = 8.0
"""The best fuzzy score must beat the runner-up by this much to be accepted."""
CANDIDATE_SCORE = 70.0
"""Minimum fuzzy score to be offered as a clarification candidate."""
MAX_CANDIDATES = 3

# Leading words that carry quantity or articles, not identity ("una", "2", "otra").
_LEADING_FILLERS = frozenset(
    [
        "un",
        "una",
        "uno",
        "unos",
        "unas",
        "el",
        "la",
        "los",
        "las",
        "otra",
        "otro",
        "otras",
        "otros",
        "dos",
        "tres",
        "cuatro",
        "cinco",
        "seis",
        "quiero",
        "dame",
        "regalame",
        "porfa",
    ]
)
# Where leftover modifier text splits ("sin champiñones y con piña"). "sin" stays with its
# piece, so "con champiñones sin aceitunas" yields both modifiers.
_SPLIT_CONNECTORS = re.compile(r"\b(?:y|con)\b|(?=\bsin\b)")
_LEADING_PIECE_FILLERS = frozenset({"de", "del", "la", "el", "en", "con"})
_PAYMENT_FILLERS = frozenset({"en", "con", "por", "pago", "pagar"})


class MatchStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class MatchMethod(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"
    FUZZY = "fuzzy"
    NONE = "none"


@dataclass(frozen=True)
class Candidate:
    id: str
    score: float
    group_id: str | None = None
    """For modifier options: the group the option belongs to."""

    @property
    def choice(self) -> ModifierChoice | None:
        if self.group_id is None:
            return None
        return ModifierChoice(group_id=self.group_id, option_id=self.id)


@dataclass(frozen=True)
class Match:
    status: MatchStatus
    query: str
    method: MatchMethod
    candidates: tuple[Candidate, ...] = ()
    leftover: str = ""
    """Words of the query not explained by the matched alias (containment only)."""

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.status is MatchStatus.RESOLVED else None

    @property
    def choice(self) -> ModifierChoice | None:
        """The modifier this match settled on, if any."""
        return self.best.choice if self.best else None


@dataclass(frozen=True)
class ItemResolution:
    """A product and its modifiers.

    When `product` is AMBIGUOUS, `modifiers` are the ones the customer gave, resolved against
    the first candidate. Resolve each `query` again once the product is chosen.
    """

    product: Match
    modifiers: tuple[Match, ...] = ()

    @property
    def choices(self) -> tuple[ModifierChoice, ...]:
        return tuple(dict.fromkeys(m.choice for m in self.modifiers if m.choice))

    @property
    def unresolved_modifiers(self) -> tuple[Match, ...]:
        return tuple(m for m in self.modifiers if m.status is not MatchStatus.RESOLVED)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces, singularize tokens."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    tokens = re.sub(r"[^a-z0-9]+", " ", ascii_text).split()
    return " ".join(_singular(t) for t in tokens)


def _singular(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


@cache
def _normalized(words: frozenset[str]) -> frozenset[str]:
    return frozenset(normalize(word) for word in words)


def _strip_leading(text: str, fillers: frozenset[str], *, numbers: bool = False) -> str:
    """Drop leading filler words, and numbers if asked. `text` must already be normalized."""
    normalized = _normalized(fillers)
    tokens = text.split()
    while tokens and (tokens[0] in normalized or (numbers and tokens[0].isdigit())):
        tokens.pop(0)
    return " ".join(tokens)


def _product_query(text: str) -> str:
    """The part of `text` that names a product: a leading number there is a quantity."""
    return _strip_leading(normalize(text), _LEADING_FILLERS, numbers=True)


@dataclass(frozen=True)
class _Entry:
    id: str
    surfaces: tuple[str, ...]
    group_id: str | None = None


@dataclass
class _Index:
    entries: Sequence[_Entry]
    shared: dict[str, list[_Entry]] = field(default_factory=dict)
    """Surfaces that name a group of entries (e.g. a category); they never resolve alone."""
    _exact: dict[str, list[_Entry]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for entry in self.entries:
            for surface in entry.surfaces:
                self._add(surface, entry)
        for surface, entries in self.shared.items():
            for entry in entries:
                self._add(surface, entry)

    def _add(self, surface: str, entry: _Entry) -> None:
        bucket = self._exact.setdefault(surface, [])
        if entry not in bucket:
            bucket.append(entry)

    def match(self, raw_query: str, query: str) -> Match:
        """Match `query`, the cleaned-up form of `raw_query`, against the entries."""
        if not query:
            return Match(MatchStatus.NOT_FOUND, raw_query, MatchMethod.NONE)
        exact = self._match_exact(raw_query, query)
        if exact is not None:
            return exact
        contained = self._match_contained(raw_query, query)
        if contained is not None and contained.status is MatchStatus.RESOLVED:
            return contained
        shared = self._match_shared_word(raw_query, query)
        if contained is None and shared is not None:
            return shared
        fuzzy = self._match_fuzzy(raw_query, query)
        if fuzzy.status is MatchStatus.RESOLVED or contained is None:
            return fuzzy
        return contained

    def _match_exact(self, raw: str, query: str) -> Match | None:
        entries = self._exact.get(query)
        if not entries:
            return None
        status = MatchStatus.RESOLVED if len(entries) == 1 else MatchStatus.AMBIGUOUS
        return Match(status, raw, MatchMethod.EXACT, tuple(_candidate(e, 100.0) for e in entries))

    def _match_contained(self, raw: str, query: str) -> Match | None:
        best_len = 0
        hits: dict[_Entry, str] = {}
        padded = f" {query} "
        for surface, entries in self._exact.items():
            if len(surface) < 3 or f" {surface} " not in padded:
                continue
            if len(surface) > best_len:
                best_len, hits = len(surface), {}
            if len(surface) == best_len:
                for entry in entries:
                    hits.setdefault(entry, surface)
        if not hits:
            return None
        candidates = tuple(_candidate(e, 100.0) for e in hits)
        if len(hits) > 1:
            return Match(MatchStatus.AMBIGUOUS, raw, MatchMethod.CONTAINS, candidates)
        surface = next(iter(hits.values()))
        leftover = " ".join(padded.replace(f" {surface} ", " ", 1).split())
        return Match(MatchStatus.RESOLVED, raw, MatchMethod.CONTAINS, candidates, leftover)

    def _match_shared_word(self, raw: str, query: str) -> Match | None:
        """The query is part of the names of several entries, so it identifies none."""
        if len(query) < 3 or query in _NEGATIONS:
            return None
        padded = f" {query} "
        entries = [e for e in self.entries if any(padded in f" {s} " for s in e.surfaces)]
        if len(entries) < 2:
            return None
        candidates = tuple(_candidate(e, 100.0) for e in entries)
        return Match(MatchStatus.AMBIGUOUS, raw, MatchMethod.CONTAINS, candidates)

    def _match_fuzzy(self, raw: str, query: str) -> Match:
        scored: list[tuple[float, _Entry, str]] = []
        for entry in self.entries:
            best_score, best_leftover = 0.0, ""
            for surface in entry.surfaces:
                score, leftover = _score_with_window(query, surface)
                if score > best_score:
                    best_score, best_leftover = score, leftover
            if best_score >= CANDIDATE_SCORE:
                scored.append((best_score, entry, best_leftover))
        if not scored:
            return Match(MatchStatus.NOT_FOUND, raw, MatchMethod.FUZZY)
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_entry, leftover = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= RESOLVE_SCORE and best_score - runner_up >= MIN_GAP:
            return Match(
                MatchStatus.RESOLVED,
                raw,
                MatchMethod.FUZZY,
                (_candidate(best_entry, best_score),),
                leftover,
            )
        close = [(sc, e) for sc, e, _ in scored if best_score - sc < MIN_GAP * 2][:MAX_CANDIDATES]
        return Match(
            MatchStatus.AMBIGUOUS,
            raw,
            MatchMethod.FUZZY,
            tuple(_candidate(e, sc) for sc, e in close),
        )


def _candidate(entry: _Entry, score: float) -> Candidate:
    return Candidate(id=entry.id, score=round(score, 1), group_id=entry.group_id)


_NEGATIONS = frozenset({"sin", "no"})


def _score(query: str, surface: str) -> float:
    score = max(fuzz.ratio(query, surface), fuzz.token_sort_ratio(query, surface))
    # A shorter query that is a clean fragment of a longer name ("pizza" in
    # "pizza hawaiana") is a strong signal, but only when it covers a good part of it.
    if len(query) >= 5 and len(query) < len(surface) and len(query) >= 0.5 * len(surface):
        score = max(score, fuzz.partial_ratio(query, surface))
    # "aceitunas" must not silently resolve to "sin aceitunas": a negation mismatch can at most
    # make the option a clarification candidate.
    if bool(_NEGATIONS & set(query.split())) != bool(_NEGATIONS & set(surface.split())):
        score = min(score, RESOLVE_SCORE - 1)
    # Likewise a number the surface does not contain: "2 porciones" is not "8 porciones".
    if _numbers(query) - _numbers(surface):
        score = min(score, RESOLVE_SCORE - 1)
    return float(score)


def _numbers(text: str) -> set[str]:
    return {word for word in text.split() if word.isdigit()}


def _score_with_window(query: str, surface: str) -> tuple[float, str]:
    """Best score of `surface` against the whole query or any same-length word window.

    Returns the score and, when a window matched better, the words left outside it, so
    "piza de pollo" can match "pizza" and keep "de pollo" for modifiers.
    """
    best, leftover = _score(query, surface), ""
    words, size = query.split(), len(surface.split())
    if len(surface) < 4 or size >= len(words):
        return best, leftover
    for start in range(len(words) - size + 1):
        window = " ".join(words[start : start + size])
        score = float(fuzz.ratio(window, surface))
        if score > best:
            best = score
            leftover = " ".join(words[:start] + words[start + size :])
    return best, leftover


def _surfaces(*texts: str | Iterable[str]) -> tuple[str, ...]:
    flat: list[str] = []
    for text in texts:
        flat.extend([text] if isinstance(text, str) else text)
    return tuple(dict.fromkeys(s for s in (normalize(t) for t in flat) if s))


def _is_generic(word: str, generic: set[str]) -> bool:
    """A leftover word that is (a typo of) a word of the product's own name or aliases."""
    return word in generic or any(
        len(word) >= 4 and fuzz.ratio(word, g) >= RESOLVE_SCORE for g in generic
    )


def _matched_words(query: str, leftover: str) -> str:
    """The words of `query` that a match consumed, in order."""
    words = query.split()
    for word in leftover.split():
        words.remove(word)
    return " ".join(words)


class MenuResolver:
    """Resolves customer wording against one restaurant's configuration."""

    def __init__(self, config: RestaurantConfig) -> None:
        self._config = config
        self._menu = config.menu
        entries = {
            p.id: _Entry(p.id, _surfaces(p.name, p.aliases))
            for p in self._menu.products()
            if p.available
        }
        shared: dict[str, list[_Entry]] = {}
        for category in self._menu.categories:
            members = [entries[p.id] for p in category.products if p.id in entries]
            for surface in _surfaces(category.name, category.aliases):
                shared.setdefault(surface, []).extend(members)
        self._products = _Index(list(entries.values()), shared)
        self._payments = _Index(
            [_Entry(m.id, _surfaces(m.label, m.aliases)) for m in config.payment_methods]
        )
        self._modifiers: dict[str, _Index] = {}

    def resolve_product(self, text: str) -> Match:
        """Best product for `text`, ignoring leftover words. Orders go through `resolve_item`."""
        return self._products.match(text, _product_query(text))

    def resolve_payment(self, text: str) -> Match:
        return self._payments.match(text, _strip_leading(normalize(text), _PAYMENT_FILLERS))

    def resolve_modifier(self, product_id: str, text: str) -> Match:
        query = _strip_leading(normalize(text), _LEADING_PIECE_FILLERS)
        return self._modifier_index(product_id).match(text, query)

    def resolve_item(self, text: str, modifier_texts: Iterable[str] = ()) -> ItemResolution:
        """Resolve a product plus its modifiers.

        Modifiers come from `modifier_texts` and from leftover words of the product text
        (e.g. "pizza con champiñones" -> product `pizza`, leftover "con champiñones").

        The whole phrase has to make sense, not only the best alias in it: a leftover word
        that names another product means the first reading may be wrong. The other reading
        is taken when it explains every word.
        """
        product = self.resolve_product(text)
        best = product.best
        if best is None:
            return ItemResolution(product=product)
        explicit = [
            piece for text_ in modifier_texts for piece in self._pieces(best.id, normalize(text_))
        ]
        spanning = self._spanning_modifier(best.id, text, product.leftover)
        if spanning:
            return self._ask_product_for(best.id, text, spanning, explicit)
        pieces = self._pieces(best.id, product.leftover)
        rivals = self._rival_products(best.id, pieces)
        if rivals:
            return self._settle_rivals(text, product, explicit, pieces, rivals)
        modifiers = tuple(self.resolve_modifier(best.id, p) for p in [*explicit, *pieces])
        return ItemResolution(product=product, modifiers=modifiers)

    def _spanning_modifier(self, product_id: str, text: str, leftover: str) -> str | None:
        """A modifier alias that uses words the product match consumed, if any.

        Those words cannot mean both things, so the product was not really named.
        """
        plain = _product_query(text)
        query = f" {plain} "
        rest = f" {leftover} "
        matched = set(_matched_words(plain, leftover).split())
        for entry in self._modifier_index(product_id).entries:
            for surface in entry.surfaces:
                words = set(surface.split())
                if (
                    f" {surface} " in query
                    and f" {surface} " not in rest
                    and words & matched
                    and not words <= matched
                ):
                    return surface
        return None

    def _ask_product_for(
        self, product_id: str, text: str, surface: str, explicit: list[str]
    ) -> ItemResolution:
        """Ask which product of the category, keeping the modifiers the customer gave."""
        option = self.resolve_modifier(product_id, surface)
        category = self._menu.category_of(product_id)
        siblings = category.products if category else ()
        candidates = tuple(
            Candidate(id=p.id, score=100.0)
            for p in siblings
            if p.available and self.resolve_modifier(p.id, surface).best == option.best
        )
        rest = " ".join(f" {_product_query(text)} ".replace(f" {surface} ", " ", 1).split())
        pieces = self._pieces(product_id, rest)
        modifiers = tuple(self.resolve_modifier(product_id, p) for p in [*explicit, *pieces])
        return ItemResolution(
            product=Match(MatchStatus.AMBIGUOUS, text, MatchMethod.CONTAINS, candidates),
            modifiers=(option, *modifiers),
        )

    def _rival_products(self, product_id: str, pieces: Iterable[str]) -> dict[str, Match]:
        """Leftover pieces that are not modifiers of the product but do name another one."""
        rivals = {}
        for piece in pieces:
            modifier = self.resolve_modifier(product_id, piece).status
            if modifier is MatchStatus.RESOLVED or _NEGATIONS & set(piece.split()):
                continue
            match = self.resolve_product(piece)
            # A word that half fits this product only counts if it is exactly another one.
            if modifier is MatchStatus.AMBIGUOUS and match.method is not MatchMethod.EXACT:
                continue
            # A fuzzy near-miss is noise here; the piece stays an unresolved modifier.
            if match.status is MatchStatus.RESOLVED or (
                match.status is MatchStatus.AMBIGUOUS and match.method is not MatchMethod.FUZZY
            ):
                rivals[piece] = match
        return rivals

    def _settle_rivals(
        self,
        text: str,
        product: Match,
        explicit: list[str],
        pieces: list[str],
        rivals: dict[str, Match],
    ) -> ItemResolution:
        """Take the only reading that explains every word, otherwise ask."""
        query = _product_query(text)
        matched = _matched_words(query, product.leftover)
        complete = []
        for piece, rival in rivals.items():
            # Modifiers follow their product, so only a product named first can claim them.
            if query.find(piece) > query.find(matched):
                continue
            others = [p for p in pieces if p != piece]
            for candidate in rival.candidates:
                reading = self._reading(text, rival, candidate, [*explicit, *others, matched])
                if not reading.unresolved_modifiers:
                    complete.append(reading)
        if len(complete) == 1:
            return complete[0]
        found = [*product.candidates, *(c for r in rivals.values() for c in r.candidates)]
        unique = tuple({c.id: c for c in found}.values())
        best = product.candidates[0].id
        kept = [*explicit, *(p for p in pieces if p not in rivals)]
        return ItemResolution(
            product=Match(MatchStatus.AMBIGUOUS, text, MatchMethod.CONTAINS, unique),
            modifiers=tuple(self.resolve_modifier(best, p) for p in kept),
        )

    def _reading(
        self, text: str, rival: Match, candidate: Candidate, pieces: Iterable[str]
    ) -> ItemResolution:
        """The item as `candidate`, with `pieces` as its modifiers."""
        product = Match(MatchStatus.RESOLVED, text, rival.method, (candidate,))
        modifiers = tuple(self.resolve_modifier(candidate.id, p) for p in pieces)
        return ItemResolution(product=product, modifiers=modifiers)

    def _pieces(self, product_id: str, text: str) -> list[str]:
        """Split normalized modifier text into pieces of one modifier each."""
        if not text:
            return []
        if self.resolve_modifier(product_id, text).method is MatchMethod.EXACT:
            return [text]  # the alias may contain a connector
        generic = self._generic_words(product_id)
        pieces = []
        for raw_piece in _SPLIT_CONNECTORS.split(text):
            piece = _strip_leading(raw_piece.strip(), _LEADING_PIECE_FILLERS)
            if piece and not all(_is_generic(word, generic) for word in piece.split()):
                pieces.extend(self._single_modifiers(product_id, piece))
        return pieces

    def _single_modifiers(self, product_id: str, piece: str) -> list[str]:
        """Separate a modifier from words next to it, which must not be lost."""
        match = self.resolve_modifier(product_id, piece)
        if match.best is None or not match.leftover:
            return [piece]
        used = _matched_words(piece, match.leftover)
        return [used, *self._pieces(product_id, match.leftover)]

    def _generic_words(self, product_id: str) -> set[str]:
        """Words that only repeat the product's name. Negated aliases say something else."""
        product = self._menu.product(product_id)
        category = self._menu.category_of(product_id)
        surfaces: list[str] = []
        if product:
            surfaces += _surfaces(product.name, product.aliases)
        if category:
            surfaces += _surfaces(category.name, category.aliases)
        return {w for s in surfaces if not _NEGATIONS & set(s.split()) for w in s.split()}

    def _modifier_index(self, product_id: str) -> _Index:
        if product_id not in self._modifiers:
            product = self._menu.product(product_id)
            groups = self._menu.groups_for(product) if product else ()
            self._modifiers[product_id] = _Index(
                [
                    _Entry(o.id, _surfaces(o.label, o.aliases), group_id=g.id)
                    for g in groups
                    for o in g.options
                ]
            )
        return self._modifiers[product_id]
