"""The votes out of the minutes, and out of the video when there are none yet.

Spec 9.4: the draft minutes of a regular session are not approved until the
next regular session, so they sit in the *next* session's packet under
"Approval of Minutes". Reading a meeting's votes therefore starts by finding
that run of pages in a packet of a later meeting, and the run is found by what
the pages print rather than by where they are: a heading that says MINUTES over
the body's name and this meeting's date, followed by pages whose draft numbers
count on from one another. A packet can hold two runs, as the September 22,
2026 packet of Longmont did (September 1 and September 8), so the date is what
tells them apart.

Four things about the reading are worth saying out loud, because they are what
the data did rather than what a form would want:

* one item can be moved on more than once. Item 9.B of the September 8, 2026
  minutes was moved on seven times, three of them withdrawn, before it was
  approved, and migration ``0011_motions.sql`` stores each motion as its own
  row. The ``votes`` row keeps the outcome, which is the last motion that was
  not withdrawn;
* the minutes repeat one consent-agenda motion verbatim under every consent
  item it covers. The copies are one motion, and they are stored once and
  linked to every item they cover;
* a motion's text runs on until the next thing the minutes print as a block
  starts, so a withdrawn motion that was followed by a paragraph of discussion
  carries that paragraph in its text. Nothing is invented to hide it;
* when the minutes are not there yet, the video is read instead (spec 10.4) and
  what it gives is a mention, never a count: a transcript vote has no tally,
  whatever the chair said.

The second and third of those are the reason the page of a motion is stored on
the motion and not on the vote: a citation of the September 8 minutes is a
citation of one page of the September 22 packet (spec 10.5).
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..align.identifiers import NUMBER_WORDS, find_identifiers, find_item_references
from ..jobs import JobContext
from ..repo import (
    Record,
    RecordPage,
    Segment,
    agenda_items,
    clear_minutes_reading,
    get_body,
    get_meeting,
    get_motion,
    insert_minutes_document,
    insert_motion,
    insert_motion_item,
    insert_record_citation,
    insert_video_citation,
    insert_vote,
    latest_transcript,
    meetings_of_body,
    primary_video,
    record_pages,
    records_of_meeting,
    segments_of,
    upsert_person,
    upsert_seat,
)
from . import portal
from .pages import own_footer, printed_footers
from .requests import request_speakers

#: The heading a draft minutes run starts under (spec 9.4).
MINUTES_HEADING = "MINUTES"

#: How many of the lines under the heading belong to the head of the document.
#: The heading, the body and the sitting, the date, and where it was held.
HEAD_LINES = 4

#: The plain words a vote read from the video is labelled with. Spec 10.4: a
#: transcript-only mention is never a tally, and the label says where it came
#: from and why that is all there is.
FROM_VIDEO = "from video; minutes not yet available"

#: The plain sentence the reading leaves when no minutes have been published
#: yet (spec 10.4). The part after it says which of the three things is true.
NOT_AVAILABLE = "not available: no official minutes document yet"

#: No later regular session is listed, so no packet can hold this meeting's
#: draft minutes yet (spec 9.4).
NO_LATER_SESSION = "no later regular session is listed yet"

#: A later regular session is listed, and its packet is what will hold the
#: draft of this meeting's minutes. The sentence is spec 10.4's own example,
#: so what a reader sees here is the words the spec asks for.
EXPECTED_IN = "expected in the {month_day} packet"

#: The later packet is stored and read, and holds no minutes of this meeting.
#: The packet was read: this is a finding, not a wait.
NO_RUN_FOUND = "the {month_day} packet holds no minutes of this meeting"

#: The words that say a motion was counted, with the tally the minutes printed
#: as an en dash (U+2013) between the two numbers.
_RESULT_LINE = re.compile(r"^(Carried|Failed)\s*:\s*(\d+)\s*[–—−-]\s*(\d+)\s*$", re.IGNORECASE)

#: A pair of numbers with no word in front of it. It is what a withdrawn motion
#: prints in place of a result ("0 – 0") and it is not a result.
_BARE_TALLY = re.compile(r"^\d+\s*[–—−-]\s*\d+\s*$")

#: The label a counted name list is printed under.
_LABELS = {"approved": "approved", "dissented": "dissented", "abstained": "abstained"}

#: The words the minutes print when a motion was taken off the table before it
#: was voted on. It is an outcome and not a result: nobody voted.
_WITHDRAWN = "MOTION WITHDRAWN"

#: The labels, and the block starters that end a motion. A mover line is its own
#: start, so the next "MOTION" is the marker that a following motion has begun.
_BLOCK_STARTERS = frozenset({"motion", _WITHDRAWN.casefold()})

#: A line that moves something: who moved, who seconded, and what it was to do.
#: The seconder is allowed to be empty, because a Longmont minute printed one
#: that way ("Crystal Prieto moved, seconded by , to amend").
_MOVER_LINE = re.compile(r"^(.+?)\s+moved,\s+seconded by\s*(.*?)\s*,\s*(to\s+.+)$", re.IGNORECASE)

#: A motion whose text says it was to table the item, and which carried.
_TO_TABLE = re.compile(r"^to\s+table\s", re.IGNORECASE)

#: The words a chair uses to say what the vote on a motion was. The video is
#: read for a mention, not for a count, so this is only ever a result.
_SPOKEN_OUTCOME = re.compile(
    r"\b(carr(?:ies|ied)|unanimous|fails?|failed|does not carry|denied)\b", re.IGNORECASE
)

#: What makes a spoken line a motion at all.
_SPOKEN_MOTION = re.compile(r"\b(motion|moved|seconded)\b", re.IGNORECASE)

#: How far past a motion line the chair's outcome is looked for, and how far
#: around a motion line an identifier is looked for. A chair says "a motion to
#: pass and" and names the ordinance in the next line, so the window reaches
#: past the trigger line.
SPOKEN_OUTCOME_LINES = 24
SPOKEN_WINDOW_LINES = 2

#: The number of segments a spoken motion line is built from at the most.
SPOKEN_LINE_LINES = 3

#: Four digits in a line are what tells a running head from a line of the
#: minutes: every page of a packet prints the same head, which carries the date.
_YEAR = re.compile(r"\b\d{4}\b")

#: A line of the minutes that is a footer, either the packet's own or the one
#: the draft printed (spec 9.4, 9.6).
_PAGE_LINE = re.compile(r"^page\s+\d+\s*$", re.IGNORECASE)

#: The suffix of an ordinal that a wrapped line broke off ("26" then "th").
_ORDINAL_SUFFIX = re.compile(r"^(st|nd|rd|th)$", re.IGNORECASE)

#: A numbered item, as an agenda's number is spelled ("9.", "9.B", "10.A.1").
_ITEM_NUMBER = re.compile(r"^\d+(?:[A-Za-z]|\.[A-Za-z0-9]+)*$")

#: The words that say a motion took items out of the consent agenda. "with the
#: exception of" is written before "except" because "except" alone does not
#: match inside "exception", and a chair who says "with the exception of items
#: 9A and 9B" means what a minute that prints "except items 9A and 9B" means.
#: "minus" is here because the September 8, 2026 chair used it ("a motion to
#: approve the consent agenda? Um, minus items B, E, B, and E"), and a reading
#: that does not know the word approves the very items he took out.
_EXCEPTION = re.compile(
    r"\b(?:with\s+the\s+exception\s+of|excepting|except|excluding|pulling|pulled|minus)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MinutesRun:
    """The run of pages one meeting's draft minutes were read out of."""

    record_id: int
    start_page: int
    end_page: int
    head: str
    pages: tuple[RecordPage, ...]

    @property
    def page_count(self) -> int:
        """How many pages of the packet the run covers."""
        return self.end_page - self.start_page + 1


@dataclass(frozen=True)
class MotionRead:
    """One motion as the minutes print it, before anything is stored."""

    page_number: int
    ordinal: int
    mover: str
    seconder: str
    text: str
    result: str
    outcome: str
    approved: tuple[str, ...] = ()
    dissented: tuple[str, ...] = ()
    abstained: tuple[str, ...] = ()
    tally: dict[str, int] | None = None
    evidence: str = ""
    links: tuple[ItemLink, ...] = ()

    def as_printed(self) -> tuple[object, ...]:
        """The whole motion, for telling one printed motion from another.

        The minutes repeat their consent motion word for word under every item
        it covers. Two motions that agree on all of this are that print of the
        motion over again, and the second is not a second motion.
        """
        return (
            self.mover,
            self.seconder,
            self.text,
            self.result,
            self.outcome,
            self.approved,
            self.dissented,
            self.abstained,
            None if self.tally is None else tuple(sorted(self.tally.items())),
        )


@dataclass(frozen=True)
class StoredMotion:
    """A motion that was stored, with the item ids it was stored against."""

    motion: MotionRead
    motion_id: int
    links: tuple[ItemLink, ...] = ()

    def decided_on(self, agenda_item_id: int) -> bool:
        """Whether this motion is a motion on an item and was actually decided.

        A withdrawn motion was moved on the item and nothing came of it, so it
        is a motion of the item and not an outcome of it.
        """
        if self.motion.result == "withdrawn":
            return False
        return any(link.agenda_item_id == agenda_item_id for link in self.links)


@dataclass(frozen=True)
class ItemRef:
    """An agenda item as the linking reads it, with the numbers it is known by."""

    id: int
    number: str
    title: str
    identifiers: dict[str, str | None]


@dataclass(frozen=True)
class ItemLink:
    """One item a motion was a motion on, and what the link rests on."""

    agenda_item_id: int
    link_kind: str
    evidence: str


@dataclass(frozen=True)
class ConsentCoverage:
    """What a consent-agenda motion covers, and what it took out of itself.

    ``covered`` is every item of the consent agenda the motion is a motion on,
    and ``excepted`` is every item it named as taken out. The two are disjoint,
    and an item in ``excepted`` is an item whose own motion has to be found
    somewhere else (spec 10.4).

    ``unreadable`` says the motion named exceptions and this reading could not
    read one of them as an item number. Such a motion covers nothing: what it
    covers is exactly what is not known about it, and a vote stored for an item
    it might have taken out would be a false fact (spec 16.3).
    """

    covered: tuple[ItemRef, ...] = ()
    excepted: tuple[ItemRef, ...] = ()
    unreadable: bool = False


@dataclass(frozen=True)
class SpokenVote:
    """A vote mentioned in the video, with the lines that mention it.

    There is no tally and there is no field to put one in: what a person said
    is not a count (spec 10.4).
    """

    agenda_item_id: int
    result: str
    start_ms: int
    end_ms: int
    evidence: str


@dataclass(frozen=True)
class SpokenReading:
    """What the video said about a meeting's votes, and what it did not say.

    ``votes`` is one mention per item, the first the video makes. ``taken_out``
    is the items a consent-agenda motion took out of itself that the video
    never came back to: they get no vote, and they are named here so the
    meeting's own note can say which items have no vote yet and why, rather
    than the reading inventing one for them (spec 10.4, 16.3).
    """

    votes: tuple[SpokenVote, ...] = ()
    taken_out: tuple[ItemRef, ...] = ()
    unreadable_consent: bool = False


def find_run(pages: Sequence[RecordPage], *, body_name: str, on: date) -> MinutesRun | None:
    """Find the run of pages holding this body's draft minutes of one date.

    None means these pages hold no such run, which is a finding about the pages
    and not a statement about the world: the same minutes may be in a packet
    this reading has not been given (spec 9.4).
    """
    wanted = _date_pattern(on)
    for index, page in enumerate(pages):
        lines = page_lines(page.text)
        if not _is_head(lines, body_name=body_name, wanted=wanted):
            continue
        run = [page]
        draft = _draft_number(page)
        for following in pages[index + 1 :]:
            step = _draft_number(following)
            if draft is None or step is None or step != draft + 1:
                break
            run.append(following)
            draft = step
        return MinutesRun(
            record_id=page.record_id,
            start_page=page.page_number,
            end_page=run[-1].page_number,
            head=_head_of(lines),
            pages=tuple(run),
        )
    return None


def parse_motions(run: MinutesRun) -> list[MotionRead]:
    """Read every motion a minutes run records, in the order they were moved.

    The ordinals count every printed motion, so a run read twice gives the same
    ordinals. A motion whose text is the text of an earlier one is dropped only
    when the caller stores them: this function reports what the pages say.
    """
    headings = _repeated_headings(run)
    lines: list[tuple[int, str]] = []
    for page in run.pages:
        for line in page_lines(page.text):
            if line in headings:
                continue
            lines.append((page.page_number, line))

    motions: list[MotionRead] = []
    index = 0
    while index < len(lines):
        page_number, line = lines[index]
        moved = _MOVER_LINE.match(line)
        if moved is None:
            index += 1
            continue
        text, stop = _motion_text(lines, index)
        outcome = _read_outcome(lines, stop)
        result = outcome.result
        if result == "passed" and _TO_TABLE.match(text):
            result = "tabled"
        motions.append(
            MotionRead(
                page_number=page_number,
                ordinal=len(motions) + 1,
                mover=moved.group(1).strip(),
                seconder=moved.group(2).strip(),
                text=text,
                result=result,
                outcome=outcome.line,
                approved=outcome.approved,
                dissented=outcome.dissented,
                abstained=outcome.abstained,
                tally=outcome.tally,
                evidence=_evidence(lines, index, outcome.stop),
            )
        )
        index = outcome.stop if outcome.stop > index else index + 1
    return motions


def items_of(conn: sqlite3.Connection, meeting_id: int) -> list[ItemRef]:
    """The agenda items of a meeting, with the numbers each one is known by.

    The stored ``identifiers`` column is the first place a number is looked for
    and the title is the second. Both are read because the column is empty for
    every item a database built before the column was filled kept, and a title
    that names its ordinance is not a title this reading should ignore.
    """
    refs: list[ItemRef] = []
    for item in agenda_items(conn, meeting_id):
        known: dict[str, str | None] = dict(item.identifiers)
        for canonical, kind in portal.identifiers_of(item.title).items():
            known.setdefault(canonical, kind)
        refs.append(ItemRef(id=item.id, number=item.number, title=item.title, identifiers=known))
    return refs


def links_for(motion: MotionRead, items: Sequence[ItemRef]) -> tuple[ItemLink, ...]:
    """The items a motion is a motion on, and what each link rests on.

    Three readings, in this order, each of which is the whole answer when it
    works:

    1. a consent-agenda motion covers every item of the consent agenda except
       the ones it names. This is read first because the clause it names them in
       ("except items 9B and 9E") reads as a reference to those items, and
       linking the motion to the items it excepts would be exactly backwards;
    2. an ordinance or resolution number the motion names, matched against the
       number the item is known by;
    3. an item number the motion names ("item 10.A").

    A consent-agenda motion's coverage is the whole answer and not a first
    reading: when it names exceptions this reading cannot read, the readings
    below would link it to the very items it took out, so it is linked to
    nothing instead. A motion no reading matches is linked to nothing, and the
    reading says so rather than attaching it to the nearest item.
    """
    consent = consent_links(motion.text, items)
    if consent is not None:
        return consent

    found: dict[str, ItemLink] = {}
    for identifier in find_identifiers(motion.text):
        canonical = identifier.canonical()
        for item in items:
            if canonical not in item.identifiers:
                continue
            found.setdefault(
                item.number,
                ItemLink(item.id, "identifier", canonical),
            )
    if found:
        return tuple(found.values())

    numbered: dict[str, ItemLink] = {}
    for reference in find_item_references(motion.text):
        for item in items:
            if not reference.matches(_parts(item.number)):
                continue
            numbered.setdefault(
                item.number,
                ItemLink(item.id, "item_number", reference.text.strip()),
            )
    return tuple(numbered.values())


def spoken_votes(segments: Sequence[Segment], items: Sequence[ItemRef]) -> list[SpokenVote]:
    """Read the votes the video mentions, one per item (spec 10.4).

    A chair asks for a motion and then says what it did, so the two lines are
    read from the transcript's own segments: the line that asks, and the line
    that says it carried. What comes back is a mention with no tally, because
    "carries unanimously" is not a count of anything (spec 10.4).
    """
    return list(spoken_reading(segments, items).votes)


def spoken_reading(segments: Sequence[Segment], items: Sequence[ItemRef]) -> SpokenReading:
    """Read what the video said about the items' votes, and what it left out.

    A spoken motion is read for the items it covers, and nothing else is:
    a consent-agenda motion is read with the same code the minutes are read
    with, so the items it takes out get no vote from it, and a chair's later
    motion on one of them is that item's own vote (spec 10.4). The items it
    took out and never came back to are reported rather than given a vote.

    The first mention of an item wins. A meeting reconsiders things, and a
    later mention of the same item is a second thing the video said rather than
    a second vote to store under one source kind.
    """
    votes: list[SpokenVote] = []
    spoken: set[int] = set()
    taken_out: dict[int, ItemRef] = {}
    unreadable = False
    for index, segment in enumerate(segments):
        if not _SPOKEN_MOTION.search(segment.text):
            continue
        motion_line, last = _spoken_line(segments, index)
        coverage = consent_coverage(motion_line, items)
        if coverage is None:
            item = _item_spoken_of(motion_line, items)
            covered = () if item is None else (item,)
        else:
            covered = coverage.covered
            unreadable = unreadable or coverage.unreadable
            for item in coverage.excepted:
                taken_out.setdefault(item.id, item)
        if not covered:
            continue
        outcome, line_index = _spoken_outcome(segments, last + 1)
        for item in covered:
            if item.id in spoken:
                continue
            spoken.add(item.id)
            taken_out.pop(item.id, None)
            votes.append(
                SpokenVote(
                    agenda_item_id=item.id,
                    result=_spoken_result(outcome),
                    start_ms=segments[index].start_ms,
                    end_ms=(
                        segments[line_index].end_ms if line_index is not None else segment.end_ms
                    ),
                    evidence=_spoken_evidence(motion_line, outcome),
                )
            )
    return SpokenReading(
        votes=tuple(votes),
        taken_out=tuple(taken_out.values()),
        unreadable_consent=unreadable,
    )


def read_minutes(ctx: JobContext) -> None:
    """Read one meeting's votes out of its minutes, or out of its video.

    The minutes are looked for in the packet of the next regular session of the
    same body, which is where spec 9.4 puts them, and the reading says which of
    the three ways there was nothing to read applies when it finds none. It
    does not write a ``minutes_documents`` row for a meeting whose minutes it
    did not find: the fact that they were searched for and not found is on this
    job's own row, and a row saying where minutes are that are not there would
    be a false citation.

    Reading is idempotent. Each reading clears only its own source kind before
    it writes, so a re-run writes one set of rows and a reading of one kind
    leaves a vote of the other kind alone. The video is read only when the
    minutes are missing, and a transcript vote written then is kept when the
    minutes arrive, because spec 10.4 shows both sources when they disagree
    rather than picking one.
    """
    request = portal.minutes_request(ctx.payload)
    meeting = get_meeting(ctx.conn, request.meeting_id)
    if meeting is None:
        ctx.pause(f"There is no meeting {request.meeting_id} to read the minutes of.")
        return

    body = get_body(ctx.conn, meeting.body_id)
    body_name = "" if body is None else body.name
    held = _meeting_date(meeting.starts_at)
    following = _next_regular(ctx.conn, meeting)
    packet = _packet_of(ctx.conn, following) if following is not None else None
    run = None
    if packet is not None:
        pages = record_pages(ctx.conn, packet.id)
        if pages:
            run = find_run(pages, body_name=body_name, on=held)

    if run is None:
        _read_from_video(ctx, meeting, _missing(ctx, following, packet, held))
        return

    if packet.source_id is None:
        ctx.pause(
            f"The minutes of meeting {meeting.id} are in record {packet.id}, which names no "
            "source, so no page of it can be cited."
        )
        return

    # Only this reading's own kind is cleared. A reading that ran before the
    # packet was stored left transcript votes, and spec 10.4 keeps both sources
    # when they disagree rather than picking one, so they stay.
    clear_minutes_reading(ctx.conn, meeting.id, source_kinds=("minutes",))
    motions = _store_motions(ctx, meeting.id, packet, run)
    votes = _store_votes(ctx, motions, items_of(ctx.conn, meeting.id))
    seats = _store_seats(ctx, meeting, run, motions)
    ctx.save_checkpoint(
        {
            "meeting_id": meeting.id,
            "record_id": packet.id,
            "found": True,
            "start_page": run.start_page,
            "end_page": run.end_page,
            "page_count": run.page_count,
            "motions": len(motions),
            "votes": votes,
            "seats": seats,
        }
    )
    # Spec 10.6: a meeting whose transcript is stored and whose seats are read
    # is a meeting whose speakers can be read. Asking is safe to repeat, and a
    # meeting whose video has no transcript yet is not asked for: the ask is
    # what the capture lane repeats once a transcript of that video is stored.
    request_speakers(ctx.conn, meeting.id)


#: The office a seat is read as when the minutes print no title in front of the
#: name. A council member is the ordinary case, and the minutes name the mayor
#: and the mayor pro tem where they are one.
COUNCIL_MEMBER = "Council Member"

#: How many words of a name a printed title is read with. Three covers "Susie
#: Hidalgo-Fahring" and stops a title from swallowing the rest of a sentence.
_TITLE_NAME_WORDS = 3

#: The offices the minutes print in front of a name, most specific first, with
#: the spelling this project gives each one and the rank that decides which
#: title wins when the pages print a name under two of them. A name printed as
#: "Mayor Pro Tem" is not the mayor, so the longer title outranks the shorter
#: one even though the pages may print both.
_PRINTED_TITLES = (
    ("mayor pro tem", "Mayor Pro Tem", 2),
    ("mayor", "Mayor", 3),
)

#: A title in front of a name as the minutes print it. The alternation puts the
#: longest title first for the same reason the list above does: "Mayor Pro Tem
#: McCoy" begins with the words of "Mayor McCoy" and is not that title.
_TITLE_BEFORE = re.compile(
    r"\b(?P<title>mayor\s+pro\s+tem|mayor|council\s+member|councilmember|councilman|councilwoman)"
    rf"\s+(?P<name>[A-Za-z][\w'’.\-]*(?:\s+[A-Za-z][\w'’.\-]*){{0,{_TITLE_NAME_WORDS - 1}}})",
    re.IGNORECASE,
)


def _store_seats(
    ctx: JobContext, meeting: Any, run: MinutesRun, motions: Sequence[StoredMotion]
) -> int:
    """Write the people and the seats a meeting's minutes name (spec 10.6).

    The names come from the vote lists, and from nowhere else: a person is
    written because the minutes of a meeting recorded their vote on it, so a
    caption file that misspells a name can never create a person. Rule D holds
    here as everywhere: the names are read out of the records, and nothing in
    this file knows the name of any city's council.

    The title is the most specific one the pages print in front of the name,
    read from the same pages the motions came from. "Council Member" is the
    default because it is what the lists are headed by, and a name the minutes
    print a title in front of gets that title instead.

    Returns how many names were written.
    """
    lines = [line for page in run.pages for line in page_lines(page.text)]
    names: list[str] = []
    for stored in motions:
        for column in (stored.motion.approved, stored.motion.dissented, stored.motion.abstained):
            names.extend(column)
    ordered = list(dict.fromkeys(name for name in names if name))
    if not ordered:
        return 0

    titles = _printed_titles(lines, ordered)
    on = _meeting_date(meeting.starts_at).isoformat()
    for name in ordered:
        person_id = upsert_person(ctx.conn, name=name)
        upsert_seat(
            ctx.conn,
            person_id=person_id,
            body_id=meeting.body_id,
            title=titles.get(name, COUNCIL_MEMBER),
            on=on,
        )
    return len(ordered)


def _printed_titles(lines: Sequence[str], names: Sequence[str]) -> dict[str, str]:
    """The most specific title the minutes print in front of each name.

    Only a title in front of the name itself counts. A heading that names
    nobody is a title for nobody, and the roll call's "Council Members" heads a
    list rather than naming a person, so the printed "Mayor Susie
    Hidalgo-Fahring" and "Mayor Pro Tem McCoy" are the two titles a council
    member can be lifted out of the default by.

    A name is matched by its first word after the title or by the words of the
    name itself, because the pages print "Mayor Susie Hidalgo-Fahring" and
    "Mayor Pro Tem McCoy": the first names one person in full and the second
    names another by surname.
    """
    printed_titles = {
        spelled: (name_of_title, rank) for spelled, name_of_title, rank in _PRINTED_TITLES
    }
    best: dict[str, tuple[int, str]] = {}
    for line in lines:
        for match in _TITLE_BEFORE.finditer(line):
            printed = " ".join(match.group("title").casefold().split())
            words = match.group("name").split()
            if not words or printed not in printed_titles:
                continue
            title, rank = printed_titles[printed]
            for name in names:
                if not _is_printed_name(name, words):
                    continue
                if name not in best or best[name][0] < rank:
                    best[name] = (rank, title)
    return {name: title for name, (_, title) in best.items()}


def _is_printed_name(name: str, words: Sequence[str]) -> bool:
    """Whether a name is the one a title was printed in front of.

    The surname is the test, because that is what the pages repeat: a line that
    prints "Council Member Kalkhofer noted" names the person the vote list
    prints in full. The whole name is a test too, for a title in front of a
    name whose first word is a first name, as "Mayor Susie Hidalgo-Fahring" is.
    """
    written = [word.strip(".,;:").casefold() for word in words]
    known = [word.casefold() for word in name.split()]
    if not written or not known:
        return False
    if written[0] == known[-1]:
        return True
    return " ".join(written[: len(known)]) == " ".join(known)


def _store_motions(
    ctx: JobContext, meeting_id: int, packet: Record, run: MinutesRun
) -> list[StoredMotion]:
    """Store the motions of a run, one row each, and return them.

    A motion the minutes printed more than once, word for word, is stored once.
    The ``votes`` table holds one row per item and source kind (spec 10.4), so
    the prints of one consent-agenda motion are one motion that is linked to
    every item it covers, which is what ``motion_items`` is for. The ordinals of
    the prints that were dropped are not reused, so the ordinal of a motion is
    the place it was moved in and a gap in the ordinals is a repeat.
    """
    items = items_of(ctx.conn, meeting_id)
    insert_minutes_document(
        ctx.conn,
        meeting_id=meeting_id,
        record_id=packet.id,
        start_page=run.start_page,
        end_page=run.end_page,
        head=run.head,
    )
    stored: list[StoredMotion] = []
    seen: set[tuple[object, ...]] = set()
    for motion in parse_motions(run):
        if motion.as_printed() in seen:
            continue
        seen.add(motion.as_printed())
        citation_id = insert_record_citation(
            ctx.conn,
            record_id=packet.id,
            source_id=int(packet.source_id),
            artifact_id=packet.artifact_id,
            excerpt=motion.evidence,
            page_number=motion.page_number,
        )
        motion_id = insert_motion(
            ctx.conn,
            meeting_id=meeting_id,
            record_id=packet.id,
            page_number=motion.page_number,
            ordinal=motion.ordinal,
            mover=motion.mover,
            seconder=motion.seconder,
            text=motion.text,
            result=motion.result,
            outcome=motion.outcome,
            evidence=motion.evidence,
            approved=motion.approved,
            dissented=motion.dissented,
            abstained=motion.abstained,
            tally=motion.tally,
            citation_id=citation_id,
        )
        links = links_for(motion, items)
        for link in links:
            insert_motion_item(
                ctx.conn,
                motion_id=motion_id,
                agenda_item_id=link.agenda_item_id,
                link_kind=link.link_kind,
                evidence=link.evidence,
            )
        stored.append(StoredMotion(motion=motion, motion_id=motion_id, links=links))
    return stored


def _store_votes(ctx: JobContext, stored: Sequence[StoredMotion], items: Sequence[ItemRef]) -> int:
    """Write one minutes vote per item, from the last motion that was decided.

    An item whose every motion was withdrawn gets no vote: nothing was decided.
    An item that was moved on is not the same as an item that was voted on, and
    a vote with no outcome would be a row saying something happened that did
    not.

    The vote names the motion it came out of and that motion's citation, so a
    reader who sees one outcome per item can walk back to the motion, and to the
    page of the packet it was read from (spec 10.5).
    """
    written = 0
    for item in items:
        decided = [entry for entry in stored if entry.decided_on(item.id)]
        if not decided:
            continue
        entry = decided[-1]
        motion = get_motion(ctx.conn, entry.motion_id)
        insert_vote(
            ctx.conn,
            agenda_item_id=item.id,
            result=entry.motion.result,
            source_kind="minutes",
            evidence=entry.motion.evidence,
            tally=entry.motion.tally,
            motion_id=entry.motion_id,
            citation_id=None if motion is None else motion.citation_id,
        )
        written += 1
    return written


def _read_from_video(ctx: JobContext, meeting: Any, sentence: str) -> None:
    """Write what the video mentions, then pause with the plain sentence.

    Pausing is the honest end of this: the minutes are not there yet and a job
    that waits says so. Something has to ask for the reading again when the
    packet is stored (spec 16.3), and ``request_minutes`` is what does.
    """
    clear_minutes_reading(ctx.conn, meeting.id, source_kinds=("transcript",))
    written = 0
    reading = SpokenReading()
    video = primary_video(ctx.conn, meeting.id)
    if video is not None:
        transcript = latest_transcript(ctx.conn, video.id)
        if transcript is not None:
            items = items_of(ctx.conn, meeting.id)
            reading = spoken_reading(segments_of(ctx.conn, transcript.id), items)
            for vote in reading.votes:
                citation_id = insert_video_citation(
                    ctx.conn,
                    video_id=video.id,
                    transcript_id=transcript.id,
                    artifact_id=transcript.artifact_id,
                    excerpt=vote.evidence,
                    start_ms=vote.start_ms,
                    end_ms=vote.end_ms,
                )
                insert_vote(
                    ctx.conn,
                    agenda_item_id=vote.agenda_item_id,
                    result=vote.result,
                    source_kind="transcript",
                    evidence=vote.evidence,
                    tally=None,
                    citation_id=citation_id,
                )
                written += 1
    # The sentence already ends in a full stop, and a reason that ends "yet.."
    # reads as a mistake rather than as the state of the reading.
    reason = f"The minutes of meeting {meeting.id} are {sentence.rstrip('.')}."
    if written:
        reason += f" {written} vote(s) were read from the video instead."
    if reading.taken_out:
        # An item taken out of the consent agenda is voted on by its own motion
        # or not at all, so an item with no vote yet is named here with the
        # reason rather than left to look like an item nobody read (spec 16.3).
        numbers = ", ".join(item.number for item in reading.taken_out)
        reason += (
            f" No motion of their own was found for {numbers}, which the consent agenda"
            " motion took out, so they have no vote from the video yet."
        )
    if reading.unreadable_consent:
        reason += (
            " A consent-agenda motion named exceptions the reading could not read as"
            " item numbers, so it was read as a motion on no item at all."
        )
    ctx.pause(reason)


def _missing(
    ctx: JobContext,
    following: Any,
    packet: Record | None,
    held: date,
) -> str:
    """Which of the three ways there are no minutes to read applies here."""
    if following is None:
        return f"{NOT_AVAILABLE}; {NO_LATER_SESSION}."
    month_day = _month_day(_meeting_date(following.starts_at))
    if packet is None or not record_pages(ctx.conn, packet.id):
        return f"{NOT_AVAILABLE}; {EXPECTED_IN.format(month_day=month_day)}."
    return f"{NOT_AVAILABLE}; {NO_RUN_FOUND.format(month_day=month_day)}."


def _next_regular(conn: sqlite3.Connection, meeting: Any) -> Any:
    """The next regular session of the same body, or None when none is listed.

    A cancelled session holds no minutes and a continued one is the same
    session, so neither is the session whose packet holds this meeting's draft.
    The sessions are looked at in date order and the first regular one is the
    answer, so a study session between two regular sessions is passed over
    rather than taken as the next one.
    """
    for other in meetings_of_body(conn, meeting.body_id):
        if other.id == meeting.id or other.starts_at <= meeting.starts_at:
            continue
        if other.type != "regular" or other.is_cancelled:
            continue
        return other
    return None


def _packet_of(conn: sqlite3.Connection, meeting: Any) -> Record | None:
    """The packet of a meeting, when it has one.

    A meeting with two packets is read from the first one stored, which is what
    ``records_of_meeting`` orders them to be. Both are the same meeting's
    documents, and reading one of them twice does not make the second better.
    """
    for record in records_of_meeting(conn, meeting.id):
        if record.kind == portal.PACKET_KIND:
            return record
    return None


def _meeting_date(starts_at: str) -> date:
    """The local date a meeting starts on, from the first ten characters.

    Spec 16.2: a meeting's date is the local date it was published with, and
    nothing is converted to UTC.
    """
    return date.fromisoformat(starts_at[:10])


def _month_day(on: date) -> str:
    """A date as a person says it in a sentence: "October 6"."""
    return f"{calendar.month_name[on.month]} {on.day}"


# -- Reading the pages --------------------------------------------------------


def page_lines(text: str) -> list[str]:
    """The lines of a page, with the footers gone and the spacing folded.

    A page prints its footer at the foot and a packet's pages print a running
    head that says the same thing on every one of them. Both are the page
    talking about itself rather than about the minutes, so both are dropped
    here, and what is left is read as one line per line. A non-breaking space is
    a space: the minutes are full of them.
    """
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.replace(" ", " ").split())
        if not line or _PAGE_LINE.match(line):
            continue
        lines.append(line)
    return lines


def _repeated_headings(run: MinutesRun) -> frozenset[str]:
    """The lines that are a running head rather than a line of the minutes.

    A running head is on every page of the run and carries a date; a line of the
    minutes is not and does not. The two are told apart by both at once because
    either one alone is wrong: "MOTION" is on most of these pages and is not a
    head, and a date is printed in the minutes themselves.
    """
    seen: dict[str, set[int]] = {}
    for page in run.pages:
        for line in set(page_lines(page.text)):
            seen.setdefault(line, set()).add(page.page_number)
    enough = max(2, (len(run.pages) + 1) // 2)
    return frozenset(
        line for line, pages in seen.items() if len(pages) >= enough and _YEAR.search(line)
    )


def _is_head(lines: Sequence[str], *, body_name: str, wanted: re.Pattern[str]) -> bool:
    """Whether these lines are the head of this body's minutes of this date."""
    if len(lines) < 2 or lines[0].casefold() != MINUTES_HEADING.casefold():
        return False
    head = " ".join(lines[1:HEAD_LINES])
    if body_name and body_name.casefold() not in head.casefold():
        return False
    return wanted.search(head) is not None


def _head_of(lines: Sequence[str]) -> str:
    """The head of a document, as the lines it printed, joined for one row."""
    return " / ".join(lines[:HEAD_LINES])


def _date_pattern(on: date) -> re.Pattern[str]:
    """A date as a packet prints it, which is with a leading zero or without."""
    return re.compile(
        rf"\b{calendar.month_name[on.month]}\s+0?{on.day}\s*,?\s*{on.year}\b", re.IGNORECASE
    )


def _draft_number(page: RecordPage) -> int | None:
    """The number the draft minutes printed on this page, or None.

    A page that carries a page of a draft minutes document prints two footers:
    the packet's own, and the one the draft gave the page. The draft's number is
    the one that is not the packet's own. A page printing fewer than two footers
    is not carrying a draft page, and its number is None, which ends a run.
    """
    printed = printed_footers(page.text)
    if len(printed) < 2:
        return None
    own = own_footer(printed, page.page_number)
    others = [number for number in printed if number != own]
    return min(others) if others else None


# -- Reading the motions ------------------------------------------------------


def _motion_text(lines: Sequence[tuple[int, str]], start: int) -> tuple[str, int]:
    """The text of a motion, from the line that moved it to where it stops.

    The text is the mover's line and the lines after it until the minutes start
    printing something else: a label, a result, the next motion, or the end of
    the run. A line that ends a sentence does not stop it, because a withdrawn
    motion's discussion paragraph ends with a sentence and is not part of the
    motion: the reading keeps the paragraph rather than guessing where the
    motion ended inside it.
    """
    pieces = [_MOVER_LINE.match(lines[start][1]).group(3)]  # type: ignore[union-attr]
    index = start + 1
    while index < len(lines):
        line = lines[index][1]
        if _starts_block(line):
            break
        pieces.append(line)
        index += 1
    return _joined(pieces), index


def _read_outcome(lines: Sequence[tuple[int, str]], start: int) -> _Outcome:
    """The outcome of a motion, from the first line after its text.

    The first line the minutes print about what became of the motion is the
    outcome, and the scan stops there: a withdrawn motion is printed as
    ``MOTION WITHDRAWN`` and then three empty name lists and a bare ``0 – 0``,
    and the count line is printed last for a motion that was counted. What is
    read first is therefore what is kept, and the rest of the block is read for
    the names and the numbers it carries.
    """
    result = "unknown"
    line = ""
    tally = None
    names: dict[str, tuple[str, ...]] = {}
    index = start
    while index < len(lines):
        text = lines[index][1]
        folded = text.casefold()
        if folded == _WITHDRAWN.casefold():
            if not line:
                result, line = "withdrawn", text
            index += 1
            continue
        if folded in _BLOCK_STARTERS:
            break
        label = _LABELS.get(folded[:-1].strip()) if text.endswith(":") else None
        if label is not None:
            collected = []
            index += 1
            while index < len(lines) and not _is_outcome_line(lines[index][1]):
                collected.append(lines[index][1])
                index += 1
            names[label] = _split_names(" ".join(collected))
            continue
        counted = _RESULT_LINE.match(text)
        if counted is not None:
            if not line:
                result = "passed" if counted.group(1).casefold() == "carried" else "failed"
                line = text
                tally = {"yes": int(counted.group(2)), "no": int(counted.group(3))}
            index += 1
            break
        if _BARE_TALLY.match(text):
            if not line:
                line = text
            index += 1
            continue
        break
    return _Outcome(
        result=result,
        line=line,
        approved=names.get("approved", ()),
        dissented=names.get("dissented", ()),
        abstained=names.get("abstained", ()),
        tally=tally,
        stop=index,
    )


@dataclass(frozen=True)
class _Outcome:
    """What the minutes printed about a motion after its text."""

    result: str
    line: str
    approved: tuple[str, ...] = ()
    dissented: tuple[str, ...] = ()
    abstained: tuple[str, ...] = ()
    tally: dict[str, int] | None = None
    stop: int = 0


def _starts_block(line: str) -> bool:
    """Whether a line is the start of something the minutes print as a block."""
    folded = line.casefold()
    if folded in _BLOCK_STARTERS:
        return True
    if line.endswith(":") and line.casefold().rstrip(":").strip() in _LABELS:
        return True
    return _RESULT_LINE.match(line) is not None


def _is_outcome_line(line: str) -> bool:
    """Whether a line ends a label's name list rather than adding a name.

    The line of numbers a withdrawn motion prints in place of a result ends it
    too: it is not a name, and the list it follows is a list of names.
    """
    return _starts_block(line) or _BARE_TALLY.match(line) is not None


def _split_names(text: str) -> tuple[str, ...]:
    """The names a label was printed with, or nothing when it printed "None".

    The minutes write the names of a vote separated by commas and sometimes by
    "and", and write "None" when nobody was in that column. "None" is the empty
    list, not a person called None.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.casefold() == "none":
        return ()
    parts = re.split(r"[,;]|\band\b", cleaned, flags=re.IGNORECASE)
    return tuple(part.strip() for part in parts if part.strip())


def _evidence(lines: Sequence[tuple[int, str]], start: int, stop: int) -> str:
    """The motion as it was printed, from the mover's line through its outcome."""
    return _joined([line for _, line in lines[start:stop]])


def _joined(pieces: Sequence[str]) -> str:
    """Lines as one text, with the hyphen a wrapped line ended on kept tight.

    A packet breaks a long line where it likes, and it likes to break a
    hyphenated word and an ordinance number there: "O-" ends one line and
    "2026-54" begins the next. Rejoining those with a space would invent a
    number that is not the number, so a line ending in a hyphen is joined to the
    next one without one, as is a line that is nothing but the tail of an
    ordinal ("26" then "th").
    """
    text = ""
    for piece in pieces:
        if not text:
            text = piece
            continue
        if text.endswith("-") or _ORDINAL_SUFFIX.match(text.split(" ")[-1]):
            text += piece
        else:
            text += " " + piece
    return text.strip()


def _parts(number: str) -> tuple[str, ...]:
    """The parts of an agenda item's number ("9.B" is "9" and "B")."""
    return tuple(part for part in re.split(r"[.\s]+", number.strip()) if part)


def consent_coverage(text: str, items: Sequence[ItemRef]) -> ConsentCoverage | None:
    """What a consent-agenda motion covers, or None when it is not one.

    None means the text is not a motion on the consent agenda: it does not name
    a consent agenda, or these items hold no consent-agenda section to be a
    motion on. A finding that is not None is the whole answer, because what a
    consent motion covers is decided by the clause it names its exceptions in,
    and a second reading of that clause is how a motion ends up linked to the
    items it took out.
    """
    if not re.search(r"\bconsent\s+agenda\b", text, re.IGNORECASE):
        return None
    section = next(
        (item for item in items if item.title.strip().casefold().startswith("consent agenda")),
        None,
    )
    if section is None:
        return None
    named = _excepted_keys(text, section.number)
    if named is None:
        return ConsentCoverage(unreadable=True)
    covered: list[ItemRef] = []
    excepted: list[ItemRef] = []
    for item in items:
        if not _is_under(item.number, section.number):
            continue
        if _number_key(item.number) in named:
            excepted.append(item)
        else:
            covered.append(item)
    return ConsentCoverage(covered=tuple(covered), excepted=tuple(excepted))


def consent_links(text: str, items: Sequence[ItemRef]) -> tuple[ItemLink, ...] | None:
    """The consent-agenda items a motion's text covers, or None when it is not one.

    The clause the motion names its exceptions in is read first, because it
    reads as a reference to the items it names and linking the motion to those
    would be exactly backwards. An empty tuple is a consent motion that covers
    nothing, which is a finding and not a fall-through.
    """
    coverage = consent_coverage(text, items)
    if coverage is None:
        return None
    clause = _consent_clause(text)
    return tuple(ItemLink(item.id, "consent", clause) for item in coverage.covered)


def _is_under(number: str, section: str) -> bool:
    """Whether an item's number is one of a section's items.

    The section itself is not one of its items, and "10." is not under "1.":
    what follows the section's number has to begin the item's own part of it.
    Both numbers are read without the dot the agenda writes a section with,
    so the section's own "9." and its first item's "9.A" stay apart.
    """
    base = section.rstrip(".")
    own = number.rstrip(".")
    if not own.startswith(base):
        return False
    rest = own[len(base) :]
    if not rest:
        return False
    return rest.startswith(".") or (len(rest) == 1 and rest.isalpha())


def _consent_clause(text: str) -> str:
    """The words that make a motion a consent-agenda motion, for the evidence.

    The clause is quoted from the motion rather than written here, so what the
    link shows is what the minutes printed and not a sentence of this module's
    own about it.
    """
    found = re.search(r"\bconsent\s+agenda\b.*", text, re.IGNORECASE | re.DOTALL)
    return found.group(0).strip() if found else ""


def _excepted_keys(text: str, section: str) -> frozenset[str] | None:
    """The item numbers a motion's exception clause names, as one key per number.

    "except items 9B and 9E" names two, spelled without the dot the agenda
    spells them with, so the keys are compared with everything but the letters
    and the digits taken out. A number a chair said as a word is read too:
    "items nine A and nine B" is the same two items, and the letter that
    follows a number word is part of the number and not a word of its own.

    A chair speaking says the letters without the section ("minus items B, E,
    B, and E"), so a letter on its own is read as the item of the consent
    section of that letter. Reading it as nothing would have the motion cover
    the items the chair took out, which is the false vote this reading exists
    to prevent, so the section is used rather than the letter dropped.

    An empty set is a motion with nothing to except. None is a motion that has
    an exception clause and no number and no letter in it this reading can
    read: the two are different facts, and only the second one means the
    motion's coverage is unknown (spec 16.3).
    """
    found = _EXCEPTION.search(text)
    if found is None:
        return frozenset()
    base = section.rstrip(".")
    keys: set[str] = set()
    pending: str | None = None
    for token in re.split(r"[,;&]|\band\b|\s+", text[found.end() :], flags=re.IGNORECASE):
        cleaned = token.strip().strip(".:;,")
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded in NUMBER_WORDS:
            number = str(NUMBER_WORDS[folded])
        elif _ITEM_NUMBER.match(cleaned):
            number = cleaned
        elif len(cleaned) == 1 and cleaned.isalpha():
            if pending is not None:
                # "9 A" and "nine A" are item 9.A: the letter belongs to the
                # number that was just read, so it replaces that reading.
                keys.discard(_number_key(pending))
                combined = f"{pending}{cleaned}"
            else:
                combined = f"{base}.{cleaned}"
            keys.add(_number_key(combined))
            pending = None
            continue
        else:
            pending = None
            continue
        keys.add(_number_key(number))
        pending = number
    return frozenset(keys) if keys else None


def _number_key(number: str) -> str:
    """An item number as one lowercase key ("9.B" and "9B" are the same key)."""
    return "".join(character for character in number.casefold() if character.isalnum())


# -- Reading the video --------------------------------------------------------


def _spoken_line(segments: Sequence[Segment], start: int) -> tuple[str, int]:
    """The line a chair asked for a motion in, and where it ended.

    A caption breaks a sentence where the captioner liked, so the line runs on
    until the question is asked or a couple of segments have gone by.
    """
    last = start
    pieces = [segments[start].text]
    while (
        last + 1 < len(segments)
        and last - start + 1 < SPOKEN_LINE_LINES
        and not pieces[-1].strip().endswith("?")
    ):
        last += 1
        pieces.append(segments[last].text)
    return _joined(pieces), last


def _item_spoken_of(line: str, items: Sequence[ItemRef]) -> ItemRef | None:
    """The item a spoken motion names, when it names exactly one.

    A number the item is known by is the first reading, and an item number is
    the second, as on the page. More than one match is no match: a line naming
    two ordinances is not a motion on one of them.
    """
    found: dict[int, ItemRef] = {}
    for identifier in find_identifiers(line):
        canonical = identifier.canonical()
        for item in items:
            if canonical in item.identifiers:
                found.setdefault(item.id, item)
    if not found:
        for reference in find_item_references(line):
            for item in items:
                if reference.matches(_parts(item.number)):
                    found.setdefault(item.id, item)
    if len(found) != 1:
        return None
    return next(iter(found.values()))


def _spoken_outcome(segments: Sequence[Segment], start: int) -> tuple[str, int | None]:
    """The chair's line about what the vote did, and where it was."""
    for index in range(start, min(start + SPOKEN_OUTCOME_LINES, len(segments))):
        if _SPOKEN_OUTCOME.search(segments[index].text):
            return segments[index].text.strip(), index
    return "", None


def _spoken_result(line: str) -> str:
    """What a spoken outcome line says the vote did, as a result.

    A chair who says nothing this reading understands leaves the result unknown,
    which is what the line says: the video was read and did not settle it.
    """
    folded = line.casefold()
    if re.search(r"\b(denied|fails?|failed|does not carry)\b", folded):
        return "failed"
    if re.search(r"\b(carr(?:ies|ied)|unanimous)\b", folded):
        return "passed"
    return "unknown"


def _spoken_evidence(motion_line: str, outcome: str) -> str:
    """The two lines a video vote rests on, labelled with where they came from."""
    quoted = f'"{motion_line}"'
    if outcome:
        quoted += f'; "{outcome}"'
    return f"{FROM_VIDEO}: {quoted}"


__all__ = [
    "EXPECTED_IN",
    "FROM_VIDEO",
    "HEAD_LINES",
    "MINUTES_HEADING",
    "NOT_AVAILABLE",
    "NO_LATER_SESSION",
    "NO_RUN_FOUND",
    "ConsentCoverage",
    "ItemLink",
    "ItemRef",
    "MinutesRun",
    "MotionRead",
    "SpokenReading",
    "SpokenVote",
    "consent_coverage",
    "consent_links",
    "find_run",
    "items_of",
    "links_for",
    "page_lines",
    "parse_motions",
    "read_minutes",
    "spoken_reading",
    "spoken_votes",
]
