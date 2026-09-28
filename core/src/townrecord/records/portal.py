"""What the three PrimeGov jobs share: the source, the client and the reading.

Section 9.2 says where a document comes from and what a citation names;
section 7.3 says what happens when a source stops answering. Both are here
because all three jobs need them.

Nothing in this package names a city, a channel or a body of its own. Every
origin is read from a configured source row, every body from the jurisdiction
that source belongs to (PROJECT-BRIEF rule D). A value that cannot be read is
reported with its plain reason rather than defaulted (rule F).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from ..adapters.base import (
    PACKET_LIMIT_BYTES,
    PDF_LIMIT_BYTES,
    AdapterError,
    Document,
)
from ..adapters.primegov import PrimeGovAdapter
from ..align.identifiers import find_identifiers
from ..config import Settings
from ..jobs import JobContext, enqueue
from ..repo import BROKEN_AFTER_FAILURES, Body, Source, bodies, get_body, get_source

#: The job kinds this package registers (spec 16.1). Their lanes are the ones
#: the brief gives them: a packet is 60 to 170 MB, so fetching one is heavy, and
#: opening one is slower still.
SYNC_PRIMEGOV = "sync_primegov"
DOWNLOAD_RECORD = "download_record"
EXTRACT_PAGES = "extract_pages"
ALIGN_MEETING = "align_meeting"
READ_MINUTES = "read_minutes"

#: The lanes, as spec 16.1 spells them.
NORMAL = "normal"
HEAVY = "heavy"

#: The source type a sync reads: a portal that lists meetings (spec 6.2).
MEETING_PORTAL = "meeting_portal"

#: The source type a watched channel has. A video row a channel captured is
#: that channel's row (spec 7.2 step 7).
VIDEO_CHANNEL = "video_channel"

#: A sync runs on a source the user accepted, or on one that went broken: a
#: source that could never be checked again could never recover (spec 7.3).
SYNCABLE_STATUSES = ("accepted", "broken")

#: The window a sync covers when its payload names none (spec 7.1 step 5).
DEFAULT_WINDOW_DAYS = 30

#: How many skipped meetings and documents a checkpoint keeps by name. A
#: summary that grows without bound is not a summary.
MAX_REPORTED_SKIPS = 50

#: The separators a portal title uses before the kind of sitting.
_TITLE_SEPARATORS = (" - ", ": ")

#: The words that name the kind of sitting rather than the body holding it.
#: A portal writes them spaced or hyphenated ("Pre-Session", "Pre Session") and
#: sometimes in front of the body rather than behind it, so every one of them
#: is read out of the title wherever it stands. The live sync stored a body
#: called "City Council Pre" from a "City Council Pre-Session" title before
#: this list was spelled out.
SITTING_WORDS = (
    "continued meeting",
    "executive session",
    "joint meeting",
    "joint session",
    "pre session",
    "regular session",
    "special meeting",
    "special session",
    "study session",
    "work session",
    "workshop",
    "hearings",
    "meeting",
    "session",
)

#: How a portal marks a cancelled meeting in a title.
_CANCELLED_WORDS = ("cancelled", "canceled")

#: The words that say how a sitting was held rather than which body held it.
#: A portal writes them in front of the name ("REMOTE Sustainability Advisory
#: Board"), behind it ("Airport Noise Improvement Project Community Meeting -
#: VIRTUAL") and as a marker of its own ("*Livestream*", "(livestreamed)").
#: None of them is part of a body's name wherever it stands.
_MODALITY_WORDS = (
    "in person",
    "livestreamed",
    "livestreaming",
    "livestream",
    "virtual",
    "remote",
)

#: The words a status tail opens with: a sitting that did not happen as it was
#: listed. Cancellation is the third one and has a signal of its own (spec
#: 9.5); these two are read by :func:`continuation_signal`. Like the sitting
#: words, they are what the meeting row keeps, not the body's name.
_CONTINUATION_WORDS = ("postponed", "continued")

#: Every word a body name cannot carry: the sitting words, the words that say
#: the sitting did not happen (spec 9.5) and the words that say how it was
#: held, longest first so that a phrase is read before the single word inside
#: it.
_BODY_WORDS = tuple(
    sorted(SITTING_WORDS + _CANCELLED_WORDS + _MODALITY_WORDS, key=len, reverse=True)
)

#: The words that name the type of sitting rather than a part of a body's name
#: when they stand first: "SPECIAL WATER BOARD MEETING" is a special sitting of
#: the Water Board, and the type is already read by :func:`meeting_type`. Only
#: a first word is taken: a type word inside a name is somebody's name.
_LEADING_TYPE_WORDS = (
    "executive",
    "special",
    "regular",
    "joint",
    "study",
    "work",
)

#: The words a portal uses for a gathering rather than a body. A title reads
#: "Annual Retreat - Historic Preservation Commission", where one side says
#: what kind of gathering it is and the other names the body holding it.
_GATHERING_WORDS = ("annual", "retreat", "day 1", "day 2")

#: The month names a portal names a sitting by ("Transportation Advisory Board
#: May Meeting"). A trailing one is a word about the sitting, not a part of the
#: body's name.
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

#: A date a portal hangs off the end of a name ("Longmont Urban Renewal
#: Authority (LURA) 5/5/26").
_TRAILING_DATE = re.compile(r"[\s(]*\d{1,2}/\d{1,2}/\d{2,4}\)?\s*$")

#: A month a portal hangs off the end of a name.
_TRAILING_MONTH = re.compile(rf"\s+({'|'.join(_MONTHS)})\s*$", re.IGNORECASE)

#: An acronym a portal writes in brackets after the name it repeats
#: ("Longmont Urban Renewal Authority (LURA)").
_TRAILING_ACRONYM = re.compile(r"\s*\(([A-Z]{2,8})\)\s*$")

#: What a marker leaves behind once its word has been read out of the name
#: ("Planning and Zoning Commission ()").
_EMPTY_BRACKETS = re.compile(r"\s*\(\s*\)\s*")

#: The dashes a title may join a sitting word with. They are read as spaces, so
#: that "Pre-Session" and "Pre Session" are one string to every reading here.
_DASHES = str.maketrans({character: " " for character in "‐‑‒–—―−-"})

#: The marks a title may put around a body name and that are not part of it.
_TITLE_MARKS = " .,-~*:;|/"

#: The words that name the kind of sitting, with the type spec 6.2 gives it.
#: All four types of the schema are reachable: a pre-session and a work session
#: are sittings where the body works through material rather than the ordinary
#: one, a joint sitting and a special one are both called special, and the
#: portal's own ``meetingTypeId`` is no help (one id covers four different
#: kinds of Water Board sitting in the recorded list).
_MEETING_TYPES = (
    ("executive", "executive"),
    ("study", "study"),
    ("work session", "study"),
    ("workshop", "study"),
    ("pre session", "special"),
    ("special", "special"),
    ("joint meeting", "special"),
    ("joint session", "special"),
    ("regular", "regular"),
)

#: The kind of the record a meeting's documents are published in. Spec 9.4: the
#: draft minutes of a session sit in the next session's packet, so this is the
#: kind of record a meeting's minutes are looked for in.
PACKET_KIND = "packet"

#: The record kinds migration ``0005_core_model.sql`` allows, with the words a
#: portal template name uses for them. Longest first: "Agenda Packet" is a
#: packet, and the adapter's own size rule reads "packet" the same way.
_TEMPLATE_KINDS = (
    ("minutes", "minutes"),
    ("ordinance", "ordinance"),
    ("resolution", "resolution"),
    ("packet", PACKET_KIND),
    ("budget", "budget"),
    ("report", "report"),
    ("agenda", "agenda"),
)

#: Spec 9.5: the document a clerk publishes to cancel a meeting.
NOTICE_OF_CANCELLATION = "notice of cancellation"

#: The hosts a meeting video is published on. A URL elsewhere has no id this
#: module can read, and guessing one would attach the wrong video.
_YOUTUBE_DOMAINS = ("youtube.com", "youtube-nocookie.com", "youtu.be")

#: A YouTube video id: eleven characters of the URL-safe alphabet.
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")

#: The YouTube path segments that carry a video id.
_YOUTUBE_PATH_KINDS = frozenset({"embed", "v", "shorts", "live"})

#: What to name a document's bytes. The artifact extension is a short lower
#: case token of letters and digits (``artifacts.EXT_PATTERN``).
_CONTENT_EXTENSIONS = {
    "application/pdf": "pdf",
    "text/html": "html",
    "text/plain": "txt",
    "application/json": "json",
    "application/zip": "zip",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

#: The extension of bytes whose content type is unknown. The bytes are kept as
#: they came and the type is not invented.
UNKNOWN_EXTENSION = "bin"

#: The one extension the reading job opens. A stored document with any other
#: one has no pages to read, and the job says so rather than guessing.
PDF_EXTENSION = "pdf"


class SyncRefused(Exception):
    """A job cannot do its work, with a plain reason a person can act on."""


@dataclass(frozen=True)
class SyncRequest:
    """One run of the sync: one source, and the window it covers."""

    source_id: int
    date_from: date
    date_to: date


@dataclass(frozen=True)
class DocumentJob:
    """One document a sync asked to be fetched (spec 9.2)."""

    source_id: int
    meeting_id: int
    document_id: int
    template_id: int
    compile_output_type: int
    template_name: str

    def as_payload(self) -> dict[str, Any]:
        """The JSON payload a download job carries."""
        return {
            "source_id": self.source_id,
            "meeting_id": self.meeting_id,
            "document_id": self.document_id,
            "template_id": self.template_id,
            "compile_output_type": self.compile_output_type,
            "template_name": self.template_name,
        }


@dataclass(frozen=True)
class PageJob:
    """One stored record to read page by page (spec 9.6)."""

    record_id: int

    def as_payload(self) -> dict[str, Any]:
        """The JSON payload a reading job carries."""
        return {"record_id": self.record_id}


@dataclass(frozen=True)
class MinutesRequest:
    """One meeting whose minutes are to be read (spec 9.4, 10.4)."""

    meeting_id: int

    def as_payload(self) -> dict[str, Any]:
        """The JSON payload a minutes reading job carries."""
        return {"meeting_id": self.meeting_id}


@dataclass(frozen=True)
class AlignRequest:
    """One meeting to align, and the portal source it came from."""

    meeting_id: int
    source_id: int | None = None

    def as_payload(self) -> dict[str, Any]:
        """The JSON payload an align job carries."""
        return {"meeting_id": self.meeting_id, "source_id": self.source_id}


# -- Runtime settings ---------------------------------------------------------


def open_client() -> httpx.Client:
    """Return the HTTP client the portal adapter uses.

    One place, so a test replaces it with a client built on an
    ``httpx.MockTransport`` and no job reaches the network (rule 7). The
    environment is not trusted here: this is one machine talking to one portal,
    and a proxy set for something else must not answer for it.
    """
    return httpx.Client(timeout=60.0, trust_env=False)


def settings() -> Settings:
    """Return the runtime settings of the process (spec 13.1)."""
    return Settings.from_env()


def size_limits(env: Mapping[str, str] | None = None) -> dict[str, int]:
    """Return the document size limits, from the environment when set.

    Spec 9.6 gives an HTML agenda 5 MB, a PDF 25 MB and a packet 200 MB. A
    machine that must keep packets small says so with
    ``TOWNRECORD_PACKET_LIMIT_BYTES``; a value that is not a number is refused
    rather than ignored, because a limit nobody applied is a limit nobody has.
    """
    source = os.environ if env is None else env
    return {
        "pdf_limit_bytes": _limit(source, "TOWNRECORD_PDF_LIMIT_BYTES", PDF_LIMIT_BYTES),
        "packet_limit_bytes": _limit(source, "TOWNRECORD_PACKET_LIMIT_BYTES", PACKET_LIMIT_BYTES),
    }


def _limit(env: Mapping[str, str], name: str, default: int) -> int:
    """Read one size limit, or the default when it is not set."""
    text = str(env.get(name, "") or "").strip()
    if not text:
        return default
    if not text.isdigit() or int(text) <= 0:
        raise SyncRefused(f"{name} is a whole number of bytes, not {text!r}.")
    return int(text)


def adapter_for(source: Source) -> PrimeGovAdapter:
    """Build the adapter of one configured source.

    The origin is the source's own. There is no other place a base URL could
    come from, which is what rule D asks for.
    """
    return PrimeGovAdapter(source.origin, open_client(), **size_limits())


# -- Payloads -----------------------------------------------------------------


def sync_request(payload: Any, *, today: date) -> SyncRequest:
    """Read the payload of a sync job (spec 7.1 step 5).

    The payload is the id of one source, or a mapping naming one and the window
    to cover. The window defaults to the last 30 days, and the end of a window
    before its start is refused rather than quietly swapped.
    """
    raw_source: Any = payload
    from_text = to_text = None
    if isinstance(payload, Mapping):
        raw_source = payload.get("source_id")
        from_text = payload.get("from_date")
        to_text = payload.get("to_date")
    source_id = _as_int(raw_source, "A sync job names the id of one source to sync.")
    first = _as_date(from_text, "The start of the window") if from_text else None
    last = _as_date(to_text, "The end of the window") if to_text else None
    if first is None and last is None:
        last = today
        first = today - timedelta(days=DEFAULT_WINDOW_DAYS)
    elif first is None:
        first = last - timedelta(days=DEFAULT_WINDOW_DAYS)
    elif last is None:
        last = today
    if last < first:
        raise SyncRefused(
            f"The window ends {last.isoformat()}, before it starts on {first.isoformat()}."
        )
    return SyncRequest(source_id=source_id, date_from=first, date_to=last)


def document_job(payload: Any) -> DocumentJob:
    """Read the payload of a download job (spec 9.2)."""
    if not isinstance(payload, Mapping):
        raise SyncRefused("A download job carries the meeting and the document to fetch.")
    return DocumentJob(
        source_id=_as_int(
            payload.get("source_id"), "A download job names the source it came from."
        ),
        meeting_id=_as_int(
            payload.get("meeting_id"), "A download job names the meeting it belongs to."
        ),
        document_id=_as_int(
            payload.get("document_id"), "A download job names the document to fetch."
        ),
        template_id=_as_int(
            payload.get("template_id"), "A download job names the document's template."
        ),
        compile_output_type=_as_int(
            payload.get("compile_output_type", 0),
            "A download job names the document's compile output type.",
            allow_zero=True,
        ),
        template_name=str(payload.get("template_name") or "").strip(),
    )


def page_job(payload: Any) -> PageJob:
    """Read the payload of a reading job (spec 9.6)."""
    if not isinstance(payload, Mapping):
        raise SyncRefused("A reading job carries the record to read.")
    return PageJob(
        record_id=_as_int(payload.get("record_id"), "A reading job names the record to read.")
    )


def align_request(payload: Any) -> AlignRequest:
    """Read the payload of an align job (spec 10.2)."""
    if not isinstance(payload, Mapping):
        raise SyncRefused("An align job carries the meeting to align.")
    raw_source = payload.get("source_id")
    return AlignRequest(
        meeting_id=_as_int(payload.get("meeting_id"), "An align job names the meeting to align."),
        source_id=(
            None
            if raw_source is None
            else _as_int(raw_source, "An align job names the source it came from.")
        ),
    )


def minutes_request(payload: Any) -> MinutesRequest:
    """Read the payload of a minutes reading job (spec 10.4)."""
    if not isinstance(payload, Mapping):
        raise SyncRefused("A minutes reading job carries the meeting whose minutes to read.")
    return MinutesRequest(
        meeting_id=_as_int(
            payload.get("meeting_id"),
            "A minutes reading job names the meeting whose minutes to read.",
        )
    )


def _as_int(value: Any, sentence: str, *, allow_zero: bool = False) -> int:
    """Read one whole number out of a payload, or refuse it by its sentence."""
    if isinstance(value, bool) or value is None:
        raise SyncRefused(sentence)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SyncRefused(sentence) from exc
    if number < 0 or (number == 0 and not allow_zero):
        raise SyncRefused(sentence)
    return number


def _as_date(value: Any, sentence: str) -> date:
    """Read one ISO date out of a payload, or refuse it by its sentence."""
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SyncRefused(f"{sentence} is an ISO date like 2026-09-08, not {value!r}.") from exc


# -- Reading a source ---------------------------------------------------------


def syncable_source(conn: sqlite3.Connection, source_id: int) -> Source:
    """Return the portal source to sync, or say plainly what stops it.

    A missing row, a source of another type, and a source the user has not
    accepted are three different refusals with three different sentences: the
    coordinator can act on each of them.
    """
    source = get_source(conn, source_id)
    if source is None:
        raise SyncRefused(f"There is no source {source_id}.")
    if source.type != MEETING_PORTAL:
        raise SyncRefused(
            f"Source {source.id} is a {source.type} source, which is not a meeting portal."
        )
    if source.status not in SYNCABLE_STATUSES:
        raise SyncRefused(
            f"Source {source.id} is {source.status}, so it is not synced. "
            f"A sync runs on a source that is accepted or broken."
        )
    return source


# -- Reading a listed meeting -------------------------------------------------


def body_name(title: str) -> str:
    """Return the name of the body a portal title names.

    A portal title reads "City Council Regular Session", "City Council
    Pre-Session" or "Golf Course Advisory Board - CANCELLED". The body is the
    part of the title that is not the sitting: the part before the first
    separator, or the part after one when the part before it says nothing but
    the sitting, with every sitting word and every cancellation word read out
    of it wherever it stands. A portal writes one sitting both ways round
    ("Pre-Session" in front, "CANCELLED - Parks and Recreation Advisory Board"
    behind), so the two are read as one title with the words taken out of it
    rather than as a word trimmed off an end.

    What a title says about a sitting rather than about a body is not part of
    the name, and it is kept where the meeting's own state lives instead: a
    status tail (POSTPONED, CONTINUED, CANCELLED) on the meeting row, a
    leading type word in the type, and a modality word nowhere at all, since
    how a sitting was held is not a name either. A date, a month and an
    acronym in brackets that only repeats the name are annotations the portal
    hangs on it, and they come off with them.

    A title that is nothing but a sitting or a cancellation ("CANCELLED") names
    no body this reading can find, and the tidied title is returned for the
    caller to report. It is deliberately not empty: an empty name is what a
    title that names no body at all looks like, and the caller answers that one
    with the source's own configured body, which would file this meeting under
    the wrong one. A title that opens with a modality word is the same case:
    what follows a marker such as "VIRTUAL -" is the event the marker is about
    and not a body ("VIRTUAL - Black Rock Coffee Notice of Neighborhood
    Meeting"), so the marker is reported and no body is invented for it.
    Anything else falls through as the title wrote it.
    """
    parts = _title_parts(title)
    if not parts:
        return ""
    if _says_only_how_it_was_held(parts[0]):
        return _tidy(parts[0])
    for part in parts:
        named = _body_of(part)
        if named:
            return named
    return _tidy(parts[0])


def _title_parts(title: str) -> list[str]:
    """The parts of a title around its first separator, head first."""
    text = (title or "").strip()
    if not text:
        return []
    for separator in _TITLE_SEPARATORS:
        if separator in text:
            head, tail = text.split(separator, 1)
            return [head.strip(), tail.strip()]
    return [text]


def _body_of(part: str) -> str:
    """One part of a title, read as the name of a body."""
    return _clean_name(_without_body_words(_without_leading_type(_without_status_tail(part))))


def _says_only_how_it_was_held(part: str) -> bool:
    """Whether a part says only how the sitting was held and where.

    "VIRTUAL" on its own is a marker rather than a body, and a title that
    opens with one names its body nowhere in front of it. The part is asked
    for a modality word as well as for reading as nothing, so that a title
    that is nothing but a cancellation ("Cancelled - Parks and Recreation
    Advisory Board") still goes on to the side that names its body.
    """
    if _body_of(part):
        return False
    folded = part.casefold().translate(_DASHES)
    return any(re.search(rf"\b{word}\b", folded) for word in _MODALITY_WORDS)


def _without_status_tail(text: str) -> str:
    """One part of a title with the status tail off it, where it has one.

    A portal writes the status of a sitting behind the name for it ("Water
    Board POSTPONED to March 23", "Local Licensing Authority- CONTINUED TO
    DATE TBD"), and the status is kept on the meeting row rather than in the
    name. What the tail says is read back off the title by
    :func:`continuation_signal` and :func:`cancellation_signal`, so nothing is
    lost with the tail.
    """
    folded = text.casefold().translate(_DASHES)
    at = len(folded)
    for word in _CONTINUATION_WORDS:
        start = 0
        while True:
            found = folded.find(word, start)
            if found < 0:
                break
            if _whole_word(folded, found, found + len(word)):
                at = min(at, found)
                break
            start = found + 1
    return text[:at].rstrip(_TITLE_MARKS)


def _without_leading_type(text: str) -> str:
    """One part of a title with a leading type word taken off it.

    A type word in front of a name says what kind of sitting it is and is read
    as the type already (:func:`meeting_type`), so "SPECIAL WATER BOARD
    MEETING" is a special sitting of the Water Board. A type word that is not
    the first word is left where it stands.
    """
    folded = text.casefold().translate(_DASHES)
    for word in _LEADING_TYPE_WORDS:
        if folded.startswith(word) and _whole_word(folded, 0, len(word)):
            return text[len(word) :]
    return text


def _clean_name(text: str) -> str:
    """A name with the annotations a portal hangs on it taken off.

    Three of them, and each is something the portal says about the sitting
    rather than a part of a body's name: a date ("5/5/26"), the month a
    sitting is named by ("Transportation Advisory Board May Meeting"), and an
    acronym in brackets that repeats the name in front of it ("Longmont Urban
    Renewal Authority (LURA)"). What a marker leaves behind once its word has
    been read out is tidied away with them.
    """
    text = _TRAILING_DATE.sub("", text)
    text = _TRAILING_MONTH.sub("", text)
    text = _without_repeating_acronym(text)
    text = _EMPTY_BRACKETS.sub(" ", text)
    return _tidy(text)


def _without_repeating_acronym(text: str) -> str:
    """A name with a bracketed acronym of itself taken off the end.

    "(LURA)" behind "Longmont Urban Renewal Authority" says nothing the name
    does not already say. An acronym of something else is left alone: it may
    be the one place the name is written in full.
    """
    match = _TRAILING_ACRONYM.search(text)
    if match is None:
        return text
    head = text[: match.start()]
    initials = "".join(word[0] for word in head.split() if word[:1].isalpha())
    if match.group(1).casefold() != initials.casefold():
        return text
    return head


def _without_body_words(text: str) -> str:
    """Return a part of a title with the sitting and cancellation words gone.

    The words are matched on a folded copy of the text, in which the dashes a
    title joins a sitting word with read as spaces and the case is not the
    title's, and they are matched whole: "session" comes out of "Pre-Session"
    and stays inside "Sessionalia". The folded copy is the same length as the
    text, so what comes back is the title as it was written, not lowercased.
    """
    folded = text.lower().translate(_DASHES)
    keep = [True] * len(folded)
    for word in _BODY_WORDS:
        start = 0
        while True:
            at = folded.find(word, start)
            if at < 0:
                break
            end = at + len(word)
            if all(keep[at:end]) and _whole_word(folded, at, end):
                keep[at:end] = [False] * (end - at)
            start = at + 1
    return _tidy("".join(character for index, character in enumerate(text) if keep[index]))


def _whole_word(folded: str, at: int, end: int) -> bool:
    """Whether the match at these offsets is a word of its own."""
    before = folded[at - 1] if at else " "
    after = folded[end] if end < len(folded) else " "
    return not before.isalnum() and not after.isalnum()


def _tidy(text: str) -> str:
    """A name with its runs of space collapsed and its marks of separation gone."""
    return re.sub(r"\s+", " ", text).strip(_TITLE_MARKS)


def meeting_type(title: str) -> str:
    """Return the meeting type a title names (spec 6.2).

    A title that names none is a regular session, which is the ordinary case:
    a portal lists an ordinary sitting without labelling it. The same folded
    reading as :func:`body_name` is used, so that a hyphenated sitting word
    names its type the same way it names its body.
    """
    lowered = (title or "").lower().translate(_DASHES)
    for needle, kind in _MEETING_TYPES:
        if needle in lowered:
            return kind
    return "regular"


def cancellation_signal(title: str, documents: Iterable[Document]) -> str | None:
    """Return which signal says a meeting is cancelled, or None (spec 9.5).

    Two signals, and either one is enough. A "Notice of Cancellation" is the
    document a clerk publishes to cancel a sitting, so it is the stronger one;
    a title that says cancelled is the other, which a portal sometimes uses
    with no notice at all. The sentence returned is the reason, so a reader can
    see which of the two it was.
    """
    lowered = (title or "").casefold()
    for word in _CANCELLED_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            return f"the listing's title says {word}"
    for document in documents:
        if NOTICE_OF_CANCELLATION in (document.template_name or "").casefold():
            return f"document {document.id} is a Notice of Cancellation"
    return None


def continuation_signal(title: str) -> str | None:
    """Return which continuation status a title says, or None (spec 6.2).

    A sitting a portal lists as POSTPONED or CONTINUED did not happen at the
    time it was listed for, which is what ``meetings.is_continued`` records.
    The sentence returned is the reason, so a reader can see which word it was
    read from: the status is kept on the meeting row, and this is what says
    why. A cancelled sitting has its own signal and its own column.
    """
    lowered = (title or "").casefold()
    for word in _CONTINUATION_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            return f"the listing's title says {word}"
    return None


def resolve_body(conn: sqlite3.Connection, source: Source, title: str) -> Body | None:
    """Return the body a listed meeting belongs to, or None.

    The name the title carries is matched against the bodies of the source's
    own jurisdiction, because that is the meeting's own name for its body. The
    two are compared as names rather than as strings: case, an ampersand and
    punctuation are folded away, since "Planning & Zoning Commission" and
    "Planning and Zoning Commission" are one body written two ways and a
    title that names it either way names it (:func:`_folded_name`).

    A title whose head names no body is given a second reading when a dash
    separates a gathering word from one: "Annual Retreat - Historic
    Preservation Commission" is a Commission sitting, so the side of the dash
    that is a body of the jurisdiction is the answer (:func:`_gathered_body`).
    A title that names no body the jurisdiction holds gives None, and the
    caller records the name it looked for: filing a Historic Preservation
    Commission meeting under City Council because the source was configured
    with one body would be a silent lie about a public meeting.

    The one case where the source's own body is used is a title that names no
    body at all, which is what ``sources.body_id`` is for.
    """
    held = bodies(conn, source.jurisdiction_id)
    wanted = body_name(title)
    if wanted:
        folded = _folded_name(wanted)
        for body in held:
            if _folded_name(body.name) == folded:
                return body
        return _gathered_body(held, title)
    if source.body_id is None:
        return None
    return get_body(conn, source.body_id)


def _folded_name(name: str) -> str:
    """A body's name with the ways of writing it folded away.

    Case, an ampersand and punctuation are how a portal spells a name, not
    which name it is: "Planning & Zoning Commission" is "Planning and Zoning
    Commission", and "MASTER BOARD OF APPEALS" is "Master Board of Appeals".
    Nothing is added to a name here and nothing is dropped from it, so two
    bodies whose names differ by a word stay two bodies.
    """
    folded = (name or "").casefold().replace("&", " and ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _gathered_body(held: Iterable[Body], title: str) -> Body | None:
    """The body on the far side of a gathering word in a title, or None.

    One side of the dash says what kind of gathering it is and the other names
    the body it is for, so what is looked for is the side that is a body of the
    jurisdiction while every other side is nothing but a gathering word. A
    title whose other side names no body of it is left as it reads, which is
    what keeps an event such as "Town Hall: What the City Can and Cannot Do
    About Airport Noise" reported as the title wrote it.
    """
    sides = _title_sides(title)
    if len(sides) < 2:
        return None
    for index, side in enumerate(sides):
        others = sides[:index] + sides[index + 1 :]
        if not all(_is_gathering(other) for other in others):
            continue
        folded = _folded_name(side)
        for body in held:
            if _folded_name(body.name) == folded:
                return body
    return None


def _title_sides(title: str) -> list[str]:
    """The sides of a title around every separator it has, in order."""
    text = (title or "").strip()
    for separator in _TITLE_SEPARATORS:
        text = text.replace(separator, "\n")
    return [side.strip() for side in text.split("\n") if side.strip()]


def _is_gathering(text: str) -> bool:
    """Whether a side of a dash names a gathering and no body.

    "Annual Retreat" is one. "City Council Retreat" is not, because a body's
    name stands in front of the gathering word. What is read is what is left
    once the gathering words are out of it: nothing left means nothing but the
    gathering.
    """
    folded = text.casefold().translate(_DASHES)
    for word in _GATHERING_WORDS:
        folded = re.sub(rf"\b{word}\b", " ", folded)
    return not folded.strip(_TITLE_MARKS + "()")


def record_kind(template_name: str) -> str | None:
    """Return the record kind a portal template name is, or None.

    A template the schema has no kind for is not downloaded, and the sync
    records why. "Notice of Cancellation" is the one that matters in practice:
    it is not a record of the meeting, it is the document that says the meeting
    did not happen (spec 9.5).
    """
    text = (template_name or "").strip().casefold()
    if not text:
        return None
    for needle, kind in _TEMPLATE_KINDS:
        if needle in text:
            return kind
    return None


def html_template_id(documents: Iterable[Document]) -> int | None:
    """The template id of the meeting's HTML agenda, if it lists one.

    compileOutputType 3 is the HTML agenda (spec 9.2), which is the only place
    the clerk's per item video times exist. The id is kept on the meeting so
    the align job can read the agenda again without the listing.
    """
    for document in documents:
        if document.compile_output_type == 3:
            return document.template_id
    return None


def identifiers_of(text: str) -> dict[str, str | None]:
    """Return the ordinance and resolution numbers of a title (spec 10.1).

    Keyed by the canonical spelling of each number, which is how a person
    cites it, with the kind the title gave or None when it gave none.
    """
    found: dict[str, str | None] = {}
    for identifier in find_identifiers(text):
        found.setdefault(identifier.canonical(), identifier.kind)
    return found


def video_id(url: str | None) -> str | None:
    """Return the YouTube video id a listing's videoUrl names, or None.

    A URL on another host has no id this module can read, so it gives None and
    the caller records the reason. An id is never guessed out of a URL whose
    shape is not one of the shapes YouTube publishes.
    """
    text = (url or "").strip()
    if not text:
        return None
    if "//" not in text:
        text = f"https://{text}"
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    host = parsed.netloc.casefold().split("@")[-1].split(":")[0]
    if not any(host == domain or host.endswith(f".{domain}") for domain in _YOUTUBE_DOMAINS):
        return None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = parsed.path.strip("/").split("/")[0]
    else:
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("v"):
            candidate = query["v"][0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            candidate = (
                parts[1] if len(parts) >= 2 and parts[0].casefold() in _YOUTUBE_PATH_KINDS else ""
            )
    candidate = candidate.strip()
    return candidate if _YOUTUBE_ID.fullmatch(candidate) else None


def content_extension(content_type: str | None) -> str:
    """Return the artifact extension for a content type (spec 8.6).

    A type this module does not know is stored as ``bin``: the bytes are kept
    as they came and the type is not invented. The extension is not the
    document's name and carries no meaning beyond what the bytes are.
    """
    text = (content_type or "").split(";")[0].strip().casefold()
    return _CONTENT_EXTENSIONS.get(text, UNKNOWN_EXTENSION)


# -- Queueing the work --------------------------------------------------------


def enqueue_once(ctx: JobContext, kind: str, payload: Any) -> int | None:
    """Queue a job of this kind once, and return its id, or None if one waits.

    A job of the same kind with the same payload that is queued or running
    already does this work, so a second one would fetch the same packet twice.
    A finished job blocks nothing: a re-sync of the same window after a download
    failed has to be able to try again, and a paused job that could not run yet
    does not block the attempt either.

    The new job carries the origin of the job that asked for it, so the children
    of a scheduled sync are scheduled too and the user's own runs stay apart
    from them (spec 16.2). It is not passed a lane: the registry knows the one
    this kind runs in.
    """
    text = json.dumps(payload)
    row = ctx.conn.execute(
        "SELECT id FROM jobs WHERE kind = ? AND payload = ? AND state IN ('queued', 'running') "
        "ORDER BY id LIMIT 1",
        (kind, text),
    ).fetchone()
    if row is not None:
        return None
    return enqueue(ctx.conn, kind, payload, origin=ctx.origin, clock=ctx.clock)


def adapter_problem(exc: AdapterError) -> str:
    """The plain sentence one failed portal request is recorded with.

    The type is kept, because "AdapterHttpError: ... answered with status 500"
    tells the coordinator more than the message alone does.
    """
    return f"{type(exc).__name__}: {exc}"


def failure_sentence(source: Source, reason: str, failures: int) -> str:
    """The plain sentence one failed listing is recorded with (spec 7.3).

    The count of failures in a row is what makes the source broken, so the
    sentence carries it, and the two readers of a source's health -- the portal
    sync and the channel watch -- say the same thing about it.
    """
    sentence = f"{reason} Source {source.id} has now failed {failures} time(s) in a row."
    if failures >= BROKEN_AFTER_FAILURES:
        return f"{sentence} It is broken."
    return sentence
