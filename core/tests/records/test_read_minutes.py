"""The votes out of the minutes, and out of the video when there are none yet.

The draft minutes of a regular session are approved at the next regular
session, so the draft of one meeting sits in the packet of the *next* one
(spec 9.4). A meeting's votes therefore cannot be read out of a document of
that meeting: they are read out of another meeting's packet, and reading them
is what this module tests.

The six things the checks were asked for, in the order they were asked for:

* a motion block parses into its mover, its seconder, its text, its names and
  its counted result;
* a consent-agenda motion covers every consent item except the ones its own
  text names, and is a motion on each of them;
* an item moved on more than once keeps every motion, and the one vote the item
  gets names the last motion that was actually decided;
* a vote read from the video has no tally, and there is nowhere to put one;
* a meeting whose minutes are not published yet pauses with the plain sentence
  spec 10.4 asks for;
* a consent-agenda motion read from the video excepts the same items the
  minutes' own clause excepts, so the item it took out is not stored as an item
  that motion approved.

Three of the tests read the recorded September 8 and September 22, 2026 agendas,
packet and caption tracks of the oversight repository in place and are skipped
when the recordings are not on this machine. Everything else is built here, so
the six checks hold on a machine with no recordings at all.

Nothing here reaches the network: the portal is never asked for anything, and
the records the tests read are built in the database (rule 7).
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path

import pytest
from pypdf import PdfReader

from townrecord.artifacts import store
from townrecord.captions import parse_srv3
from townrecord.jobs import DONE, ORIGIN_SCHEDULED, PAUSED, read_checkpoint
from townrecord.records.minutes import (
    EXPECTED_IN,
    FROM_VIDEO,
    NO_LATER_SESSION,
    NO_RUN_FOUND,
    NOT_AVAILABLE,
    ConsentCoverage,
    ItemRef,
    MinutesRun,
    SpokenVote,
    consent_coverage,
    find_run,
    page_lines,
    parse_motions,
    spoken_votes,
)
from townrecord.records.portal import (
    PACKET_KIND,
    READ_MINUTES,
    MinutesRequest,
    adapter_for,
    identifiers_of,
)
from townrecord.repo import (
    READ_MINUTES_KIND,
    RecordPage,
    Segment,
    Vote,
    get_citation,
    get_source,
    insert_agenda_item,
    insert_meeting,
    insert_record,
    insert_record_page,
    insert_segment,
    insert_transcript,
    insert_video,
    insert_vote,
    meeting_by_portal_id,
    minutes_document,
    motion_items_of,
    motions_of_item,
    motions_of_meeting,
    votes_of_item,
)

from .conftest import (
    AGENDA_16805,
    AGENDA_16821,
    CAPTIONS_16805,
    CAPTIONS_SEP22,
    PACKET_MINUTES_SEP08,
    Area,
    FakePortal,
    Sync,
    days_from_today,
    meeting,
    month_day,
    needs,
)
from .test_extract_pages import a_pdf_of_pages

#: The two sittings the tests build: the one whose minutes are read, and the
#: next regular session, whose packet is where spec 9.4 puts them.
SEPT_8 = "2026-09-08T19:00:00"
SEPT_22 = "2026-09-22T19:00:00"

#: The body the meetings belong to, as the fixture portal names it.
BODY_NAME = "City Council"

#: The date the draft minutes read here are the minutes of.
HELD = date(2026, 9, 8)

#: The head a draft minutes document prints (spec 9.4). Four lines, which is
#: what ``HEAD_LINES`` is, and the date is one of them because that is how a
#: reading tells one run in a packet from another.
HEAD = (
    "MINUTES",
    "City Council Regular Session",
    "September 8, 2026",
    "City Council Chambers, 350 Kimbark St., Longmont, CO",
)

#: The en dash the minutes print between the two numbers of a tally (U+2013).
DASH = "–"

#: A non-breaking space (U+00A0). The minutes are full of them where a printed
#: page had one, and a reading that did not fold them would split words.
NBSP = " "

#: The nine names the September 8, 2026 council voted with. They are the names
#: the fixture packet prints, kept here so a synthetic page prints the same
#: shape of block the real one does.
COUNCIL = (
    "Susie Hidalgo-Fahring",
    "Sean McCoy",
    "Diane Crist",
    "Alex Kalkhofer",
    "Jake Marsing",
    "Matthew Popkin",
    "Crystal Prieto",
)

#: The September 8, 2026 agenda numbers the consent motion of that meeting
#: covers: every item under "9." except the two it names.
CONSENT_COVERED = (
    "9.A",
    "9.C",
    "9.D",
    "9.F",
    "9.G",
    "9.H",
    "9.I",
    "9.I.1",
    "9.I.2",
    "9.I.3",
    "9.I.4",
)


# -- Building the meetings, the items and the pages ---------------------------


def a_meeting(
    sync: Sync,
    area: Area,
    *,
    starts_at: str = SEPT_8,
    title: str = "City Council Regular Session",
    type: str = "regular",
    is_cancelled: bool = False,
) -> int:
    """Store one meeting of the test's body and return its row id."""
    return insert_meeting(
        sync.conn,
        body_id=area.body_id,
        starts_at=starts_at,
        title=title,
        type=type,
        is_cancelled=is_cancelled,
    )


def an_item(
    sync: Sync,
    meeting_id: int,
    number: str,
    *,
    title: str = "",
) -> int:
    """Store one agenda item, with the numbers its title names, and return its id."""
    return insert_agenda_item(
        sync.conn,
        meeting_id=meeting_id,
        number=number,
        title=title,
        identifiers=identifiers_of(title),
    )


def a_page(page_number: int, *lines: str) -> RecordPage:
    """One page of a record, printing its own number as its footer."""
    return RecordPage(
        id=page_number,
        record_id=1,
        page_number=page_number,
        footer_page_number=page_number,
        text="\n".join([*lines, f"Page {page_number}"]),
        ocr_reason=None,
    )


def a_draft_page(page_number: int, draft_number: int, *lines: str) -> RecordPage:
    """One packet page carrying a draft minutes page, so it prints two footers.

    The first footer is the packet's own, which is the page's place in the
    packet, and the second is the number the draft gave it (spec 9.4). That
    second number is what chains the run of pages together.
    """
    return RecordPage(
        id=page_number,
        record_id=1,
        page_number=page_number,
        footer_page_number=page_number,
        text="\n".join([*lines, f"Page {page_number}", f"Page {draft_number}"]),
        ocr_reason=None,
    )


def a_draft_run(*pages_of_lines: list[str] | tuple[str, ...]) -> MinutesRun:
    """A run of draft minutes built the way a packet holds one.

    The pages are numbered 17 up, because that is where the measured September
    22, 2026 packet starts the September 8 minutes, and the draft numbers count
    from one from there. The run is found by ``find_run`` rather than assembled
    here, so every test that uses one exercises the finding of it too.
    """
    pages = [
        a_draft_page(17 + offset, 1 + offset, *lines) for offset, lines in enumerate(pages_of_lines)
    ]
    run = find_run(pages, body_name=BODY_NAME, on=HELD)
    assert run is not None, "the pages built here are the head of this meeting's minutes"
    return run


def stored_pages(*pages: RecordPage) -> list[tuple[int, str]]:
    """The pages of a run, as the pairs a record's pages are stored from."""
    return [(page.page_number, page.text) for page in pages]


def a_named(label: str, names: tuple[str, ...]) -> list[str]:
    """One label and the names under it, or the word the minutes print instead.

    The minutes print a label on a line of its own and the names under it, and
    print "None" when nobody was in that column. Both shapes are built here so
    a synthetic block is the shape of the real one.
    """
    return [f"{label}:", ", ".join(names) if names else "None"]


def a_motion_block(
    mover: str,
    seconder: str,
    text: str,
    *,
    approved: tuple[str, ...] = COUNCIL,
    dissented: tuple[str, ...] = (),
    abstained: tuple[str, ...] = (),
    result: str = f"Carried: 7 {DASH} 0",
) -> list[str]:
    """One counted motion, printed the way the minutes print it."""
    return [
        "MOTION",
        f"{mover} moved, seconded by {seconder}, {text}",
        *a_named("Approved", approved),
        *a_named("Dissented", dissented),
        *a_named("Abstained", abstained),
        result,
    ]


def a_withdrawn_block(
    mover: str,
    seconder: str,
    text: str,
    *,
    discussion: tuple[str, ...] = (),
    result: str = f"0 {DASH} 0",
) -> list[str]:
    """One withdrawn motion, with the lines the minutes print around it.

    A withdrawn motion is printed as ``MOTION WITHDRAWN`` and then the three
    empty name lists and a bare pair of zeroes, which is a template artifact
    and not a count (spec 10.4).
    """
    return [
        "MOTION",
        f"{mover} moved, seconded by {seconder}, {text}",
        *discussion,
        "MOTION WITHDRAWN",
        *a_named("Approved", ()),
        *a_named("Dissented", ()),
        *a_named("Abstained", ()),
        result,
    ]


def a_packet(
    sync: Sync,
    storage_root: Path,
    area: Area,
    meeting_id: int,
    pages: list[tuple[int, str]],
    *,
    data: bytes = b"a packet, as the store keeps it",
) -> int:
    """Store a packet of one meeting, with its pages, and return the record id."""
    artifact = store(sync.conn, storage_root, "packet", data, "pdf")
    record_id = insert_record(
        sync.conn,
        meeting_id=meeting_id,
        kind=PACKET_KIND,
        artifact_id=artifact.id,
        source_id=area.portal_id,
        title="City Council Regular Session Packet",
    )
    for page_number, text in pages:
        insert_record_page(
            sync.conn,
            record_id=record_id,
            page_number=page_number,
            text=text,
            footer_page_number=page_number,
        )
    return record_id


def read(sync: Sync, meeting_id: int) -> int:
    """Queue one reading of a meeting's minutes and run the lane it is on."""
    job_id = sync.queue(READ_MINUTES, MinutesRequest(meeting_id).as_payload())
    sync.lane("normal")
    return job_id


def a_pause_reason(meeting_id: int, sentence: str, *, written: int = 0) -> str:
    """The reason a reading pauses with when it has no minutes to read.

    It is the plain sentence spec 10.4 asks for, and one more sentence when the
    video gave the meeting votes in the minutes' place: the reading says what it
    did as well as what it could not do.
    """
    reason = f"The minutes of meeting {meeting_id} are {sentence.rstrip('.')}."
    if written:
        reason += f" {written} vote(s) were read from the video instead."
    return reason


def a_video(sync: Sync, area: Area, meeting_id: int) -> int:
    """Store the meeting's primary video and return its id."""
    return insert_video(
        sync.conn,
        source_id=area.portal_id,
        platform_video_id="aVideoId01",
        meeting_id=meeting_id,
        url="https://www.youtube.com/watch?v=aVideoId01",
        is_primary=True,
    )


def a_transcript(sync: Sync, storage_root: Path, video_id: int, text: bytes) -> int:
    """Store one auto caption transcript of a video, with no lines yet."""
    artifact = store(sync.conn, storage_root, "transcript", text, "srv3")
    return insert_transcript(
        sync.conn, video_id=video_id, artifact_id=artifact.id, origin="auto_captions"
    )


def its_lines(sync: Sync, transcript_id: int, text: bytes) -> None:
    """Store the lines of one transcript, parsed from a recorded track."""
    for segment in parse_srv3(text):
        insert_segment(
            sync.conn,
            transcript_id=transcript_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
        )


def a_spoken_transcript(
    sync: Sync, storage_root: Path, video_id: int, lines: tuple[str, ...]
) -> int:
    """Store a transcript of one spoken line per segment, a second apart.

    Real caption bytes are used for the recorded track; this is for the tests
    that say what the chair said themselves, so what the reading does with a
    mention can be held to a line that is known here.
    """
    video = a_transcript(sync, storage_root, video_id, b"<transcript/>")
    for index, text in enumerate(lines):
        insert_segment(
            sync.conn,
            transcript_id=video,
            start_ms=1000 * (index + 1),
            end_ms=1000 * (index + 2),
            text=text,
        )
    return video


def meeting_with_real_items(
    sync: Sync, area: Area, *, starts_at: str = SEPT_8, agenda: Path = AGENDA_16805
) -> int:
    """Store a meeting whose items are the ones the recorded agenda prints.

    The items come from the real HTML agenda through the real adapter, which is
    the same reading the alignment job does, so what the motions are linked
    against is the real agenda rather than a list written here.
    """
    meeting_id = a_meeting(sync, area, starts_at=starts_at)
    source = get_source(sync.conn, area.portal_id)
    assert source is not None
    for item in adapter_for(source).parse_html_agenda(agenda.read_bytes()):
        insert_agenda_item(
            sync.conn,
            meeting_id=meeting_id,
            number=item.number,
            title=item.title,
        )
    return meeting_id


# -- Check 1: a motion block parses -------------------------------------------


def test_a_motion_block_parses_into_its_parts() -> None:
    """The mover, the seconder, the text, the names and the count come apart."""
    run = a_draft_run(
        [
            *HEAD,
            *a_motion_block(
                "Sean McCoy",
                "Diane Crist",
                "to approve the August 25, 2026 Regular Session minutes as presented",
                approved=("Sean McCoy", "Diane Crist", "Alex Kalkhofer"),
            ),
        ]
    )

    motions = parse_motions(run)

    assert len(motions) == 1
    motion = motions[0]
    assert motion.ordinal == 1
    assert motion.page_number == 17, "the motion's page is its page in the packet"
    assert (motion.mover, motion.seconder) == ("Sean McCoy", "Diane Crist")
    assert motion.text == "to approve the August 25, 2026 Regular Session minutes as presented"
    assert motion.result == "passed"
    assert motion.outcome == f"Carried: 7 {DASH} 0"
    assert motion.approved == ("Sean McCoy", "Diane Crist", "Alex Kalkhofer")
    assert motion.dissented == (), 'the minutes printed "None", which is nobody'
    assert motion.abstained == ()
    assert motion.tally == {"yes": 7, "no": 0}


def test_a_motion_the_minutes_printed_no_seconder_for_keeps_an_empty_one() -> None:
    """One Longmont minute printed "seconded by ," and that is not a seconder."""
    run = a_draft_run(
        [
            *HEAD,
            *a_motion_block(
                "Crystal Prieto",
                "",
                "to amend O-2026-58, A Bill For An Ordinance",
            ),
        ]
    )

    motion = parse_motions(run)[0]

    assert motion.mover == "Crystal Prieto"
    assert motion.seconder == ""


def test_a_run_stops_where_the_draft_numbers_stop() -> None:
    """The run is the pages whose draft numbers count on, and nothing more.

    A packet holds more than the minutes, so the reading has to know where the
    minutes end: the next page whose footer does not carry the next draft
    number is the first page of something else (spec 9.4).
    """
    pages = [
        a_draft_page(17, 1, *HEAD, "one page of mine"),
        a_draft_page(18, 2, "another page of mine"),
        # A page of the packet that is not carrying a draft page.
        a_page(19, "ORDINANCE 2026-58", "A Bill For An Ordinance"),
    ]

    run = find_run(pages, body_name=BODY_NAME, on=HELD)

    assert run is not None
    assert (run.start_page, run.end_page, run.page_count) == (17, 18, 2)


def test_a_run_of_another_sitting_is_not_this_meeting_s() -> None:
    """The September 1 minutes in the September 22 packet are not September 8's.

    The measured September 22, 2026 packet holds two runs, so the date in the
    head is what tells them apart (spec 9.4).
    """
    other = ["MINUTES", "City Council Regular Session", "September 1, 2026", "Council Chambers"]
    pages = [a_draft_page(17, 1, *other), a_draft_page(18, 2, "a page of the wrong sitting")]

    assert find_run(pages, body_name=BODY_NAME, on=date(2026, 9, 8)) is None
    assert find_run(pages, body_name="Parks Board", on=date(2026, 9, 1)) is None
    assert find_run(pages, body_name=BODY_NAME, on=date(2026, 9, 1)) is not None


def test_lines_drop_the_footers_and_fold_the_spacing() -> None:
    """The spacing is folded because the minutes are full of non-breaking spaces."""
    text = "\n".join(
        [
            "MOTION",
            NBSP.join(["Alex", "Kalkhofer", "moved,", "seconded", "by", "Matthew", "Popkin"]),
            "Page 5",
            "",
        ]
    )

    assert page_lines(text) == ["MOTION", "Alex Kalkhofer moved, seconded by Matthew Popkin"]


# -- Check 2: the consent agenda's exceptions ---------------------------------


def consent_meeting(sync: Sync, area: Area) -> tuple[int, dict[str, int]]:
    """A meeting whose agenda is a consent section with the fixture's items."""
    meeting_id = a_meeting(sync, area)
    items = {"9.": an_item(sync, meeting_id, "9.", title="Consent Agenda")}
    for number in ("9.A", "9.B", "9.C", "9.D", "9.E", "9.I", "9.I.1"):
        items[number] = an_item(sync, meeting_id, number, title=f"Item {number}")
    items["10."] = an_item(sync, meeting_id, "10.", title="Second Reading")
    items["10.A"] = an_item(sync, meeting_id, "10.A", title="An ordinance")
    return meeting_id, items


def test_a_consent_motion_covers_every_consent_item_except_the_ones_it_names(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The clause that excepts two items is read as an exception, not a link.

    The motion's own text names 9B and 9E, and the two of them are exactly the
    items it is *not* a motion on. Reading the clause backwards would link the
    motion to the two items it took out of the consent agenda.
    """
    meeting_id, items = consent_meeting(sync, area)
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    a_packet(
        sync,
        storage_root,
        area,
        follow,
        stored_pages(
            *a_draft_run(
                [
                    *HEAD,
                    *a_motion_block(
                        "Alex Kalkhofer",
                        "Matthew Popkin",
                        "to approve the Consent Agenda except items 9B and 9E",
                    ),
                ]
            ).pages
        ),
    )

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == DONE
    motions = motions_of_meeting(sync.conn, meeting_id)
    assert len(motions) == 1
    consent = motions[0]
    linked = {item_id: kind for item_id, kind in _links_of(sync, consent.id)}
    covered = {number for number in items if number in ("9.A", "9.C", "9.D", "9.I", "9.I.1")}
    assert {number for number, item_id in items.items() if item_id in linked} == covered
    assert set(linked.values()) == {"consent"}
    assert items["9.B"] not in linked, "the motion took 9B out of the consent agenda"
    assert items["9.E"] not in linked, "the motion took 9E out of the consent agenda"
    assert items["9."] not in linked, "the section is not one of its own items"
    assert items["10.A"] not in linked, "10.A is not under 9"

    # Every item it covers is an item it decided, and each one has the vote.
    for number in covered:
        votes = votes_of_item(sync.conn, items[number])
        assert len(votes) == 1
        assert votes[0].source_kind == "minutes"
        assert votes[0].motion_id == consent.id
        assert votes[0].result == "passed"
        assert votes[0].tally == {"yes": 7, "no": 0}
    assert votes_of_item(sync.conn, items["9.B"]) == [], "nothing was moved on 9.B here"


def _links_of(sync: Sync, motion_id: int) -> list[tuple[int, str]]:
    """One motion's item links, as (item id, link kind) pairs."""
    return [(link.agenda_item_id, link.link_kind) for link in motion_items_of(sync.conn, motion_id)]


# -- Check 3: two motions on one item -----------------------------------------


def test_two_motions_on_one_item_are_both_stored_and_the_vote_names_the_last(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """An amendment and the adoption it led to are two rows, and one vote.

    Item 9.B of the September 8, 2026 minutes was moved on seven times, three
    of them withdrawn, before it was approved. ``votes`` holds one row per item
    and source kind, so it can hold only the outcome: the motions are their own
    rows, and the vote names the last one that was decided (spec 10.4).
    """
    meeting_id = a_meeting(sync, area)
    item_id = an_item(
        sync,
        meeting_id,
        "9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    run = a_draft_run(
        [
            *HEAD,
            *a_motion_block(
                "Matthew Popkin",
                "Jake Marsing",
                "to amend O-2026-58, Section 2.95.050.F to add a second sentence",
            ),
        ],
        [
            *a_motion_block(
                "Matthew Popkin",
                "Jake Marsing",
                "to approve O-2026-58, A Bill For An Ordinance Amending Title 2",
            ),
        ],
    )
    a_packet(sync, storage_root, area, follow, stored_pages(*run.pages))

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == DONE
    motions = motions_of_meeting(sync.conn, meeting_id)
    assert [motion.ordinal for motion in motions] == [1, 2]
    assert [motion.page_number for motion in motions] == [17, 18]
    assert motions[-1].text.startswith("to approve O-2026-58")
    assert [motion.id for motion in motions_of_item(sync.conn, item_id)] == [
        motion.id for motion in motions
    ], "both motions are motions on the item"

    votes = votes_of_item(sync.conn, item_id)
    assert len(votes) == 1, "the item has one outcome, from one source kind"
    assert votes[0].motion_id == motions[-1].id, "the vote names the motion that decided it"
    assert votes[0].result == "passed"
    assert votes[0].tally == {"yes": 7, "no": 0}
    citation = get_citation(sync.conn, int(votes[0].citation_id))
    assert citation is not None
    assert citation.page_number == 18, "the citation is the page of the motion that decided it"


def test_a_withdrawn_motion_is_stored_and_decides_nothing(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """A withdrawn motion is a motion of the item and not an outcome of it.

    It printed no count, and the bare "0 – 0" under it is a template artifact
    rather than a tally of nothing (spec 10.4).
    """
    meeting_id = a_meeting(sync, area)
    item_id = an_item(
        sync,
        meeting_id,
        "9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    run = a_draft_run(
        [
            *HEAD,
            *a_withdrawn_block(
                "Diane Crist",
                "Alex Kalkhofer",
                "to amend O-2026-58, Section 2.95.050.F",
                discussion=(
                    "Council consulted with City Attorney Eugene Mei who advised",
                    "that this amendment could create some issues.",
                ),
            ),
        ]
    )
    a_packet(sync, storage_root, area, follow, stored_pages(*run.pages))

    read(sync, meeting_id)

    motions = motions_of_meeting(sync.conn, meeting_id)
    assert len(motions) == 1
    withdrawn = motions[0]
    assert withdrawn.result == "withdrawn"
    assert withdrawn.outcome == "MOTION WITHDRAWN"
    assert withdrawn.tally is None, "nobody was counted, so there is no tally"
    assert f"0 {DASH} 0" in withdrawn.evidence, "the line is kept where it was read"
    assert withdrawn.approved == []
    assert "consulted with City Attorney" in withdrawn.text, (
        "the discussion paragraph after a withdrawn motion runs on into its text"
    )
    assert motions_of_item(sync.conn, item_id), "it is still a motion on the item"
    assert votes_of_item(sync.conn, item_id) == [], "nothing was decided, so there is no vote"


# -- Check 4: a transcript vote has no tally ----------------------------------


def test_the_reading_of_a_spoken_vote_has_nowhere_to_put_a_tally() -> None:
    """What a chair said is a mention, and there is no field for a count."""
    names = {field.name for field in fields(SpokenVote)}

    assert "tally" not in names
    assert names == {"agenda_item_id", "result", "start_ms", "end_ms", "evidence"}


def test_a_vote_read_from_the_video_has_no_tally(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The whole reading, when the minutes are not there: a mention, no count."""
    meeting_id = a_meeting(sync, area)
    item_id = an_item(
        sync,
        meeting_id,
        "9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    video_id = a_video(sync, area, meeting_id)
    a_spoken_transcript(
        sync,
        storage_root,
        video_id,
        (
            ">> I make a motion to approve item 9.B, ordinance O-2026-58.",
            ">> Okay, and that carries unanimously.",
        ),
    )

    job_id = read(sync, meeting_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_LATER_SESSION}.", written=1
    )
    votes = votes_of_item(sync.conn, item_id)
    assert len(votes) == 1
    vote = votes[0]
    assert vote.source_kind == "transcript"
    assert vote.tally is None, "a transcript-only mention is never a tally"
    assert vote.evidence.startswith(FROM_VIDEO)
    assert "carries unanimously" in vote.evidence
    citation = get_citation(sync.conn, int(vote.citation_id))
    assert citation is not None
    assert citation.kind == "video"
    assert citation.start_ms == 1000
    assert citation.end_ms == 2000


def test_the_schema_refuses_a_tally_on_a_transcript_vote(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The rule is the schema's, so no caller can write one by mistake."""
    meeting_id = a_meeting(sync, area)
    item_id = an_item(sync, meeting_id, "9.B", title="An ordinance")

    with pytest.raises(sqlite3.IntegrityError):
        insert_vote(
            sync.conn,
            agenda_item_id=item_id,
            result="passed",
            source_kind="transcript",
            evidence="a line the chair said",
            tally={"yes": 7},
        )


# -- Check 5: the plain sentence when there are no minutes yet ----------------


def test_no_later_regular_session_is_listed(area: Area, wired: FakePortal, sync: Sync) -> None:
    """With no later session there is no packet that could hold the draft."""
    meeting_id = a_meeting(sync, area)

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == PAUSED
    assert sync.job(job_id)["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_LATER_SESSION}."
    )
    assert minutes_document(sync.conn, meeting_id) is None


def test_the_missing_minutes_sentence_is_the_plain_wording() -> None:
    """What a reader is told is the sentence spec 10.4 asks for, in words.

    The three tests below compare the job's reason to these constants, so a
    constant that says something else would agree with itself and pass. This
    pins the wording itself, which is a product decision and not a message
    this module is free to rephrase.
    """
    assert NOT_AVAILABLE == "not available: no official minutes document yet"
    assert FROM_VIDEO == "from video; minutes not yet available"


def test_the_queue_spells_the_reading_job_the_same_way_as_the_repo_layer() -> None:
    """The record layer names the job kind without importing the record layer.

    ``repo.motions`` reads the paused rows of the reading job to find the
    sentence saying where a meeting's minutes are expected, and it cannot import
    ``records.portal`` for the kind without a cycle. It spells the kind itself,
    so this is what stops the two spellings drifting apart.
    """
    assert READ_MINUTES_KIND == READ_MINUTES


def test_the_next_session_s_packet_is_not_there_yet(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A later session is listed and its packet has not been stored."""
    meeting_id = a_meeting(sync, area)
    a_meeting(sync, area, starts_at=SEPT_22)

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == PAUSED
    assert sync.job(job_id)["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {EXPECTED_IN.format(month_day='September 22')}."
    )
    assert minutes_document(sync.conn, meeting_id) is None


def test_the_sentence_names_a_session_only_the_upcoming_list_carried(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.4 and 10.4: the next regular session can be one nobody has held.

    Both meetings here come off the portal rather than out of the test. The
    later one is on the upcoming list and in no archived year, three weeks
    after the day of the run, and it is the session whose packet the sentence
    names. The sentence can only name it because a default-window sync stored
    it, which is the whole of this unit.
    """
    later = date.today() + timedelta(days=21)
    wired.meetings = [meeting(5001, days_from_today(-1), "City Council Regular Session")]
    wired.upcoming = [meeting(5002, days_from_today(21), "City Council Regular Session")]

    sync.queue("sync_primegov", {"source_id": area.portal_id})
    sync.lane("normal")

    earlier = meeting_by_portal_id(sync.conn, area.portal_id, 5001)
    assert earlier is not None, "the sync stored the meeting whose minutes are read"
    assert minutes_document(sync.conn, earlier.id) is None

    job_id = read(sync, earlier.id)

    assert sync.job(job_id)["state"] == PAUSED
    assert sync.job(job_id)["last_error"] == a_pause_reason(
        earlier.id, f"{NOT_AVAILABLE}; {EXPECTED_IN.format(month_day=month_day(later))}."
    ), "the expected packet is the packet of the session the upcoming list carried"


def test_the_packet_is_there_and_holds_no_minutes_of_this_meeting(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The packet was read, and this is a finding about it rather than a wait."""
    meeting_id = a_meeting(sync, area)
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    a_packet(
        sync,
        storage_root,
        area,
        follow,
        [
            (1, "AGENDA\nCity Council Regular Session\nSeptember 22, 2026\nPage 1"),
            (2, "ORDINANCE 2026-58\nPage 2"),
        ],
    )

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == PAUSED
    assert sync.job(job_id)["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_RUN_FOUND.format(month_day='September 22')}."
    )
    assert minutes_document(sync.conn, meeting_id) is None


def test_a_meeting_that_is_not_there_pauses(area: Area, wired: FakePortal, sync: Sync) -> None:
    """A reading of a meeting nobody stored says so."""
    job_id = read(sync, 999)

    assert sync.job(job_id)["state"] == PAUSED
    assert sync.job(job_id)["last_error"] == "There is no meeting 999 to read the minutes of."


# -- Reading a packet asks for the minutes it may hold ------------------------


def test_reading_a_packet_queues_the_reading_of_the_minutes_it_may_hold(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The hook of spec 9.4: a packet is read, and the earlier session is asked about.

    The packet of the September 22 session is where the September 8 draft
    minutes live, and nothing on the packet says so. The reading of the packet
    is therefore what queues the reading of the session before it.
    """
    earlier = a_meeting(sync, area)
    later = a_meeting(sync, area, starts_at=SEPT_22)
    artifact = store(
        sync.conn,
        storage_root,
        "packet",
        a_pdf_of_pages(
            [
                ["AGENDA", "City Council Regular Session", "September 22, 2026", "Page 1"],
                ["ORDINANCE 2026-58", "Page 2"],
            ]
        ),
        "pdf",
    )
    record_id = insert_record(
        sync.conn,
        meeting_id=later,
        kind=PACKET_KIND,
        artifact_id=artifact.id,
        source_id=area.portal_id,
    )

    job_id = sync.queue("extract_pages", {"record_id": record_id})
    sync.lane("heavy")

    assert sync.job(job_id)["state"] == DONE
    asked = sync.jobs_of_kind(READ_MINUTES)
    assert len(asked) == 1, "the packet's own reading queued one reading, for the session before it"
    assert asked[0]["payload"] == f'{{"meeting_id": {earlier}}}'


def test_a_scheduled_packet_reading_queues_a_scheduled_minutes_reading(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The child says who asked for the parent (spec 16.2).

    ``origin`` is how a person tells work the daily schedule started from work
    they asked for themselves, and a reading the schedule started is the same
    work all the way down: the packet reading of spec 9.4 queues the minutes
    reading, so a scheduled packet reading must not queue a manual one.
    """
    earlier = a_meeting(sync, area)
    later = a_meeting(sync, area, starts_at=SEPT_22)
    artifact = store(
        sync.conn,
        storage_root,
        "packet",
        a_pdf_of_pages(
            [
                ["AGENDA", "City Council Regular Session", "September 22, 2026", "Page 1"],
                ["ORDINANCE 2026-58", "Page 2"],
            ]
        ),
        "pdf",
    )
    record_id = insert_record(
        sync.conn,
        meeting_id=later,
        kind=PACKET_KIND,
        artifact_id=artifact.id,
        source_id=area.portal_id,
    )

    job_id = sync.queue("extract_pages", {"record_id": record_id}, origin=ORIGIN_SCHEDULED)
    sync.lane("heavy")

    assert sync.job(job_id)["state"] == DONE
    assert sync.job(job_id)["origin"] == ORIGIN_SCHEDULED
    asked = sync.jobs_of_kind(READ_MINUTES)
    assert len(asked) == 1
    assert asked[0]["origin"] == ORIGIN_SCHEDULED, "the child took after the parent"

    sync.lane("normal")

    assert sync.job(int(asked[0]["id"]))["state"] == PAUSED
    assert sync.job(int(asked[0]["id"]))["last_error"] == a_pause_reason(
        earlier, f"{NOT_AVAILABLE}; {NO_RUN_FOUND.format(month_day='September 22')}."
    )


def test_reading_a_packet_leaves_a_transcript_vote_alone(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """When the two sources disagree both are kept, and neither is picked.

    The video was read because the minutes were not there, and the minutes
    arrive later. Spec 10.4 shows both rather than overwriting the one with the
    other, so the transcript vote is still there after the second reading.
    """
    meeting_id = a_meeting(sync, area)
    item_id = an_item(
        sync,
        meeting_id,
        "9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    video_id = a_video(sync, area, meeting_id)
    a_spoken_transcript(
        sync,
        storage_root,
        video_id,
        (
            ">> I make a motion to approve item 9.B, ordinance O-2026-58.",
            ">> Okay, and that carries unanimously.",
        ),
    )
    read(sync, meeting_id)
    assert [vote.source_kind for vote in votes_of_item(sync.conn, item_id)] == ["transcript"]

    follow = a_meeting(sync, area, starts_at=SEPT_22)
    run = a_draft_run(
        [
            *HEAD,
            *a_motion_block(
                "Matthew Popkin",
                "Jake Marsing",
                "to approve O-2026-58, A Bill For An Ordinance Amending Title 2",
            ),
        ]
    )
    a_packet(sync, storage_root, area, follow, stored_pages(*run.pages))
    read(sync, meeting_id)

    votes = votes_of_item(sync.conn, item_id)
    assert [vote.source_kind for vote in votes] == ["minutes", "transcript"]
    assert votes[0].tally == {"yes": 7, "no": 0}
    assert votes[1].tally is None
    assert len(motions_of_item(sync.conn, item_id)) == 1


def test_reading_the_same_minutes_twice_writes_one_set_of_rows(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The reading is safe to re-run, which is what a later sync asks for."""
    meeting_id = a_meeting(sync, area)
    an_item(
        sync,
        meeting_id,
        "9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    run = a_draft_run(
        [
            *HEAD,
            *a_motion_block(
                "Matthew Popkin",
                "Jake Marsing",
                "to approve O-2026-58, A Bill For An Ordinance Amending Title 2",
            ),
        ]
    )
    a_packet(sync, storage_root, area, follow, stored_pages(*run.pages))

    read(sync, meeting_id)
    first = _counts(sync)
    read(sync, meeting_id)

    assert _counts(sync) == first
    assert first["motions"] == 1
    assert first["votes"] == 1
    assert first["citations"] == 1


def _counts(sync: Sync) -> dict[str, int]:
    """How many of each thing a reading writes, for a comparison of runs."""
    return {
        table: int(sync.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("minutes_documents", "motions", "motion_items", "votes", "citations")
    }


# -- The real recordings ------------------------------------------------------


@needs(AGENDA_16805, PACKET_MINUTES_SEP08)
def test_the_real_september_8_minutes_read_the_way_the_packet_holds_them(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The measured September 22, 2026 packet, read as the reading reads it.

    The recording is 22 pages of the September 22 packet, packet pages 17
    through 38, and every one of them prints both numbers: the packet's own and
    the one the draft gave it. What the reading is held to here is what it
    found: the run, the motions, the 17 votes they decide, and the consent
    motion that covers eleven items and excepts two.
    """
    meeting_id = meeting_with_real_items(sync, area)
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    reader = PdfReader(PACKET_MINUTES_SEP08)
    pages = [(17 + index, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    assert len(pages) == 22
    a_packet(
        sync,
        storage_root,
        area,
        follow,
        pages,
        data=PACKET_MINUTES_SEP08.read_bytes(),
    )

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert (checkpoint["start_page"], checkpoint["end_page"], checkpoint["page_count"]) == (
        17,
        38,
        22,
    )
    assert checkpoint["motions"] == 18
    assert checkpoint["votes"] == 17

    document = minutes_document(sync.conn, meeting_id)
    assert document is not None
    assert (document.start_page, document.end_page, document.page_count) == (17, 38, 22)
    assert document.head == " / ".join(HEAD)

    motions = motions_of_meeting(sync.conn, meeting_id)
    assert len(motions) == 18
    by_ordinal = {motion.ordinal: motion for motion in motions}
    assert [motion.ordinal for motion in motions] == sorted(by_ordinal), "ordinals are in order"

    # The consent motion of the meeting: it covers eleven items and names two.
    consent = by_ordinal[2]
    assert consent.mover == "Alex Kalkhofer"
    assert consent.seconder == "Matthew Popkin"
    assert consent.text == "to approve the Consent Agenda except items 9B and 9E"
    assert consent.outcome == f"Carried: 7 {DASH} 0"
    assert consent.tally == {"yes": 7, "no": 0}
    covered = _item_numbers(sync, consent.id)
    assert covered == set(CONSENT_COVERED)
    assert "9.B" not in covered, "the motion took 9B out of the consent agenda"
    assert "9.E" not in covered, "the motion took 9E out of the consent agenda"

    # Item 9.B was moved on seven times before it was approved. Three of those
    # motions were withdrawn, and the one the vote names is the adoption.
    nine_b = by_ordinal[9]
    assert [motion.ordinal for motion in motions if _links_to(sync, motion.id, "9.B")] == [
        3,
        4,
        5,
        6,
        7,
        8,
        9,
    ]
    assert [motion.result for motion in motions if _links_to(sync, motion.id, "9.B")].count(
        "withdrawn"
    ) == 3
    assert nine_b.result == "passed"
    assert nine_b.text.startswith("to approve O-2026-58")

    votes = _all_votes(sync, meeting_id)
    assert len(votes) == 17
    assert {vote.source_kind for vote in votes} == {"minutes"}
    assert all(vote.citation_id is not None for vote in votes)
    assert all(vote.motion_id is not None for vote in votes)
    for vote in votes:
        citation = get_citation(sync.conn, int(vote.citation_id))
        assert citation is not None
        assert 18 <= int(citation.page_number) <= 36

    # The reading is idempotent over the real bytes too.
    read(sync, meeting_id)
    assert len(motions_of_meeting(sync.conn, meeting_id)) == 18
    assert len(_all_votes(sync, meeting_id)) == 17


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_real_captions_give_mentions_with_no_tally(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """With no minutes yet, the video is read for what it mentions.

    Unit F measured six spoken motions on this meeting's captions. Seven more
    mentions come from the consent motion, which the chair moved minus items B
    and E: the two he took out keep the votes their own motions gave them, and
    every other item of the consent agenda is mentioned by the motion that
    carried it. Every one of them is a mention with no tally, including the two
    whose chair named a number out loud, because a number a chair says is not a
    count (spec 10.4).
    """
    meeting_id = meeting_with_real_items(sync, area)
    video_id = a_video(sync, area, meeting_id)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_16805.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_16805.read_bytes())

    job_id = read(sync, meeting_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_LATER_SESSION}.", written=17
    )
    votes = _all_votes(sync, meeting_id)
    assert len(votes) == 17
    assert all(vote.source_kind == "transcript" for vote in votes)
    assert all(vote.tally is None for vote in votes), "a mention is never a tally"
    assert all(vote.evidence.startswith(FROM_VIDEO) for vote in votes)
    by_number = {_number_of(sync, vote.agenda_item_id): vote for vote in votes}
    assert set(by_number) == set(CONSENT_COVERED) | {
        "9.B",
        "9.E",
        "10.A.1",
        "10.A.2",
        "10.A.3",
        "11.A",
    }
    assert by_number["9.B"].result == "unknown", "nothing there said what that motion did"
    for number in CONSENT_COVERED:
        assert "minus items B, E, B, and E" in by_number[number].evidence, number
    for number in ("9.B", "9.E"):
        assert "consent agenda" not in by_number[number].evidence.casefold(), (
            f"the chair took {number} out of the consent agenda, so the consent motion is not "
            "the motion that decided it"
        )
    for vote in votes:
        citation = get_citation(sync.conn, int(vote.citation_id))
        assert citation is not None
        assert citation.kind == "video"
        assert citation.start_ms is not None and citation.end_ms > citation.start_ms


def a_spoken_segment(start_ms: int, text: str) -> Segment:
    """One caption segment, a captioner's line long, with no speaker label set."""
    return Segment(
        id=start_ms,
        transcript_id=1,
        start_ms=start_ms,
        end_ms=start_ms + 500,
        text=text,
        speaker_label=None,
    )


def test_spoken_votes_name_the_item_and_quote_the_lines() -> None:
    """A spoken motion is read from the ask and the line that says it carried.

    The ask runs on over the seconding line, because a caption breaks a
    sentence where the captioner liked, and stops at the question (spec 10.4).
    """
    items = [
        ItemRef(id=1, number="9.B", title="", identifiers={"O-2026-58": "ordinance"}),
        ItemRef(id=2, number="10.A", title="", identifiers={}),
    ]
    asked = ">> I make a motion to approve item 9.B, ordinance O-2026-58."
    seconded = ">> Seconded by Council Member Marsing. All in favor?"
    carried = ">> That carries unanimously."
    segments = [
        a_spoken_segment(1000, asked),
        a_spoken_segment(1500, seconded),
        a_spoken_segment(2000, carried),
    ]

    votes = spoken_votes(segments, items)

    assert len(votes) == 1
    vote = votes[0]
    assert vote.agenda_item_id == 1
    assert vote.result == "passed"
    assert (vote.start_ms, vote.end_ms) == (1000, 2500), "the window ends with the outcome"
    assert vote.evidence == f'{FROM_VIDEO}: "{asked} {seconded}"; "{carried}"'


def test_a_motion_line_the_video_never_closed_is_unknown() -> None:
    """A chair who said nothing this reading understands leaves it unknown."""
    items = [ItemRef(id=1, number="9.B", title="", identifiers={"O-2026-58": "ordinance"})]
    said = ">> I make a motion to approve item 9.B, ordinance O-2026-58."
    segments = [a_spoken_segment(1000, said)]

    votes = spoken_votes(segments, items)

    assert len(votes) == 1
    assert votes[0].result == "unknown"
    assert votes[0].evidence == f'{FROM_VIDEO}: "{said}"'


# -- Check 6: what a video consent motion takes out ---------------------------

#: A chair speaking reads the item numbers of the consent agenda without the
#: section, so these are the two the September 22, 2026 chair named.
SEP22_CONSENT_REMOVED = ("9.A", "9.B")

#: The September 22, 2026 consent items the video's consent motion covers:
#: every item under "9." except the two the chair named. Both of the items he
#: named were moved on later in the same meeting, so the video holds a vote for
#: them from their own motions and no item of that meeting is left without one.
SEP22_CONSENT_COVERED = ("9.C", "9.D", "9.E", "9.F", "9.G", "9.H")


def consent_items() -> list[ItemRef]:
    """A consent section and its items, as a reading of a video is given them."""
    return [
        ItemRef(id=1, number="9.", title="Consent Agenda", identifiers={}),
        ItemRef(id=2, number="9.A", title="O-2026-62, A Bill For An Ordinance", identifiers={}),
        ItemRef(id=3, number="9.B", title="O-2026-63, A Bill For An Ordinance", identifiers={}),
        ItemRef(id=4, number="9.C", title="R-2026-68, A Resolution", identifiers={}),
        ItemRef(id=5, number="10.A", title="An ordinance", identifiers={}),
    ]


def test_a_consent_motion_s_exception_clause_reads_the_same_from_a_chair() -> None:
    """The clause the minutes print the same way is the item numbers it names.

    The exception is the whole answer: the items it names are exactly the items
    the motion is *not* a motion on, and the rest of the consent agenda is.
    """
    items = consent_items()

    coverage = consent_coverage(
        ">> I will motion to approve the consent agenda with the exception of items 9A and 9B.",
        items,
    )

    assert coverage is not None
    assert [item.number for item in coverage.excepted] == ["9.A", "9.B"]
    assert [item.number for item in coverage.covered] == ["9.C"]
    assert coverage.unreadable is False

    # A motion that names no exception covers the whole consent agenda, and one
    # that is not a consent motion at all is not read here.
    assert consent_coverage("to approve the Consent Agenda", items) == ConsentCoverage(
        covered=(items[1], items[2], items[3])
    )
    assert consent_coverage("to approve the minutes as presented", items) is None


def test_a_consent_item_number_reads_with_and_without_the_dot() -> None:
    """A chair says "9A" and the agenda prints "9.A"; both are item 9.A."""
    items = consent_items()

    for said in (
        "with the exception of items 9A and 9B",
        "with the exception of items 9.A and 9.B",
        "except items nine A and nine B",
        "minus items A and B",
    ):
        coverage = consent_coverage(
            f">> I make a motion to approve the consent agenda {said}.", items
        )
        assert coverage is not None, said
        assert [item.number for item in coverage.excepted] == ["9.A", "9.B"], said


def test_a_consent_motion_the_video_spoke_gives_no_vote_to_what_it_excepts(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The reported false fact, in miniature: 9.A is not passed by the motion
    that took 9.A out of the consent agenda.

    The September 22, 2026 chair said ">> I will motion to approve the consent
    agenda with the exception of items 9A and 9B. Second." and the stored vote
    for 9.A was that motion. Here the item it took out has no vote of its own,
    so it has no video vote, and the meeting's note names it and says why.
    """
    meeting_id = a_meeting(sync, area)
    items = {
        number: an_item(sync, meeting_id, number, title=title)
        for number, title in (
            ("9.", "Consent Agenda"),
            ("9.A", "O-2026-62, A Bill For An Ordinance Condi"),
            ("9.B", "O-2026-63, A Bill For An Ordinance Condi"),
            ("9.C", "R-2026-68, A Resolution"),
        )
    }
    video_id = a_video(sync, area, meeting_id)
    a_spoken_transcript(
        sync,
        storage_root,
        video_id,
        (
            ">> I will motion to approve the consent agenda with the exception of items 9A",
            "and 9B. Second.",
            ">> Okay. And that carries unanimously.",
            ">> I make a motion to approve ordinance O-2026-63.",
            ">> Okay. And that carries.",
        ),
    )

    job_id = read(sync, meeting_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_LATER_SESSION}.", written=2
    ) + (
        " No motion of their own was found for 9.A, which the consent agenda"
        " motion took out, so they have no vote from the video yet."
    )

    consent = votes_of_item(sync.conn, items["9.C"])
    assert len(consent) == 1
    assert consent[0].result == "passed"
    assert "exception of items 9A" in consent[0].evidence

    # 9.A was excepted and never moved on its own, so nothing stored a vote for
    # it. 9.B was excepted and was moved on, and that motion is its vote.
    assert votes_of_item(sync.conn, items["9.A"]) == []
    nine_b = votes_of_item(sync.conn, items["9.B"])
    assert len(nine_b) == 1
    assert "O-2026-63" in nine_b[0].evidence
    assert "consent agenda" not in nine_b[0].evidence.casefold()


def test_a_consent_motion_whose_exceptions_cannot_be_read_covers_nothing(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """A clause naming no item number leaves the coverage unknown, not full.

    What such a motion covers is exactly what is not known about it, so no item
    gets a vote from it and the note says so (spec 16.3).
    """
    meeting_id = a_meeting(sync, area)
    items = {
        number: an_item(sync, meeting_id, number, title=title)
        for number, title in (
            ("9.", "Consent Agenda"),
            ("9.A", "O-2026-62, A Bill For An Ordinance Condi"),
            ("9.C", "R-2026-68, A Resolution"),
        )
    }
    video_id = a_video(sync, area, meeting_id)
    a_spoken_transcript(
        sync,
        storage_root,
        video_id,
        (
            ">> I make a motion to approve the consent agenda except the ones on the sheet.",
            ">> Okay. And that carries unanimously.",
        ),
    )

    job_id = read(sync, meeting_id)

    assert votes_of_item(sync.conn, items["9.A"]) == []
    assert votes_of_item(sync.conn, items["9.C"]) == []
    assert sync.job(job_id)["last_error"] == a_pause_reason(
        meeting_id, f"{NOT_AVAILABLE}; {NO_LATER_SESSION}."
    ) + (
        " A consent-agenda motion named exceptions the reading could not read as"
        " item numbers, so it was read as a motion on no item at all."
    )


@needs(AGENDA_16821, CAPTIONS_SEP22)
def test_the_real_september_22_video_gives_each_excepted_item_its_own_motion(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The September 22, 2026 consent motion and the items it took out.

    The stored vote the coordinator found was on 9.A, from the motion that said
    "with the exception of items 9A and 9B". Both of those items were moved on
    later in the same meeting, and the vote each one has is the motion that
    decided it (spec 10.4).
    """
    meeting_id = meeting_with_real_items(sync, area, starts_at=SEPT_22, agenda=AGENDA_16821)
    video_id = a_video(sync, area, meeting_id)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_SEP22.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_SEP22.read_bytes())

    job_id = read(sync, meeting_id)

    assert sync.job(job_id)["state"] == PAUSED
    votes = _all_votes(sync, meeting_id)
    by_number = {_number_of(sync, vote.agenda_item_id): vote for vote in votes}
    assert set(by_number) == set(SEP22_CONSENT_COVERED) | set(SEP22_CONSENT_REMOVED) | {
        "10.A",
        "10.B",
        "10.C",
        "10.D",
        "10.E",
    }
    assert all(vote.tally is None for vote in votes), "a mention is never a tally"
    for number in SEP22_CONSENT_COVERED:
        assert "exception of items 9A and 9B" in by_number[number].evidence, number
    for number in SEP22_CONSENT_REMOVED:
        assert "consent agenda" not in by_number[number].evidence.casefold(), (
            f"{number} is one of the items the consent motion took out, so that motion is not the "
            "motion that decided it"
        )
    assert "2026-62" in by_number["9.A"].evidence, "9.A's own motion is what gave it a vote"

    # The two votes are minutes apart, and the order is the point: what 9.A's
    # vote rests on was said after the consent motion, not at it.
    nine_a = _citation_start(sync, by_number["9.A"])
    nine_c = _citation_start(sync, by_number["9.C"])
    assert nine_a > nine_c, "9.A's own motion came after the consent agenda motion"


def _citation_start(sync: Sync, vote: Vote) -> int:
    """The millisecond in the video one vote's citation starts at."""
    assert vote.citation_id is not None
    citation = get_citation(sync.conn, int(vote.citation_id))
    assert citation is not None
    assert citation.start_ms is not None
    return int(citation.start_ms)


def _all_votes(sync: Sync, meeting_id: int) -> list[Vote]:
    """Every vote of a meeting's items, oldest first."""
    rows = sync.conn.execute(
        "SELECT votes.* FROM votes JOIN agenda_items ON agenda_items.id = votes.agenda_item_id "
        "WHERE agenda_items.meeting_id = ? ORDER BY votes.id",
        (meeting_id,),
    ).fetchall()
    return [Vote.from_row(row) for row in rows]


def _number_of(sync: Sync, agenda_item_id: int) -> str:
    """The number of one agenda item."""
    row = sync.conn.execute(
        "SELECT number FROM agenda_items WHERE id = ?", (agenda_item_id,)
    ).fetchone()
    assert row is not None
    return str(row["number"])


def _item_numbers(sync: Sync, motion_id: int) -> set[str]:
    """The numbers of the items one motion is a motion on."""
    return {_number_of(sync, link.agenda_item_id) for link in motion_items_of(sync.conn, motion_id)}


def _links_to(sync: Sync, motion_id: int, number: str) -> bool:
    """Whether one motion is a motion on the item with that number."""
    return number in _item_numbers(sync, motion_id)
