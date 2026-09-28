"""The evidence a model gets, and the check on what it wrote (spec 11.7).

Spec 11.7 is four rules and one division of labour:

* the model gets only the evidence this program hands it, and every piece of
  that evidence carries a citation handle;
* every sentence the model writes must cite at least one handle, every quote
  in it must appear verbatim in the evidence it cited, and every number and
  name in it must appear in that evidence;
* "Not found" is a valid answer and is better than a guess;
* code measures first, the model rewrites second, code measures again. A model
  never decides what counts as a fault, so nothing in this module asks a model
  whether an answer is grounded. The check is code and only code.

The module is in four pieces:

* :class:`EvidencePack` is the only evidence a model is given. It is built here
  from rows this program already stored: the timed lines of a transcript and
  the pages of a record, each with the artifact id and the SHA-256 that
  spec 10.5 wants a citation to carry, plus the stored votes of spec 10.4.
* :func:`check_sentence` and :func:`check_answer` are the check. They read
  strings and a pack and return verdicts. They call nothing.
* :func:`facts` and :func:`changed_facts` are the rule that quotes, numbers,
  names and URLs never change in a repair.
* :func:`ground` is the loop: measure, ask once for a repair, measure again,
  and remove a sentence that still fails.

Nothing here calls a model. A repair is asked through a plain callable, and
:class:`LadderAsk` is the one that builds a real one out of the preflight and
the ladder of spec 11.4 and 11.6. A caller hands in the call itself, so the
whole module is driven by fakes in the tests (PROJECT-BRIEF rule 9).

What is normalized, and nothing else

A quote is compared after two normalizations: runs of whitespace (including the
non-breaking spaces a PDF prints) collapse to one space, and the curly quotes
and apostrophes are straightened. Case, punctuation and every word are
compared as written. A number is compared as its digits with the thousands
separators removed, and a number written as a word ("five") is compared as the
number it names, so a repair may respell five as 5 and may not change five to
six.

What this module is strict about, on purpose

A number in a sentence must appear in the evidence that same sentence cites.
That includes a spelled number, so an answer that says "one of the items" and
cites evidence that never says one or 1 fails. The spec says every number
appears in the cited evidence and this is that rule with no exceptions; a
looser rule would be this program deciding which numbers count.

And a name in a sentence must be a person the cited evidence names. The people
are read from the database and never from the sentence: the roster is the
people seated on the meeting's body on its date, and the titles that name one
office (spec 10.6, :func:`townrecord.repo.seat_holders_of`). A sentence that
prints a surname the roster holds must print it in its cited spans too, and a
title printed with no surname ("the Mayor") stands on the holder of that seat.
A name or a title the roster does not hold is not read as a person at all —
see :func:`_names_not_in_evidence` for what that means and why.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from ..repo import (
    agenda_items,
    citation_sha256,
    get_meeting,
    latest_transcript,
    officials_of,
    primary_video,
    record_pages,
    records_of_meeting,
    seat_holders_of,
    segments_of,
    votes_of_item,
)
from .failover import REASON_UNAVAILABLE, Attempt, TaskRun, run_task
from .ladder import Ladder
from .preflight import Allowance, Checks, preflight
from .providers import Provider, ProviderRegistry

#: The handle a sentence cites, as spec 11.7 writes it: ``[E3]``.
HANDLE_PATTERN = re.compile(r"\[E(\d+)\]")

#: What a span of evidence came from.
KIND_VIDEO = "video"
KIND_RECORD = "record"
KIND_VOTE = "vote"
KINDS: tuple[str, ...] = (KIND_VIDEO, KIND_RECORD, KIND_VOTE)

#: The answer spec 11.7 calls valid, and the verdicts spec 12.9 gives a claim
#: the archive does not hold. A sentence that is one of these needs no handle:
#: it claims nothing, so there is nothing to cite.
NOT_FOUND = "Not found."
_NOT_FOUND_SENTENCES = frozenset(
    {
        "not found",
        "not found in the record",
        "not in the record",
        "no record found",
        "not found in the archive",
        "record not available yet",
        "the record is not available yet",
    }
)

#: Why a sentence passed or failed. A caller that wants to say something of its
#: own reads the code, not the sentence.
CODE_OK = "ok"
CODE_NOT_FOUND = "not-found"
CODE_EMPTY = "empty"
CODE_NO_HANDLE = "no-handle"
CODE_UNKNOWN_HANDLE = "unknown-handle"
CODE_NO_EVIDENCE = "no-evidence"
CODE_QUOTE = "quote-not-verbatim"
CODE_TALLY = "tally-without-a-stored-vote"
CODE_TALLY_MISMATCH = "tally-is-not-the-stored-vote"
CODE_NUMBER = "number-not-in-evidence"
CODE_NAME = "name-not-in-evidence"
CODE_NO_REPAIR = "no-repair"
CODE_REPAIR_CHANGED = "repair-changed-a-fact"
CODE_REPAIR_FAILED = "repair-still-failed"

#: What happened to one sentence of an answer.
OUTCOME_KEPT = "kept"
OUTCOME_REPAIRED = "repaired"
OUTCOME_REMOVED = "removed"

#: How many spans a repair prompt shows when the sentence cited none. A prompt
#: is not a place to send a whole meeting's transcript; the count that was left
#: out is printed with the prompt rather than passed over in silence.
PACK_PROMPT_LIMIT = 60

#: The quote marks that are straightened before a quote is compared.
_QUOTE_MARKS = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
    }
)

#: A run of digits, with the thousands separators a person writes.
_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: A counted vote, and not an ordinance number: ``7-0`` and ``7 - 0`` are
#: tallies, ``O-2026-58`` and ``9-8-2026`` are not. Each side is at most three
#: digits, and neither side may sit against another digit or a dash.
_TALLY = re.compile(r"(?<![\w–—-])(\d{1,3})\s*[-–—]\s*(\d{1,3})(?![\d–—-])")

#: The numbers a person may spell rather than print. This is the matcher
#: spec 10.1 asks for in the other direction: a vote of seven may be written 7.
_SPELLED: Mapping[str, int] = {
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
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SPELLED_WORD = re.compile(r"\b(" + "|".join(_SPELLED) + r")\b", re.IGNORECASE)

#: A proper noun, roughly: one or more capitalized words in a row. It is used
#: only by :func:`facts`, which is about what a repair may not change, so a
#: name this reads too widely is a repair that is refused rather than a claim
#: that is accepted.
_NAME = re.compile(r"\b[A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*)*")

#: An address. Trailing punctuation is not part of it.
_URL = re.compile(r"https?://[^\s)\]>\"']+")

#: A quoted passage, after the quote marks have been straightened.
_QUOTED = re.compile(r'"([^"]*)"')

#: The words that end a sentence, per the style this program writes in.
_TERMINATOR = re.compile(r"(?<=[.!?])\s+")

#: The words that carry a period without ending a sentence. A fragment that
#: follows one of these is the rest of the same sentence.
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "st",
        "mt",
        "no",
        "sec",
        "art",
        "exh",
        "inc",
        "ltd",
        "co",
        "vs",
        "jr",
        "sr",
        "etc",
        "approx",
        "dept",
        "u.s",
        "u.s.a",
        "sept",
        "oct",
        "nov",
        "dec",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
    }
)

#: A word at the start of a sentence that is capitalized because of where it
#: is and not because it names something.
_SENTENCE_STARTERS = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "it",
        "he",
        "she",
        "they",
        "we",
        "i",
        "in",
        "on",
        "at",
        "after",
        "before",
        "during",
        "under",
        "over",
        "not",
        "no",
        "yes",
        "and",
        "but",
        "if",
        "when",
        "while",
        "there",
        "here",
        "one",
        "two",
        "three",
        "all",
        "both",
        "most",
        "some",
        "council",
        "city",
        "staff",
        "members",
        "mayor",
    }
)


# --------------------------------------------------------------- normalizing --


def normalize(text: str) -> str:
    """Fold whitespace and straighten quote marks. Nothing else is changed."""
    return re.sub(r"\s+", " ", str(text).translate(_QUOTE_MARKS)).strip()


def _whole_words(phrase: str) -> re.Pattern[str]:
    """A pattern matching this phrase where a folded text prints it as a word.

    Used for a name, and only for a name: both sides are casefolded first, so
    the capitalization a page prints does not decide whether a person is named.
    The guards around the phrase are letters and digits, so the surname ``Ann``
    is not found inside ``Annual`` and ``McCoy`` is not found inside ``McCoys``.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])")


def _says(text: str, phrase: str) -> bool:
    """True when a folded text prints this phrase as whole words."""
    return _whole_words(phrase).search(text) is not None


def _spent(text: str, phrase: str) -> str:
    """A folded text with every whole-word print of this phrase blanked out.

    The words are replaced by spaces of the same length, so the text is the
    same length and a match found in it before this call still points at the
    same place afterwards.
    """
    return _whole_words(phrase).sub(lambda found: " " * len(found.group()), text)


def _followed_by_a_surname(tail: str, surnames: set[str]) -> bool:
    """True when one of these surnames is the first word after this point."""
    return any(
        re.match(rf"\s*{re.escape(surname)}(?![a-z0-9])", tail) for surname in surnames if surname
    )


def is_not_found(sentence: str) -> bool:
    """True when this sentence is one of the answers that claims nothing.

    Spec 11.7 makes "Not found" a valid answer, and spec 12.9 gives the two
    verdicts a claim the archive does not hold may have. A sentence that is one
    of these needs no citation, because it asserts nothing about the record.
    """
    folded = normalize(sentence).rstrip(".!" if sentence.endswith(("!", ".")) else ".").lower()
    return folded in _NOT_FOUND_SENTENCES


def split_sentences(text: str) -> tuple[str, ...]:
    """Split an answer into sentences.

    The split is on a terminator that is followed by whitespace, and a fragment
    that is the rest of the sentence before it is put back: "Sept. 8" is one
    sentence and not two, and so is anything that continues in lower case or
    with a digit. A splitter that got this wrong would report a fault against
    the model for a sentence the model did not write.
    """
    stripped = str(text).strip()
    if not stripped:
        return ()
    pieces = _TERMINATOR.split(stripped)
    sentences: list[str] = []
    for piece in pieces:
        if sentences and _joins(sentences[-1], piece):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return tuple(sentence.strip() for sentence in sentences if sentence.strip())


def _joins(previous: str, following: str) -> bool:
    """True when ``following`` is the rest of the sentence ``previous`` began."""
    head = previous.rstrip()
    if head[-1:] not in (".", "!", "?"):
        return True
    last = head[:-1].split()[-1] if head[:-1].split() else ""
    if last.lower().strip(".") in _ABBREVIATIONS:
        return True
    first = following.lstrip()[:1]
    return bool(first) and (first.islower() or first.isdigit())


def handles_in(text: str) -> tuple[str, ...]:
    """Every citation handle in this text, once each, in the order it is cited."""
    seen: list[str] = []
    for found in HANDLE_PATTERN.findall(str(text)):
        handle = f"[E{found}]"
        if handle not in seen:
            seen.append(handle)
    return tuple(seen)


def without_handles(text: str) -> str:
    """This text with its citation handles taken out.

    A handle is not part of what a sentence says. ``[E1]`` holds a digit, and
    reading that digit as a number the sentence claimed would fail every
    sentence that cites the first span, which is the opposite of the rule.
    """
    return HANDLE_PATTERN.sub(" ", str(text))


def quotes_in(text: str) -> tuple[str, ...]:
    """Every quoted passage in this text, normalized, once each."""
    found: list[str] = []
    for quote in _QUOTED.findall(normalize(without_handles(text))):
        folded = normalize(quote)
        if folded and folded not in found:
            found.append(folded)
    return tuple(found)


def _numbers_in(text: str) -> set[str]:
    """Every number in this text as its digits, spelled numbers included."""
    normalized = normalize(without_handles(text))
    found = {match.group(0).replace(",", "").rstrip(".") for match in _DIGITS.finditer(normalized)}
    for word in _SPELLED_WORD.findall(normalized):
        found.add(str(_SPELLED[word.lower()]))
    return {number for number in found if number}


def _digits_with_places(text: str) -> list[tuple[str, int, int]]:
    """Every digit run in this text with where it sits, so a tally can be left out."""
    normalized = normalize(without_handles(text))
    return [
        (match.group(0).replace(",", "").rstrip("."), match.start(), match.end())
        for match in _DIGITS.finditer(normalized)
    ]


def tallies_in(text: str) -> tuple[tuple[int, int], ...]:
    """Every counted vote in this text, as (yes, no) pairs.

    An ordinance number is not a tally: ``O-2026-58`` has a dash against a
    letter and two four-digit sides, and neither shape is a counted vote.
    """
    return tuple(
        (int(match.group(1)), int(match.group(2))) for match in _TALLY.finditer(normalize(text))
    )


def _numbers_claimed(text: str) -> set[str]:
    """The numbers a sentence claims, with a tally's own digits left out.

    A tally is checked against the stored vote rather than against the text
    around it, so its digits are not asked for a second time here.
    """
    normalized = normalize(without_handles(text))
    ranges = [match.span() for match in _TALLY.finditer(normalized)]
    claimed = {
        number
        for number, start, end in _digits_with_places(normalized)
        if not any(span_start <= start and end <= span_end for span_start, span_end in ranges)
    }
    for word, start, end in _spelled_with_places(normalized):
        if not any(span_start <= start and end <= span_end for span_start, span_end in ranges):
            claimed.add(str(_SPELLED[word]))
    return {number for number in claimed if number}


def _spelled_with_places(text: str) -> list[tuple[str, int, int]]:
    """Every spelled number in this text with where it sits."""
    return [
        (match.group(1).lower(), match.start(1), match.end(1))
        for match in _SPELLED_WORD.finditer(text)
    ]


# ------------------------------------------------------------ the facts rule --


@dataclass(frozen=True)
class Facts:
    """The things a repair may never change (spec 11.7)."""

    quotes: frozenset[str] = frozenset()
    numbers: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    urls: frozenset[str] = frozenset()

    def is_empty(self) -> bool:
        """True when this text held nothing a repair could change."""
        return not (self.quotes or self.numbers or self.names or self.urls)


def facts(text: str) -> Facts:
    """Read the quotes, numbers, names and URLs out of one piece of writing.

    A number is read as the value it names, so ``five`` and ``5`` are the same
    fact and a repair that only respells one has changed nothing. A name is a
    run of capitalized words, which reads some ordinary phrases as names; that
    is the direction that refuses a repair rather than accepting one, which is
    the direction spec 11.7 asks for.

    The citation handles come out first, for the reason
    :func:`without_handles` gives: ``[E3]`` is not part of what a sentence says.
    Reading the ``E`` of a handle as a name would refuse the most ordinary
    repair there is, the one that answers "it cites no handle" by citing one.
    """
    normalized = normalize(without_handles(text))
    names: list[str] = []
    for match in _NAME.finditer(normalized):
        words = match.group(0).split()
        if match.start() == 0 and len(words) == 1 and words[0].lower() in _SENTENCE_STARTERS:
            continue
        if match.start() == 0:
            words = words[1:]
        if words:
            names.append(" ".join(words))
    return Facts(
        quotes=frozenset(quotes_in(normalized)),
        numbers=frozenset(_numbers_in(normalized)),
        names=frozenset(names),
        urls=frozenset(match.group(0).rstrip(".,;:") for match in _URL.finditer(normalized)),
    )


def changed_facts(before: Facts, after: Facts) -> tuple[str, ...]:
    """What a repair changed, in plain words. Empty means it changed nothing.

    A fact that disappeared and a fact that appeared are both changes: a repair
    that drops a number it cannot support is the model editing the record, and
    a repair that adds one is the model inventing one. Spec 11.7 refuses both.
    """
    changed: list[str] = []
    for word, old, new in (
        ("quote", before.quotes, after.quotes),
        ("number", before.numbers, after.numbers),
        ("name", before.names, after.names),
        ("URL", before.urls, after.urls),
    ):
        for gone in sorted(old - new):
            changed.append(f"the {word} {gone!r} is gone")
        for added in sorted(new - old):
            changed.append(f"the {word} {added!r} was added")
    return tuple(changed)


# --------------------------------------------------------------- the evidence --


def render_ms(milliseconds: int) -> str:
    """A moment in a video as hours, minutes and seconds."""
    seconds = int(milliseconds) // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


@dataclass(frozen=True)
class Span:
    """One piece of evidence, with the handle a sentence cites it by.

    ``artifact_id`` and ``sha256`` are what spec 10.5 wants a citation to
    carry. They are read from the artifact row, never copied out of it.
    """

    handle: str
    kind: str
    text: str
    label: str
    artifact_id: int | None = None
    sha256: str = ""
    origin: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    page_number: int | None = None
    agenda_item_id: int | None = None
    result: str = ""
    source_kind: str = ""
    tally: Mapping[str, int] | None = None
    citation_id: int | None = None

    def cite(self) -> str:
        """The citation spec 10.5 asks for, in one plain sentence."""
        if self.kind == KIND_VOTE:
            where = f"the {self.source_kind} record of the vote"
            counted = ""
            if self.tally:
                counted = ", counted " + ", ".join(
                    f"{name} {value}" for name, value in sorted(self.tally.items())
                )
            return f"{where}, which is {self.result}{counted} [sha256 {self.sha256}]"
        if self.kind == KIND_RECORD:
            page = f"page {self.page_number}" if self.page_number is not None else "no page number"
            return f"{self.label}, {page} [sha256 {self.sha256}]"
        return f"{self.label} [{self.origin}; sha256 {self.sha256}]"


@dataclass(frozen=True)
class Official:
    """One person the database knows, as the name check reads them (spec 10.6).

    ``surname`` is the last word of the name the database holds, which is the
    part a record prints with a title ("Council Member McCoy", "Mayor Pro Tem
    McCoy"). A one-word name is its own surname.
    """

    name: str
    surname: str


@dataclass(frozen=True)
class Roster:
    """Who the database knows at one meeting, and the seats that name one person.

    ``officials`` is every person seated on the meeting's body on its date, and
    ``holders`` is the titles that name one office, folded, with the person who
    held each on that date. A title every member holds names nobody and is not
    here: a reading of "Council Member" stands on no sound (spec 10.6,
    :func:`townrecord.repo.seat_holders_of`).

    An empty roster is the honest answer for a pack built by hand or from a
    meeting whose people were never read: the check then says nothing about any
    name, because there is nothing it knows.
    """

    officials: tuple[Official, ...] = ()
    holders: Mapping[str, Official] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePack:
    """The only evidence a model is given (spec 11.7).

    ``dropped`` is how many spans a limit left out. It is reported and never
    passed over: a model that cannot see a span cannot cite it, and a caller
    that cut the pack short should know that (spec 16.3).

    ``roster`` is who the database knows at this meeting. It is not evidence —
    no handle reaches it and a sentence may not cite it — it is the list the
    check reads a name against, so a sentence that names somebody the cited
    spans never name fails (spec 11.7 with spec 10.6).
    """

    spans: tuple[Span, ...] = ()
    dropped: int = 0
    roster: Roster = Roster()

    def __len__(self) -> int:
        return len(self.spans)

    def __iter__(self) -> Iterator[Span]:
        return iter(self.spans)

    def __contains__(self, handle: object) -> bool:
        return any(span.handle == handle for span in self.spans)

    def get(self, handle: str) -> Span | None:
        """The span that handle names, or None when the pack holds no such span."""
        for span in self.spans:
            if span.handle == handle:
                return span
        return None

    def handles(self) -> tuple[str, ...]:
        """Every handle in the pack, in the order the spans are held."""
        return tuple(span.handle for span in self.spans)

    def prompt(self, *, limit: int = PACK_PROMPT_LIMIT) -> str:
        """The pack as the text a model is given."""
        lines = [
            "The evidence. Every sentence you write must cite at least one of these handles.",
            "",
        ]
        for span in self.spans[:limit]:
            lines.append(f"{span.handle} ({span.cite()}) {normalize(span.text)}")
        left_out = len(self.spans) - min(len(self.spans), limit)
        if left_out:
            lines.append(f"... and {left_out} more spans, which are not shown here.")
        return "\n".join(lines)

    def sentence(self) -> str:
        """One plain sentence about what the pack holds."""
        kinds: dict[str, int] = {}
        for span in self.spans:
            kinds[span.kind] = kinds.get(span.kind, 0) + 1
        if not self.spans:
            return "The evidence pack is empty, so nothing can be cited."
        counted = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
        said = f"The evidence pack holds {len(self.spans)} spans: {counted}."
        if self.dropped:
            said += f" A limit left out {self.dropped} more."
        return said


def evidence_pack(
    conn: sqlite3.Connection, *, meeting_id: int, limit: int | None = None
) -> EvidencePack:
    """Build the pack for one meeting out of the rows this program stored.

    The spans are the timed lines of the meeting's video, the pages of its
    records, and the votes stored on its items (:mod:`townrecord.repo`). Each
    one carries the artifact id and the SHA-256 a citation needs, and a vote
    also carries the counted result spec 10.4 wants, because a tally is
    answered from the vote and never from the words around it.

    ``limit`` keeps the first spans and reports the rest as dropped. Nothing
    is silently narrowed: the caller that wants a small pack gets a count of
    what it left out.
    """
    built: list[Span] = []
    video = primary_video(conn, meeting_id)
    if video is not None:
        transcript = latest_transcript(conn, video.id)
        if transcript is not None:
            digest = _artifact_hash(conn, transcript.artifact_id)
            for segment in segments_of(conn, transcript.id):
                where = f"{render_ms(segment.start_ms)} to {render_ms(segment.end_ms)}"
                built.append(
                    Span(
                        handle="",
                        kind=KIND_VIDEO,
                        text=segment.text,
                        label=f"{transcript.origin} at {where}",
                        artifact_id=transcript.artifact_id,
                        sha256=digest,
                        origin=transcript.origin,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                    )
                )
    for record in records_of_meeting(conn, meeting_id):
        digest = _artifact_hash(conn, record.artifact_id)
        for page in record_pages(conn, record.id):
            built.append(
                Span(
                    handle="",
                    kind=KIND_RECORD,
                    text=page.text,
                    label=f"{record.kind} page {page.page_number}",
                    artifact_id=record.artifact_id,
                    sha256=digest,
                    page_number=page.page_number,
                )
            )
    for item in agenda_items(conn, meeting_id):
        for vote in votes_of_item(conn, item.id):
            tally = _tally_of(vote.tally)
            built.append(
                Span(
                    handle="",
                    kind=KIND_VOTE,
                    text=_vote_text(vote.evidence, tally),
                    label=f"the vote on item {item.number}",
                    sha256=(
                        ""
                        if vote.citation_id is None
                        else citation_sha256(conn, vote.citation_id) or ""
                    ),
                    agenda_item_id=item.id,
                    result=vote.result,
                    source_kind=vote.source_kind,
                    tally=tally,
                    citation_id=vote.citation_id,
                )
            )

    kept = built if limit is None else built[: max(int(limit), 0)]
    spans = tuple(replace(span, handle=f"[E{index}]") for index, span in enumerate(kept, start=1))
    return EvidencePack(
        spans=spans, dropped=len(built) - len(kept), roster=_roster_of(conn, meeting_id)
    )


def _surname(name: str) -> str:
    """The last word of a name, folded. A one-word name is its own surname."""
    words = normalize(name).split()
    return words[-1] if words else ""


def _day_of(starts_at: str) -> date | None:
    """The local day a meeting started on, or None when the row says nothing."""
    try:
        return date.fromisoformat(str(starts_at)[:10])
    except ValueError:
        return None


def _roster_of(conn: sqlite3.Connection, meeting_id: int) -> Roster:
    """Who the database knows at one meeting (S2, spec 10.6).

    The people are the ones seated on the meeting's body on the day it met, and
    the titles are the ones that name a single office and so name one person.
    A meeting with no row, or a row whose start no date can be read from, has
    no roster: the check then knows nobody and reads no name as a person.
    """
    meeting = get_meeting(conn, meeting_id)
    if meeting is None:
        return Roster()
    day = _day_of(meeting.starts_at)
    if day is None:
        return Roster()
    officials = tuple(
        Official(name=person.name, surname=_surname(person.name))
        for person in officials_of(conn, body_id=meeting.body_id, on=day)
    )
    holders = {
        normalize(title).casefold(): Official(name=person.name, surname=_surname(person.name))
        for title, person in seat_holders_of(conn, body_id=meeting.body_id, on=day).items()
    }
    return Roster(officials=officials, holders=holders)


def _vote_text(evidence: str, tally: Mapping[str, int] | None) -> str:
    """What a vote span prints: its own evidence, and the count when it has one."""
    if not tally:
        return evidence
    counted = " ".join(f"{name}: {value}" for name, value in sorted(tally.items()))
    return f"{evidence} ({counted})"


def _tally_of(tally: Mapping[str, Any] | None) -> dict[str, int] | None:
    """The counted result of a vote as whole numbers, or None when it has none."""
    if not tally:
        return None
    counted: dict[str, int] = {}
    for name, value in tally.items():
        try:
            counted[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return counted or None


def _artifact_hash(conn: sqlite3.Connection, artifact_id: int) -> str:
    """The SHA-256 of an artifact, read from its row (spec 10.5).

    An empty answer means the artifact row is gone, which is a fact a caller
    can see in the span rather than a hash this module would invent.
    """
    row = conn.execute("SELECT sha256 FROM artifacts WHERE id = ?", (int(artifact_id),)).fetchone()
    return "" if row is None else str(row["sha256"])


# ------------------------------------------------------------------ the check --


@dataclass(frozen=True)
class Verdict:
    """What the check says about one sentence."""

    sentence: str
    ok: bool
    code: str
    reason: str = ""
    handles: tuple[str, ...] = ()
    spans: tuple[Span, ...] = ()

    def sentence_of_its_own(self) -> str:
        """One plain sentence about this verdict."""
        if self.ok:
            return f"Grounded ({self.code})."
        return f"Not grounded ({self.code}): {self.reason}"


def check_sentence(sentence: str, pack: EvidencePack) -> Verdict:
    """Check one sentence against the evidence pack (spec 11.7).

    The rules, in the order they are asked:

    1. an empty sentence is not an answer;
    2. a sentence that is "Not found" (or one of the verdicts of spec 12.9)
       claims nothing, so it passes with no handle;
    3. the sentence must cite at least one handle, and every handle it cites
       must be one the pack holds;
    4. every quote must appear verbatim in the evidence the sentence cited,
       after folding whitespace and straightening quote marks;
    5. a counted vote must come from a stored vote, and must be that vote's
       count. A transcript mention is never a tally (spec 10.4);
    6. every other number must appear in the evidence the sentence cited;
    7. every person the sentence names must appear in the evidence the sentence
       cited, read from the roster the database holds (spec 10.6). A name the
       roster does not hold is not read as a person at all.
    """
    text = str(sentence).strip()
    if not text:
        return Verdict(sentence=text, ok=False, code=CODE_EMPTY, reason="The sentence is empty.")

    handles = handles_in(text)
    if is_not_found(text):
        return Verdict(sentence=text, ok=True, code=CODE_NOT_FOUND, handles=handles)

    if not pack.spans:
        return Verdict(
            sentence=text,
            ok=False,
            code=CODE_NO_EVIDENCE,
            reason="The evidence pack is empty, so no sentence can be grounded.",
            handles=handles,
        )
    if not handles:
        return Verdict(
            sentence=text,
            ok=False,
            code=CODE_NO_HANDLE,
            reason=(
                "It cites no handle. Every sentence must cite the evidence it rests on "
                f"(the pack holds {', '.join(pack.handles()[:4])} and more)."
            ),
        )

    cited: list[Span] = []
    for handle in handles:
        span = pack.get(handle)
        if span is None:
            return Verdict(
                sentence=text,
                ok=False,
                code=CODE_UNKNOWN_HANDLE,
                reason=f"The pack holds no evidence with the handle {handle}.",
                handles=handles,
            )
        cited.append(span)

    for quote in quotes_in(text):
        if not any(quote in normalize(span.text) for span in cited):
            return Verdict(
                sentence=text,
                ok=False,
                code=CODE_QUOTE,
                reason=f"The quote {quote!r} is not in the cited evidence, word for word.",
                handles=handles,
                spans=tuple(cited),
            )

    tallies = tallies_in(text)
    if tallies:
        # A stored vote is one that carries a count. A vote whose source is the
        # transcript has no count at all (spec 10.4), so citing it is citing a
        # mention rather than a vote, and the sentence is answered the same way
        # as one that cites no vote: a tally comes from a stored vote.
        votes = [span for span in cited if span.kind == KIND_VOTE and span.tally]
        if not votes:
            return Verdict(
                sentence=text,
                ok=False,
                code=CODE_TALLY,
                reason=(
                    "It states a counted vote and cites no stored vote. A tally comes from a "
                    "stored vote, and a mention in a transcript is never a tally (spec 10.4)."
                ),
                handles=handles,
                spans=tuple(cited),
            )
        for yes, no in tallies:
            if not any(_is_count(span.tally, yes, no) for span in votes):
                return Verdict(
                    sentence=text,
                    ok=False,
                    code=CODE_TALLY_MISMATCH,
                    reason=f"The count {yes}-{no} is not the count of the vote it cites.",
                    handles=handles,
                    spans=tuple(cited),
                )

    in_evidence = set()
    for span in cited:
        in_evidence |= _numbers_in(span.text)
        if span.tally:
            in_evidence |= {str(value) for value in span.tally.values()}
    for number in sorted(_numbers_claimed(text)):
        if number not in in_evidence:
            return Verdict(
                sentence=text,
                ok=False,
                code=CODE_NUMBER,
                reason=f"The number {number} is not in the cited evidence.",
                handles=handles,
                spans=tuple(cited),
            )

    named = _names_not_in_evidence(text, cited, pack.roster)
    if named:
        return Verdict(
            sentence=text,
            ok=False,
            code=CODE_NAME,
            reason=f"The sentence names {named[0]}, and the cited evidence does not.",
            handles=handles,
            spans=tuple(cited),
        )

    return Verdict(sentence=text, ok=True, code=CODE_OK, handles=handles, spans=tuple(cited))


def _names_not_in_evidence(sentence: str, cited: list[Span], roster: Roster) -> tuple[str, ...]:
    """The people this sentence names that its cited evidence never names.

    Two readings, both on the folded text and both by surname, because that is
    how a record prints a person beside a title (spec 10.6):

    * a surname the roster holds, printed anywhere in the sentence, is a claim
      about that person. The bare form ("McCoy moved it") and the titled forms
      ("Council Member McCoy", "Mayor Pro Tem McCoy") all print the surname, so
      the one reading answers all three;
    * a title the roster holds with one holder, printed with no surname after
      it ("the Mayor"), is a claim about the holder, and stands only if the
      cited spans print the holder's surname or the title itself. A title whose
      holder the roster does not know claims nobody, the same answer spec 10.6
      gives an unidentified speaker.

    A surname the roster does not hold is not read at all, and neither is a
    title it holds no holder for. The check cannot make a person out of a name
    it cannot place: the database is the only thing here that knows who the
    officials are, and reading every capitalized run as a person would fail
    honest sentences ("the City Manager", "the Consent Agenda") and remove
    them. The cost is on the record: a name invented out of nothing, unknown to
    the roster, is not caught here. Failing such a sentence would mean reading
    a name out of a shape rather than out of what the database knows, which is
    the guess this module refuses to make.

    The second reading is narrower than "a title appears in the sentence",
    because one held title can be printed inside a longer one: "Mayor" is a
    whole word of "Mayor Pro Tem". So the longest held title is read first and
    its words are spent before the shorter titles are looked for; and a title
    with a roster surname right after it is the titled form the surname reading
    above has answered already, not a claim on the seat. Both are what keeps
    "Mayor Pro Tem McCoy" from also being read as a claim about the Mayor.
    """
    said = normalize(without_handles(sentence)).casefold()
    evidence = " ".join(normalize(span.text) for span in cited).casefold()
    surnames = {official.surname.casefold() for official in roster.officials}
    faults: list[str] = []
    named: set[str] = set()
    for official in roster.officials:
        surname = official.surname.casefold()
        if not surname or not _says(said, surname):
            continue
        named.add(surname)
        if not _says(evidence, surname):
            faults.append(official.name)
    rest = said
    for title, holder in sorted(roster.holders.items(), key=lambda pair: -len(pair[0])):
        found = _whole_words(title).search(rest)
        if found is None:
            continue
        rest = _spent(rest, title)
        surname = holder.surname.casefold()
        if not surname or surname in named:
            continue
        if _followed_by_a_surname(rest[found.end() :], surnames):
            continue
        if not (_says(evidence, title) or _says(evidence, surname)):
            faults.append(holder.name)
    return tuple(dict.fromkeys(faults))


def _is_count(tally: Mapping[str, int] | None, yes: int, no: int) -> bool:
    """True when this stored tally is the count a sentence states."""
    if not tally:
        return False
    return int(tally.get("yes", -1)) == yes and int(tally.get("no", -1)) == no


def check_answer(answer: str, pack: EvidencePack) -> tuple[Verdict, ...]:
    """Check every sentence of an answer, in the order the answer wrote them."""
    return tuple(check_sentence(sentence, pack) for sentence in split_sentences(answer))


# ------------------------------------------------------------- the repair loop --


@dataclass(frozen=True)
class RepairRequest:
    """One failed sentence, and what a model is told about it."""

    sentence: str
    verdict: Verdict
    pack: EvidencePack

    def prompt(self) -> str:
        """The text a model is asked to repair the sentence from.

        The model is shown the evidence its sentence cited, word for word, so
        it can put a quote back the way the record prints it. When the sentence
        cited nothing the whole pack is shown, because a model that cannot see
        a handle cannot cite one.
        """
        shown = self.verdict.spans or self.pack.spans[:PACK_PROMPT_LIMIT]
        lines = [
            "One sentence did not pass the grounding check. Rewrite that sentence "
            "and nothing else.",
            "",
            "The sentence:",
            self.sentence,
            "",
            "Why it failed:",
            self.verdict.reason,
            "",
            "The evidence:",
        ]
        for span in shown:
            lines.append(f"{span.handle} ({span.cite()}) {normalize(span.text)}")
        left_out = len(self.pack.spans) - len(shown)
        if left_out > 0 and not self.verdict.spans:
            lines.append(f"... and {left_out} more spans, which are not shown here.")
        lines.extend(
            [
                "",
                "Rules for the rewrite:",
                "Cite at least one handle from the evidence above.",
                "Print every quote exactly as the evidence above prints it.",
                "Keep every number inside the evidence you cite.",
                "Do not change a quote, a number, a name or a URL.",
                "Answer with one sentence and nothing else.",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class RepairAnswer:
    """What a repair asked for came back with, or why it did not."""

    text: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.text.strip())


#: How a caller asks a model to repair one sentence. It returns the new
#: sentence, or an empty :class:`RepairAnswer` with the reason it is empty.
Ask = Callable[[RepairRequest], RepairAnswer]


@dataclass(frozen=True)
class Decision:
    """What happened to one sentence of an answer, and why."""

    sentence: str
    outcome: str
    code: str
    reason: str = ""
    repaired: str = ""

    def text(self) -> str:
        """The sentence as it stands after grounding."""
        return self.repaired if self.outcome == OUTCOME_REPAIRED else self.sentence

    def sentence_of_its_own(self) -> str:
        """One plain sentence about this decision."""
        if self.outcome == OUTCOME_REPAIRED:
            return f"Repaired ({self.code}) because {self.reason}"
        if self.outcome == OUTCOME_REMOVED:
            return f"Removed ({self.code}) because {self.reason}"
        return f"Kept ({self.code})."


@dataclass(frozen=True)
class Grounded:
    """An answer after grounding: what survived, and the counts either side.

    ``before`` is the measure of the answer as the model wrote it, ``after`` is
    the measure of what is left. They are the two halves of spec 11.7's "code
    measures first, the model rewrites second, code measures again".
    """

    decisions: tuple[Decision, ...] = ()
    #: The measure of the answer as the model wrote it.
    before: Mapping[str, int] = field(default_factory=dict)
    #: The measure of what is left.
    after: Mapping[str, int] = field(default_factory=dict)
    #: How many repairs were asked for.
    calls: int = 0

    def kept(self) -> tuple[str, ...]:
        """The sentences that survived, repaired ones included."""
        return tuple(
            decision.text() for decision in self.decisions if decision.outcome != OUTCOME_REMOVED
        )

    def removed(self) -> tuple[Decision, ...]:
        """The sentences that were removed, with why."""
        return tuple(d for d in self.decisions if d.outcome == OUTCOME_REMOVED)

    def answer(self) -> str:
        """What is left of the answer, as one piece of text."""
        return " ".join(self.kept())

    def sentence(self) -> str:
        """One plain sentence about the grounding run."""
        wrote = self.before.get("sentences", 0)
        passed = self.before.get("passed", 0)
        repaired = self.after.get("repaired", 0)
        removed = self.after.get("removed", 0)
        return (
            f"{wrote} sentence(s) were written, {passed} passed the check as written, "
            f"{repaired} were repaired in one round, and {removed} were removed."
        )


def ground(answer: str, pack: EvidencePack, *, ask: Ask | None = None) -> Grounded:
    """Measure an answer, repair what failed once, and measure it again.

    One repair round and no more: a sentence that fails the check is sent back
    once, and a sentence that still fails after that is removed. A repair that
    changes a quote, a number, a name or a URL is refused, and the sentence is
    removed unaltered (spec 11.7).

    ``ask`` is None when there is no model to ask, and then a failed sentence
    is removed with the reason it failed.
    """
    sentences = split_sentences(answer)
    decisions: list[Decision] = []
    passed = 0
    calls = 0
    for sentence in sentences:
        verdict = check_sentence(sentence, pack)
        if verdict.ok:
            passed += 1
            decisions.append(Decision(sentence=sentence, outcome=OUTCOME_KEPT, code=verdict.code))
            continue
        if ask is None:
            decisions.append(
                Decision(
                    sentence=sentence,
                    outcome=OUTCOME_REMOVED,
                    code=verdict.code,
                    reason=f"{verdict.reason} No repair was asked for.",
                )
            )
            continue
        calls += 1
        reply = ask(RepairRequest(sentence=sentence, verdict=verdict, pack=pack))
        repaired = reply.text.strip()
        if not repaired:
            decisions.append(
                Decision(
                    sentence=sentence,
                    outcome=OUTCOME_REMOVED,
                    code=CODE_NO_REPAIR,
                    reason=reply.reason or "The model did not answer the repair.",
                )
            )
            continue
        differences = changed_facts(facts(sentence), facts(repaired))
        if differences:
            decisions.append(
                Decision(
                    sentence=sentence,
                    outcome=OUTCOME_REMOVED,
                    code=CODE_REPAIR_CHANGED,
                    reason=(
                        f"The repair changed {', '.join(differences)}, and a repair never "
                        "changes a quote, a number, a name or a URL (spec 11.7)."
                    ),
                )
            )
            continue
        again = check_sentence(repaired, pack)
        if again.ok:
            decisions.append(
                Decision(
                    sentence=sentence,
                    outcome=OUTCOME_REPAIRED,
                    code=again.code,
                    reason=verdict.reason,
                    repaired=repaired,
                )
            )
        else:
            decisions.append(
                Decision(
                    sentence=sentence,
                    outcome=OUTCOME_REMOVED,
                    code=again.code,
                    reason=f"The one repair round still failed: {again.reason}",
                )
            )

    kept = sum(1 for decision in decisions if decision.outcome != OUTCOME_REMOVED)
    return Grounded(
        decisions=tuple(decisions),
        before={
            "sentences": len(sentences),
            "passed": passed,
            "failed": len(sentences) - passed,
        },
        after={
            "kept": kept,
            "repaired": sum(1 for d in decisions if d.outcome == OUTCOME_REPAIRED),
            "removed": len(decisions) - kept,
        },
        calls=calls,
    )


# ----------------------------------------------------- the repair a model does --


@dataclass
class LadderAsk:
    """The repair asked of a real model, through the preflight and the ladder.

    One repair is one task run: the ladder of spec 11.4 picks the provider, the
    preflight of spec 11.6 checks it before anything is sent, and the failover
    rules of spec 11.5 decide whether a failure moves down a rung. A provider
    the preflight refuses is reported as unavailable, which is what moves the
    ladder: an unreachable model is not an answer.

    ``model_call`` is the caller's own call, so this class never reaches a
    model itself (PROJECT-BRIEF rule 9). Every task run is kept in ``repairs``
    so a caller can read why a repair came back empty.
    """

    ladder: Ladder
    registry: ProviderRegistry
    model_call: Callable[[Provider, Any, str], Attempt]
    checks: Checks | None = None
    allowance: Allowance | None = None

    def __post_init__(self) -> None:
        #: Every repair asked for, in order.
        self.repairs: list[TaskRun] = []

    def __call__(self, request: RepairRequest) -> RepairAnswer:
        """Ask for one repaired sentence."""
        prompt = request.prompt()

        def call(provider: Provider, rung: Any) -> Attempt:
            ready = preflight(
                provider,
                task=self.ladder.task,
                checks=self.checks,
                allowance=self.allowance,
            )
            if not ready.ok:
                return Attempt(ok=False, reason=REASON_UNAVAILABLE, detail=ready.sentence)
            return self.model_call(provider, rung, prompt)

        run = run_task(ladder=self.ladder, registry=self.registry, call=call)
        self.repairs.append(run)
        if not run.ok:
            return RepairAnswer(reason=run.sentence)
        return RepairAnswer(text=run.output)
