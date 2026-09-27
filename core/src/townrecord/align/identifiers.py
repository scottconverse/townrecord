"""Identifiers in agenda titles and in transcript text (spec 10.1).

Spec 10.1 asks for deterministic extraction of ordinance and resolution
numbers, agenda item numbers, dollar amounts, addresses, parcel numbers,
names and dates. This module does the two the aligner needs: ordinance and
resolution numbers, and agenda item numbers. Everything here is a pure
function of text. There is no model and no guess.

Two things make the transcript differ from the agenda, and both are measured
on the September 8, 2026 Longmont transcript (video 3qfQAkAAC9U):

* Captions write a number the way it was spoken. The agenda writes
  ``O-2026-54``; the transcript writes ``ordinance 2026-54,`` at 3978 s,
  ``ordinance 202654.`` at 4374 s, ``2026-56,`` at 4512 s and
  ``resolution R202652`` at 5568 s. So a number is read out of any of those
  shapes and normalised to one canonical identifier.
* A spoken number often has its kind word a word or two in front, with a
  filler between (``ordinance um 2026-58``, from spec 10.1). The kind is
  read from the last kind word within a small window before the number, so a
  bare ``2026-56`` keeps its kind unknown rather than picking one.

An identifier whose kind is unknown still matches an agenda item, because the
item's own title gives the kind. A number that carries a kind only matches an
item of that kind, so ``resolution 2026-54`` never matches ordinance
``O-2026-54``.

Agenda item numbers are read the same way in both directions: an agenda
number ``10.A.1`` becomes the parts ``("10", "A", "1")`` and a spoken
``item A1`` becomes ``("A", "1")``. A spoken reference matches an item when
its parts are the tail of the item's parts. That is how "item 9C" finds
``9.C`` and "item A1" finds ``10.A.1`` without either side being rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ORDINANCE",
    "RESOLUTION",
    "Identifier",
    "ItemReference",
    "find_identifiers",
    "find_item_references",
    "significant_words",
    "words_of",
]

#: The two kinds of numbered record this module reads (spec 10.1).
ORDINANCE = "ordinance"
RESOLUTION = "resolution"

_KIND_LETTER = {ORDINANCE: "O", RESOLUTION: "R"}
_LETTER_KIND = {"o": ORDINANCE, "r": RESOLUTION}

#: The number words spec 10.2 names: one to twenty.
NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

#: Every shape a spoken or written identifier takes. The alternatives are
#: tried left to right at each position, and the longest sensible one wins
#: because it is written first:
#:
#: 1. ``O-2026-54``  the agenda's written form
#: 2. ``R202652``    a written letter glued to the digits
#: 3. ``20 26-58``   a spoken year split in two (spec 10.1)
#: 4. ``2026-54``    digits with a dash and no kind
#: 5. ``202654``     the whole number spoken as one run (spec 10.1, measured)
#:
#: Nothing here matches a bare four digit year, so "September 8th, 2026" and
#: "2027 proposed budget" raise no identifier.
_IDENTIFIER = re.compile(
    r"(?P<letter>\b[OR])\s*-\s*(?P<year1>20\d{2})\s*-\s*(?P<number1>\d{1,3})\b"
    r"|(?P<letter2>\b[OR])\s*(?P<year2>20\d{2})(?P<number2>\d{2})\b"
    r"|\b(?P<year3>20)\s(?P<year3tail>\d{2})\s*-\s*(?P<number3>\d{1,3})\b"
    r"|\b(?P<year4>20\d{2})\s*-\s*(?P<number4>\d{1,3})\b"
    r"|\b(?P<year5>20\d{2})(?P<number5>\d{2})\b"
)

#: How much text before a number is looked at for a kind word. Enough for
#: "with item A1, ordinance 2026-54," and short enough not to reach back into
#: an earlier sentence.
_KIND_LOOKBACK_CHARS = 80

_WORD = re.compile(r"[A-Za-z']+")

#: The words that introduce an agenda item number in speech (spec 10.2).
_AFTER_ITEM_MARKER = re.compile(
    r"\b(?:agenda\s+)?items?\s+(?P<reference>[^\s]+(?:\s+[^\s]+){0,2})",
    re.IGNORECASE,
)

#: One word as a spoken item number: ``9``, ``9B``, ``A1``, ``nine``, ``B``.
_WORD_SHAPE = re.compile(
    r"\A(?:(?P<digits>\d{1,2})(?P<letter>[a-z])?"
    r"|(?P<letter2>[a-z])(?P<digits2>\d{1,2})?"
    r"|(?P<word>[a-z]+))\Z"
)

#: Punctuation that clings to a word and does not change what it says.
_EDGE_PUNCTUATION = ".,;:!?\"'()[]"


@dataclass(frozen=True)
class Identifier:
    """One ordinance or resolution number read out of text (spec 10.1).

    ``kind`` is ``"ordinance"``, ``"resolution"`` or ``None`` when the text
    gives no kind word. ``letter`` holds the written ``O`` or ``R`` when the
    text wrote one, which is what tells a written form from a spoken one.
    """

    kind: str | None
    year: int
    number: int
    text: str
    start: int
    end: int
    letter: str | None = None

    def key(self) -> tuple[int, int]:
        """The pair that makes two identifiers the same record: year and number."""
        return (self.year, self.number)

    def canonical(self) -> str:
        """``O-2026-54``, ``R-2026-52``, or ``2026-56`` when no kind was heard."""
        letter = _KIND_LETTER.get(self.kind or "")
        prefix = f"{letter}-" if letter else ""
        return f"{prefix}{self.year}-{self.number:02d}"

    def is_written(self) -> bool:
        """Say whether the text wrote the kind letter rather than speaking it."""
        return self.letter is not None


@dataclass(frozen=True)
class ItemReference:
    """A spoken agenda item number, as its parts (spec 10.2).

    ``("11",)`` is "agenda item 11". ``("A", "1")`` is "item A1", which is
    how the chair cites agenda item ``10.A.1``. ``text`` is what the caption
    actually says, kept for the evidence line.
    """

    parts: tuple[str, ...]
    text: str
    start: int
    end: int

    def matches(self, item_parts: tuple[str, ...]) -> bool:
        """Say whether these parts are the tail of an agenda item's parts."""
        if not self.parts or len(self.parts) > len(item_parts):
            return False
        return item_parts[-len(self.parts) :] == self.parts


def _kind_before(text: str, start: int, window_words: int) -> str | None:
    """Read the kind word spoken before an identifier, if there is one.

    The nearest kind word wins: in "the resolution and the ordinance
    2026-54" the ordinance is the one being numbered.
    """
    window = text[max(0, start - _KIND_LOOKBACK_CHARS) : start]
    for word in reversed(_WORD.findall(window)[-window_words:]):
        lowered = word.lower()
        if lowered.startswith(ORDINANCE):
            return ORDINANCE
        if lowered.startswith(RESOLUTION):
            return RESOLUTION
    return None


def _first_group(match: re.Match[str], *names: str) -> str | None:
    """Return the first of these groups that matched, or None."""
    for name in names:
        value = match.group(name)
        if value is not None:
            return value
    return None


def find_identifiers(text: str, *, kind_window_words: int = 4) -> tuple[Identifier, ...]:
    """Return every ordinance and resolution number in the text, in order.

    Args:
        text: Agenda title, agenda item text or one transcript segment.
        kind_window_words: How many words before a number may hold its kind
            word. Four covers "with item A1, ordinance 2026-54," and the
            filler in "ordinance um 2026-58".

    Returns:
        The identifiers, left to right. Each carries what it matched, so the
        evidence a boundary rests on can be quoted back to the user.
    """
    found: list[Identifier] = []
    for match in _IDENTIFIER.finditer(text):
        letter = _first_group(match, "letter", "letter2")
        year = _first_group(match, "year1", "year2", "year3", "year4", "year5")
        number = _first_group(match, "number1", "number2", "number3", "number4", "number5")
        if year is None or number is None:
            continue
        if match.group("year3") is not None:
            # "20 26-58": the year was spoken in two halves.
            year = match.group("year3") + match.group("year3tail")
        kind = _LETTER_KIND[letter.lower()] if letter else _kind_before(text, match.start(), 4)
        found.append(
            Identifier(
                kind=kind,
                year=int(year),
                number=int(number),
                text=match.group(0),
                start=match.start(),
                end=match.end(),
                letter=letter,
            )
        )
    return tuple(found)


def _parts_of_word(word: str) -> tuple[str, ...]:
    """Read one word as item number parts, or return nothing if it is not one."""
    cleaned = word.lower().strip(_EDGE_PUNCTUATION)
    if not cleaned:
        return ()
    match = _WORD_SHAPE.match(cleaned)
    if match is None:
        return ()
    digits = match.group("digits")
    if digits is not None:
        parts = [str(int(digits))]
        letter = match.group("letter")
        return tuple(parts + [letter.upper()]) if letter else tuple(parts)
    letter = match.group("letter2")
    if letter is not None:
        parts = [letter.upper()]
        trailing = match.group("digits2")
        return tuple(parts + [str(int(trailing))]) if trailing else tuple(parts)
    word = match.group("word") or ""
    if word in NUMBER_WORDS:
        return (str(NUMBER_WORDS[word]),)
    return ()


def _reference_parts(text: str, *, max_parts: int = 3) -> tuple[str, ...]:
    """Read up to three item number parts from the words after an item marker."""
    parts: list[str] = []
    for word in text.split():
        pieces = _parts_of_word(word)
        if not pieces:
            break
        parts.extend(pieces)
        if len(parts) >= max_parts:
            break
    return tuple(parts[:max_parts])


def find_item_references(text: str) -> tuple[ItemReference, ...]:
    """Return every spoken agenda item number in the text (spec 10.2).

    Only a number that follows "item", "items" or "agenda item" counts. That
    is what keeps "each speaker is limited to three minutes" out: a bare
    number word is not an item number, and the reading of the council's rules
    at 74 s to 94 s of the September 8, 2026 video uses three of them.

    "item 9 items removed from consent agenda" yields ``("9",)`` and stops at
    "items", which is not a number.
    """
    found: list[ItemReference] = []
    for match in _AFTER_ITEM_MARKER.finditer(text):
        parts = _reference_parts(match.group("reference"))
        if not parts:
            continue
        reference_text = match.group(0)
        found.append(
            ItemReference(
                parts=parts,
                text=reference_text.strip(),
                start=match.start(),
                end=match.end(),
            )
        )
    return tuple(found)


def words_of(text: str) -> tuple[str, ...]:
    """Return the alphabetic words of the text, lowercased, in order."""
    return tuple(word.lower() for word in _WORD.findall(text))


def significant_words(text: str, *, min_length: int = 4) -> tuple[str, ...]:
    """Return the words of the text longer than ``min_length``, without repeats.

    Spec 10.2's verbatim title check counts words over four letters, so the
    default drops "call" and keeps "first".
    """
    seen: dict[str, None] = {}
    for word in words_of(text):
        if len(word) > min_length:
            seen.setdefault(word, None)
    return tuple(seen)
