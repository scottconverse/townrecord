"""The evidence a model gets, and the check on what it wrote (spec 11.7).

The check is code and never a model, so a fault is a value a test reads rather
than an opinion an answer has to be judged for. Every model in this file is a
fake: :class:`FakeAsk` is a repair that answers from a list the test wrote, and
the ladder test hands :class:`~townrecord.ai.grounding.LadderAsk` a
``model_call`` that records what it was asked and answers with an
:class:`~townrecord.ai.failover.Attempt`. Nothing here reaches a model or the
network (PROJECT-BRIEF rule 9).

The class at the end of the file reads the recorded September 8, 2026 minutes
out of the oversight repository in place and is skipped when the recordings are
not on this machine. Every one of its tests carries ``@needs(...)``. The meeting
the other tests build is stored in the database here, so the rules hold on a
machine with no recordings at all.

The eight checks the unit was asked for are, in the order of the brief: a
sentence with no handle is removed; a handle not in the pack fails; a quote
with one changed word fails; "7-0" with no stored vote fails; a transcript vote
with a tally fails; a repair that changes a number is rejected; "Not found"
passes; and exactly one repair round is asked for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from pypdf import PdfReader

from townrecord.ai.failover import REASON_UNAVAILABLE, Attempt
from townrecord.ai.grounding import (
    CODE_EMPTY,
    CODE_NAME,
    CODE_NO_EVIDENCE,
    CODE_NO_HANDLE,
    CODE_NO_REPAIR,
    CODE_NOT_FOUND,
    CODE_NUMBER,
    CODE_OK,
    CODE_QUOTE,
    CODE_REPAIR_CHANGED,
    CODE_TALLY,
    CODE_TALLY_MISMATCH,
    CODE_UNKNOWN_HANDLE,
    KIND_RECORD,
    KIND_VIDEO,
    KIND_VOTE,
    OUTCOME_KEPT,
    OUTCOME_REMOVED,
    OUTCOME_REPAIRED,
    EvidencePack,
    Facts,
    LadderAsk,
    RepairAnswer,
    RepairRequest,
    Roster,
    Span,
    Verdict,
    changed_facts,
    check_answer,
    check_sentence,
    evidence_pack,
    facts,
    ground,
    handles_in,
    is_not_found,
    normalize,
    quotes_in,
    render_ms,
    split_sentences,
    tallies_in,
    without_handles,
)
from townrecord.ai.ladder import Ladder, Rung
from townrecord.ai.preflight import Checks
from townrecord.ai.providers import Provider, ProviderRegistry
from townrecord.artifacts import store
from townrecord.repo import (
    insert_agenda_item,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_record,
    insert_record_page,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    insert_vote,
    upsert_person,
    upsert_seat,
)

from .conftest import CLAUDE_NAME, CLOUD_NAME, PACKET_MINUTES_SEP08, needs

#: The en dash the minutes print between the two numbers of a count (U+2013).
DASH = "–"

#: A non-breaking space (U+00A0). The real pages are full of them.
NBSP = " "

#: The counted result the September 8, 2026 minutes print on a carried motion.
OUTCOME = f"Carried: 7 {DASH} 0"

#: The council the fixture minutes were voted by (spec 10.4).
COUNCIL = (
    "Susie Hidalgo-Fahring",
    "Sean McCoy",
    "Diane Crist",
    "Alex Kalkhofer",
    "Jake Marsing",
    "Matthew Popkin",
    "Crystal Prieto",
)

#: The seats of that council (spec 6.2, spec 10.6). "Mayor" and "Mayor Pro
#: Tem" name one office each and so name one person; the other five hold the
#: rank "Council Member", which every member holds and so names nobody. The
#: database is where the name check reads a person from (S2).
OFFICES = {
    "Susie Hidalgo-Fahring": "Mayor",
    "Sean McCoy": "Mayor Pro Tem",
}
COUNCIL_SEAT = "Council Member"

#: The consent motion of that meeting, as the minutes print it.
CONSENT_TEXT = "to approve the Consent Agenda except items 9B and 9E"

#: The line the video says about the consent motion. It is one timed line of a
#: transcript, which is all a segment is (spec 10.2).
SPOKEN = f"Council Member Kalkhofer moved, seconded by Council Member McCoy, {CONSENT_TEXT}."


# -- Building the meeting the checker is run against --------------------------


def a_printed_motion(
    mover: str,
    seconder: str,
    text: str,
    *,
    result: str = OUTCOME,
    approved: tuple[str, ...] = COUNCIL,
) -> str:
    """One counted motion, printed the way the minutes print it."""
    names = ", ".join(approved) if approved else "None"
    return "\n".join(
        [
            "MOTION",
            f"{mover} moved, seconded by {seconder}, {text}",
            "Approved:",
            names,
            "Dissented:",
            "None",
            "Abstained:",
            "None",
            result,
        ]
    )


@dataclass(frozen=True)
class Stored:
    """The rows the pack is built from, and the ids a test needs."""

    body: int
    meeting: int
    video: int
    transcript: int
    record: int
    item: int
    consent: int


@pytest.fixture
def stored(conn: sqlite3.Connection, tmp_path: Path) -> Stored:
    """One meeting with a video, a record and two items with stored votes.

    The rows are stored through the real repository functions and the real
    artifact store, so the pack is built from the rows the running system would
    have rather than from a table written by the test.
    """
    city = insert_jurisdiction(
        conn, type="city", name="Longmont", official_id="0845970", official_id_kind="geoid"
    )
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    portal = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="meeting_portal",
        origin="https://portal.test.invalid",
        suggested_by="discovery",
        reason="The city's meeting page links to it.",
        status="accepted",
    )
    channel = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-test",
        suggested_by="discovery",
        reason="The portal lists this channel for its recordings.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        starts_at="2026-09-08T19:00:00-06:00",
        title="City Council Regular Session",
    )
    # The council is seated on the body before the meeting (spec 6.2). The pack
    # reads its roster from these rows, so the name check holds on rows the
    # running system would have rather than on a list the test handed it.
    for name in COUNCIL:
        upsert_seat(
            conn,
            person_id=upsert_person(conn, name=name),
            body_id=body,
            title=OFFICES.get(name, COUNCIL_SEAT),
            on="2026-01-01",
        )
    video = insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="3qfQAkAAC9U",
        title="City Council Regular Session",
        duration_s=14407,
        is_primary=True,
    )
    root = tmp_path / "storage"
    root.mkdir()
    transcript_artifact = store(conn, root, "transcript", b"WEBVTT\n\n", "vtt").id
    transcript = insert_transcript(
        conn, video_id=video, artifact_id=transcript_artifact, origin="publisher_captions"
    )
    insert_segment(
        conn, transcript_id=transcript, start_ms=1_065_000, end_ms=1_069_000, text=SPOKEN
    )
    insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=1_070_000,
        end_ms=1_074_000,
        text="The motion carried on a vote of seven to nothing.",
    )
    packet_artifact = store(
        conn, root, "packet", b"%PDF-1.4\n% the packet of the next session\n", "pdf"
    ).id
    record = insert_record(
        conn,
        meeting_id=meeting,
        kind="packet",
        artifact_id=packet_artifact,
        source_id=portal,
        page_count=22,
    )
    insert_record_page(
        conn,
        record_id=record,
        page_number=19,
        footer_page_number=19,
        text=a_printed_motion("Alex Kalkhofer", "Matthew Popkin", CONSENT_TEXT),
    )
    item = insert_agenda_item(conn, meeting_id=meeting, number="9.B", title="An ordinance")
    consent = insert_agenda_item(conn, meeting_id=meeting, number="9.A", title="The consent agenda")
    insert_vote(
        conn,
        agenda_item_id=consent,
        result="passed",
        source_kind="minutes",
        evidence=a_printed_motion("Alex Kalkhofer", "Matthew Popkin", CONSENT_TEXT),
        tally={"yes": 7, "no": 0},
    )
    insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="transcript",
        evidence=f'From the video: "{SPOKEN}"',
    )
    return Stored(
        body=body,
        meeting=meeting,
        video=video,
        transcript=transcript,
        record=record,
        item=item,
        consent=consent,
    )


@pytest.fixture
def pack(conn: sqlite3.Connection, stored: Stored) -> EvidencePack:
    """The evidence pack of the stored meeting, as the code builds it."""
    return evidence_pack(conn, meeting_id=stored.meeting)


def a_handle(pack: EvidencePack, kind: str, *, source_kind: str = "", nth: int = 0) -> str:
    """The handle of a span of this kind, so a test never hardcodes one."""
    seen = 0
    for span in pack:
        if span.kind == kind and (not source_kind or span.source_kind == source_kind):
            if seen == nth:
                return span.handle
            seen += 1
    raise AssertionError(f"the pack holds no {kind} span at {nth}")


class FakeAsk:
    """A model that is a fake in every test (PROJECT-BRIEF rule 9).

    It answers from a list the test wrote, in order, and records every request
    so a test can say how many repairs were asked for and what the model was
    told about each one.
    """

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[RepairRequest] = []

    def __call__(self, request: RepairRequest) -> RepairAnswer:
        self.requests.append(request)
        if not self.answers:
            return RepairAnswer(reason="the fake had nothing more to say")
        return RepairAnswer(text=self.answers.pop(0))


# -- The pack -----------------------------------------------------------------


class TestTheEvidencePack:
    """The only evidence a model is given (spec 11.7, 10.5)."""

    def test_the_pack_is_the_video_the_pages_and_the_votes(self, pack: EvidencePack) -> None:
        kinds = [span.kind for span in pack]
        assert kinds == [KIND_VIDEO, KIND_VIDEO, KIND_RECORD, KIND_VOTE, KIND_VOTE]
        assert "1 record, 2 video, 2 vote" in pack.sentence()

    def test_every_handle_is_in_the_pack_and_numbered_from_one(self, pack: EvidencePack) -> None:
        assert pack.handles() == ("[E1]", "[E2]", "[E3]", "[E4]", "[E5]")
        assert "[E1]" in pack
        assert "[E9]" not in pack
        assert pack.get("[E9]") is None
        assert pack.get("[E3]") is not None

    def test_a_page_span_carries_its_page_number_and_its_hash(
        self, conn: sqlite3.Connection, pack: EvidencePack
    ) -> None:
        span = pack.get(a_handle(pack, KIND_RECORD))
        assert span is not None
        assert span.page_number == 19
        assert span.artifact_id is not None
        # The hash is read from the artifact row, never copied out of it.
        row = conn.execute(
            "SELECT sha256, kind FROM artifacts WHERE id = ?", (span.artifact_id,)
        ).fetchone()
        assert span.sha256 == row["sha256"]
        assert span.sha256 and row["kind"] == "packet"
        assert "page 19" in span.cite() and span.sha256 in span.cite()

    def test_a_video_span_carries_the_times_the_hash_and_the_origin(
        self, conn: sqlite3.Connection, pack: EvidencePack
    ) -> None:
        span = pack.get(a_handle(pack, KIND_VIDEO))
        assert span is not None
        assert (span.start_ms, span.end_ms) == (1_065_000, 1_069_000)
        assert (render_ms(span.start_ms), render_ms(span.end_ms)) == ("00:17:45", "00:17:49")
        assert "00:17:45 to 00:17:49" in span.label
        assert span.origin == "publisher_captions"
        row = conn.execute(
            "SELECT sha256 FROM artifacts WHERE id = ?", (span.artifact_id,)
        ).fetchone()
        assert span.sha256 == row["sha256"]
        assert span.origin in span.cite() and span.sha256 in span.cite()

    def test_a_vote_span_carries_the_stored_count_and_never_invents_one(
        self, pack: EvidencePack
    ) -> None:
        minutes_vote = pack.get(a_handle(pack, KIND_VOTE, source_kind="minutes"))
        assert minutes_vote is not None
        assert minutes_vote.tally == {"yes": 7, "no": 0}
        assert minutes_vote.result == "passed"
        assert "yes: 7" in minutes_vote.text and "no: 0" in minutes_vote.text
        assert OUTCOME in minutes_vote.text

        transcript_vote = pack.get(a_handle(pack, KIND_VOTE, source_kind="transcript"))
        assert transcript_vote is not None
        assert transcript_vote.tally is None
        assert "yes" not in transcript_vote.text, "a transcript mention is never a tally"

    def test_the_prompt_shows_every_handle_and_its_citation(self, pack: EvidencePack) -> None:
        prompt = pack.prompt()
        for handle in pack.handles():
            assert handle in prompt
        assert "sha256" in prompt
        assert "must cite at least one of these handles" in prompt

    def test_a_limit_reports_what_it_left_out(
        self, conn: sqlite3.Connection, stored: Stored
    ) -> None:
        small = evidence_pack(conn, meeting_id=stored.meeting, limit=2)
        assert len(small) == 2
        assert small.dropped == 3
        assert "left out 3 more" in small.sentence()

    def test_a_meeting_with_nothing_stored_has_an_empty_pack(
        self, conn: sqlite3.Connection, stored: Stored
    ) -> None:
        other = insert_meeting(conn, body_id=stored.body, starts_at="2026-10-06T19:00:00-06:00")
        empty = evidence_pack(conn, meeting_id=other)
        assert len(empty) == 0
        assert empty.dropped == 0
        assert empty.sentence() == "The evidence pack is empty, so nothing can be cited."
        assert check_sentence("The council approved it [E1].", empty).code == CODE_NO_EVIDENCE
        # "Not found" claims nothing, so an empty pack does not make it fail.
        assert check_sentence("Not found.", empty).ok is True


# -- The check ----------------------------------------------------------------


class TestTheHandlesASentenceCites:
    """Every sentence cites the evidence it rests on (spec 11.7)."""

    def test_a_sentence_with_no_handle_is_removed(self, pack: EvidencePack) -> None:
        verdict = check_sentence("The council approved the consent agenda.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_NO_HANDLE
        assert verdict.handles == ()

        result = ground("The council approved the consent agenda.", pack)
        assert result.kept() == ()
        assert [decision.outcome for decision in result.decisions] == [OUTCOME_REMOVED]
        assert result.decisions[0].code == CODE_NO_HANDLE
        assert result.before == {"sentences": 1, "passed": 0, "failed": 1}
        assert result.after == {"kept": 0, "repaired": 0, "removed": 1}

    def test_a_handle_not_in_the_pack_fails(self, pack: EvidencePack) -> None:
        verdict = check_sentence("The council approved the consent agenda [E9].", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_UNKNOWN_HANDLE
        assert verdict.handles == ("[E9]",)
        assert "[E9]" in verdict.reason

    def test_a_sentence_that_cites_a_span_it_has_passes(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        verdict = check_sentence(f"The council approved the consent agenda {handle}.", pack)
        assert verdict.ok is True
        assert verdict.code == CODE_OK
        assert verdict.handles == (handle,)
        assert [span.handle for span in verdict.spans] == [handle]

    def test_an_empty_sentence_is_not_an_answer(self, pack: EvidencePack) -> None:
        assert check_sentence("   ", pack).code == CODE_EMPTY
        assert split_sentences("  \n ") == ()

    def test_the_handle_is_not_part_of_what_the_sentence_claims(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        # ``[E1]`` holds a digit, and that digit is not a number the sentence
        # claimed: reading it as one would fail every sentence citing span one.
        assert check_sentence(f"The council approved it {handle}.", pack).ok is True
        assert without_handles(f"approved {handle}.") == "approved  ."
        assert handles_in("[E3] then [E1] then [E3]") == ("[E3]", "[E1]")


class TestTheQuotesInASentence:
    """Every quote appears verbatim in the cited evidence (spec 11.7)."""

    def test_a_quote_with_one_changed_word_fails(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        verbatim = f'The minutes say "{CONSENT_TEXT}" {handle}.'
        assert check_sentence(verbatim, pack).ok is True

        # One word changed: "items" becomes "item", and the quote is no longer
        # what the record prints.
        changed = f'The minutes say "to approve the Consent Agenda except item 9B and 9E" {handle}.'
        verdict = check_sentence(changed, pack)
        assert verdict.ok is False
        assert verdict.code == CODE_QUOTE
        assert "word for word" in verdict.reason

    def test_a_quote_of_the_whole_printed_motion_passes(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        printed = a_printed_motion("Alex Kalkhofer", "Matthew Popkin", CONSENT_TEXT)
        verdict = check_sentence(f'The minutes print "{printed}" {handle}.', pack)
        assert verdict.ok is True

    def test_a_quote_must_be_in_the_evidence_the_sentence_cites(self, pack: EvidencePack) -> None:
        page = a_handle(pack, KIND_RECORD)
        spoken = a_handle(pack, KIND_VIDEO)
        # The quote is in the video and not in the page, and the sentence cites
        # the page.
        verdict = check_sentence(f'The video says "{SPOKEN}" {page}.', pack)
        assert verdict.ok is False
        assert verdict.code == CODE_QUOTE
        assert check_sentence(f'The video says "{SPOKEN}" {spoken}.', pack).ok is True

    def test_only_whitespace_and_the_quote_marks_are_normalized(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        # The record prints non-breaking spaces where a printed page had one,
        # and a curly quote where the minutes printed one.
        assert normalize(f"Carried: 7 {NBSP} {DASH} 0") == f"Carried: 7 {DASH} 0"
        assert normalize("“carried”") == '"carried"'
        curly = f"The minutes print “{OUTCOME}” {handle}."
        assert check_sentence(curly, pack).ok is True
        # The same quote twice, once straight and once curly, is one quote.
        assert quotes_in(f'It prints "{OUTCOME}" and “{OUTCOME}” again.') == (OUTCOME,)
        # Case is not normalized: the printed line is what it is.
        wrong_case = f'The minutes print "carried: 7 {DASH} 0" {handle}.'
        assert check_sentence(wrong_case, pack).code == CODE_QUOTE


class TestTheNumbersInASentence:
    """Every number appears in the cited evidence (spec 11.7)."""

    def test_a_number_in_the_cited_evidence_passes(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        assert check_sentence(f"The motion covered 9.B {handle}.", pack).ok is True

    def test_a_number_not_in_the_cited_evidence_fails(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        verdict = check_sentence(f"The motion covered 9.H and 12 other items {handle}.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_NUMBER
        assert "12" in verdict.reason

    def test_a_number_the_record_never_says_fails(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        verdict = check_sentence(f"The meeting began at 1900 hours {handle}.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_NUMBER

    def test_a_number_is_read_as_digits_and_not_as_an_item_label(self, pack: EvidencePack) -> None:
        # The record prints "9B" and "9E". The digits of an item number are the
        # number 9, so a sentence that says 9 is grounded and one that says 12
        # is not: the check compares digits, not labels.
        handle = a_handle(pack, KIND_RECORD)
        assert check_sentence(f"The motion excepts item 9B {handle}.", pack).ok is True
        assert check_sentence(f"The motion excepts item 12 {handle}.", pack).code == CODE_NUMBER

    def test_a_spelled_number_is_the_number_it_names(self, pack: EvidencePack) -> None:
        # The second line of the video says "seven to nothing", and says no
        # digit at all.
        handle = a_handle(pack, KIND_VIDEO, nth=1)
        assert check_sentence(f"The motion carried seven to nothing {handle}.", pack).ok is True
        failed = check_sentence(f"The motion carried eight to nothing {handle}.", pack)
        assert failed.ok is False
        assert failed.code == CODE_NUMBER

    def test_the_thousands_separator_is_not_part_of_a_number(self) -> None:
        span = Span(
            handle="[E1]", kind=KIND_RECORD, text="the sum of $1,200 was approved", label="a page"
        )
        pack = EvidencePack(spans=(span,))
        assert check_sentence("The sum was $1200 [E1].", pack).ok is True
        assert check_sentence("The sum was $1,300 [E1].", pack).code == CODE_NUMBER


class TestTheNameInASentence:
    """A name is a fact like a number (spec 11.7 with spec 10.6, unit S2).

    A sentence that names a person must find that person in its cited spans.
    The people are read from the database — the seated council of the meeting —
    and never from the sentence, so a name the database does not know is not
    read as a person at all. Both directions are pinned below, because that
    answer is the one this check gives on purpose.
    """

    #: The mover line of the printed motion with no vote list under it: this
    #: page names Kalkhofer and Popkin and nobody else. It is the page the
    #: coordinator's check calls "a Kalkhofer page".
    MOVER_LINE = (
        "Alex Kalkhofer moved, seconded by Matthew Popkin, to approve the "
        "Consent Agenda except items 9B and 9E."
    )

    @pytest.fixture
    def roster(self, conn: sqlite3.Connection, stored: Stored) -> Roster:
        """Who the database knows at that meeting, as the pack builds it (S2)."""
        return evidence_pack(conn, meeting_id=stored.meeting).roster

    @staticmethod
    def a_pack(*texts: str, roster: Roster) -> EvidencePack:
        """A pack of pages carrying the roster the name check reads."""
        return EvidencePack(
            spans=tuple(
                Span(handle=f"[E{index}]", kind=KIND_RECORD, text=text, label="a page")
                for index, text in enumerate(texts, start=1)
            ),
            roster=roster,
        )

    def test_the_roster_is_the_council_the_database_knows(self, roster: Roster) -> None:
        surnames = {official.surname for official in roster.officials}
        assert surnames == {name.split()[-1] for name in COUNCIL}
        # The two offices name one person each; the rank every member holds
        # names nobody and is not a title the check can stand on.
        assert set(roster.holders) == {"mayor", "mayor pro tem"}
        assert roster.holders["mayor pro tem"].name == "Sean McCoy"

    def test_a_name_the_cited_page_does_not_print_fails(self, roster: Roster) -> None:
        pack = self.a_pack(self.MOVER_LINE, roster=roster)
        verdict = check_sentence("Council Member McCoy moved it [E1].", pack)

        assert verdict.ok is False
        assert verdict.code == CODE_NAME
        assert "Sean McCoy" in verdict.reason

    def test_a_name_the_cited_page_prints_passes(self, roster: Roster) -> None:
        pack = self.a_pack(self.MOVER_LINE, roster=roster)
        assert check_sentence("Council Member Kalkhofer moved it [E1].", pack).ok is True
        # The title is not needed for the reading: the surname is the claim.
        assert check_sentence("Kalkhofer moved it [E1].", pack).ok is True

    def test_a_title_and_a_surname_stand_on_the_span_that_names_the_person(
        self, roster: Roster
    ) -> None:
        # The recorded line of the video names McCoy as the seconder.
        named = self.a_pack(SPOKEN, roster=roster)
        assert check_sentence("Mayor Pro Tem McCoy moved it [E1].", named).ok is True

        omitted = self.a_pack(self.MOVER_LINE, roster=roster)
        wrong = check_sentence("Mayor Pro Tem McCoy moved it [E1].", omitted)
        assert wrong.ok is False
        assert wrong.code == CODE_NAME
        # The title names McCoy's seat and not the Mayor's: "Mayor" is a word
        # of "Mayor Pro Tem", and the sentence claims the one person it prints.
        assert wrong.reason == "The sentence names Sean McCoy, and the cited evidence does not."

    def test_a_title_does_not_stand_on_the_seat_of_a_surname_after_it(self, roster: Roster) -> None:
        # "Mayor Marsing" is the titled form of one person, read by surname
        # like any other: it does not also claim the seat of the Mayor, so a
        # page that prints Marsing carries the sentence.
        page = self.a_pack("Jake Marsing asked about the water rate.", roster=roster)
        assert check_sentence("Mayor Marsing asked about it [E1].", page).ok is True

        # The title with no surname after it is the other reading: now the
        # sentence does claim the seat, and this page does not carry it.
        wrong = check_sentence("The Mayor asked about it [E1].", page)
        assert wrong.ok is False
        assert wrong.code == CODE_NAME
        assert wrong.reason == (
            "The sentence names Susie Hidalgo-Fahring, and the cited evidence does not."
        )

    def test_a_title_alone_stands_on_the_holder_of_that_seat(self, roster: Roster) -> None:
        # "the Mayor" is Hidalgo-Fahring's seat on this date, so the sentence
        # stands on a span that names her or that prints the title.
        named = self.a_pack("Mayor Hidalgo-Fahring read the title of the ordinance.", roster=roster)
        assert check_sentence("The Mayor read the title [E1].", named).ok is True

        omitted = self.a_pack(self.MOVER_LINE, roster=roster)
        wrong = check_sentence("The Mayor read the title [E1].", omitted)
        assert wrong.ok is False
        assert wrong.code == CODE_NAME
        assert "Susie Hidalgo-Fahring" in wrong.reason

    def test_a_title_with_no_known_holder_claims_nobody(self) -> None:
        # No seat, so no holder, so no person: the same answer spec 10.6 gives
        # an unidentified speaker.
        pack = self.a_pack("The City Manager introduced the item.", roster=Roster())
        assert check_sentence("The City Manager introduced the item [E1].", pack).ok is True

    def test_a_name_the_database_does_not_know_is_not_read_as_a_person(
        self, roster: Roster
    ) -> None:
        # Nobody called Smith is in the roster, so the check does not make a
        # person out of the name and says nothing about it. This is the answer
        # the fix round asked to be pinned: a name is read from what the
        # database knows, never guessed at from a shape.
        assert all(official.surname != "Smith" for official in roster.officials)
        pack = self.a_pack(self.MOVER_LINE, roster=roster)
        assert check_sentence("Council Member Smith moved it [E1].", pack).ok is True

    def test_a_pack_with_no_roster_reads_no_name(self) -> None:
        # A pack built by hand carries no roster, and then there is nobody the
        # check can name: it says nothing rather than failing every surname.
        pack = EvidencePack(
            spans=(
                Span(
                    handle="[E1]",
                    kind=KIND_RECORD,
                    text=self.MOVER_LINE,
                    label="a page",
                ),
            )
        )
        assert check_sentence("Council Member McCoy moved it [E1].", pack).ok is True


class TestTheTallyOfAVote:
    """A count comes from a stored vote (spec 10.4, 11.7)."""

    def test_a_tally_with_no_stored_vote_fails(self, pack: EvidencePack) -> None:
        video = a_handle(pack, KIND_VIDEO)
        verdict = check_sentence(f"The council approved it 7-0 {video}.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_TALLY
        assert "never a tally" in verdict.reason

    def test_a_tally_that_is_the_stored_vote_passes(self, pack: EvidencePack) -> None:
        minutes = a_handle(pack, KIND_VOTE, source_kind="minutes")
        assert check_sentence(f"The council approved it 7-0 {minutes}.", pack).ok is True
        assert check_sentence(f"The council approved it 7 {DASH} 0 {minutes}.", pack).ok is True

    def test_a_tally_that_is_not_the_stored_vote_fails(self, pack: EvidencePack) -> None:
        minutes = a_handle(pack, KIND_VOTE, source_kind="minutes")
        verdict = check_sentence(f"The council approved it 7-1 {minutes}.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_TALLY_MISMATCH
        assert "7-1" in verdict.reason

    def test_a_transcript_vote_with_a_tally_fails(self, pack: EvidencePack) -> None:
        """The video's mention of the motion is not a count (spec 10.4)."""
        video_vote = a_handle(pack, KIND_VOTE, source_kind="transcript")
        verdict = check_sentence(f"The council approved it 7-0 {video_vote}.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_TALLY
        assert "transcript is never a tally" in verdict.reason

    def test_the_database_refuses_a_transcript_vote_with_a_tally(
        self, conn: sqlite3.Connection, stored: Stored
    ) -> None:
        """The rule is stored as a constraint as well as checked here."""
        with pytest.raises(sqlite3.IntegrityError):
            insert_vote(
                conn,
                agenda_item_id=stored.consent,
                result="passed",
                source_kind="transcript",
                evidence="the video said so",
                tally={"yes": 7, "no": 0},
            )
        # The same row without a tally is stored, so what was refused is the
        # tally and not the vote (the schema's CHECK, not a unique key).
        insert_vote(
            conn,
            agenda_item_id=stored.consent,
            result="passed",
            source_kind="transcript",
            evidence="the video said so",
        )

    def test_an_ordinance_number_is_not_a_count(self) -> None:
        assert tallies_in("O-2026-58 was approved") == ()
        assert tallies_in("on 9-8-2026") == ()
        assert tallies_in(f"Carried: 7 {DASH} 0") == ((7, 0),)
        assert tallies_in("the vote was 7 - 0") == ((7, 0),)
        span = Span(
            handle="[E1]", kind=KIND_RECORD, text="ORDINANCE 2026-58 was read", label="a page"
        )
        pack = EvidencePack(spans=(span,))
        # The ordinance number is read as two numbers and both are in the
        # evidence, which is the whole of what this sentence claims.
        assert check_sentence("Ordinance O-2026-58 was read [E1].", pack).ok is True


class TestNotFound:
    """ "Not found" is a valid answer and claims nothing (spec 11.7, 12.9)."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "Not found.",
            "Not found in the record.",
            "No record found.",
            "Record not available yet.",
        ],
    )
    def test_a_not_found_answer_passes(self, pack: EvidencePack, sentence: str) -> None:
        verdict = check_sentence(sentence, pack)
        assert verdict.ok is True
        assert verdict.code == CODE_NOT_FOUND
        assert verdict.handles == ()

    def test_a_not_found_sentence_is_kept_and_needs_no_repair(self, pack: EvidencePack) -> None:
        ask = FakeAsk()
        result = ground("Not found.", pack, ask=ask)
        assert result.kept() == ("Not found.",)
        assert ask.requests == []
        assert result.calls == 0
        assert result.after == {"kept": 1, "repaired": 0, "removed": 0}

    def test_a_claim_that_only_looks_like_not_found_is_still_checked(
        self, pack: EvidencePack
    ) -> None:
        verdict = check_sentence("The council did not find the record of it.", pack)
        assert verdict.ok is False
        assert verdict.code == CODE_NO_HANDLE
        assert is_not_found("Not found.") is True
        assert is_not_found("The council found nothing about it.") is False


# -- The repair loop ----------------------------------------------------------


class TestTheRepairRound:
    """One round of repair, and what a repair may not do (spec 11.7)."""

    def test_exactly_one_repair_round_is_asked_for(self, pack: EvidencePack) -> None:
        still_no_handle = "The council approved the consent agenda."
        ask = FakeAsk(still_no_handle)
        result = ground("The council approved it.", pack, ask=ask)

        assert len(ask.requests) == 1, "a failed sentence is sent back once, not twice"
        assert result.calls == 1
        assert [decision.outcome for decision in result.decisions] == [OUTCOME_REMOVED]
        assert result.decisions[0].code == CODE_NO_HANDLE
        assert "one repair round still failed" in result.decisions[0].reason
        assert result.kept() == ()

    def test_a_sentence_that_passes_is_not_sent_back(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        ask = FakeAsk()
        result = ground(f"The council approved it {handle}.", pack, ask=ask)
        assert ask.requests == []
        assert result.calls == 0
        assert result.decisions[0].outcome == OUTCOME_KEPT

    def test_a_repair_that_fixes_the_fault_is_kept(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        repaired = f"The minutes record the consent motion {handle}."
        ask = FakeAsk(repaired)
        result = ground("The minutes record the motion.", pack, ask=ask)

        assert result.calls == 1
        assert result.before == {"sentences": 1, "passed": 0, "failed": 1}
        assert result.after == {"kept": 1, "repaired": 1, "removed": 0}
        assert result.decisions[0].outcome == OUTCOME_REPAIRED
        assert result.kept() == (repaired,)
        assert result.answer() == repaired

    def test_a_repair_that_adds_the_handle_it_lacked_is_kept(self, pack: EvidencePack) -> None:
        # The commonest repair there is. A handle is not a fact the sentence
        # claimed, so adding one changes nothing a repair may not change.
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        ask = FakeAsk(f"The council approved the consent agenda {handle}.")
        result = ground("The council approved the consent agenda.", pack, ask=ask)
        assert result.decisions[0].outcome == OUTCOME_REPAIRED

    def test_a_repair_that_changes_a_number_is_rejected(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f"The packet has 23 pages {handle}.")
        result = ground(f"The packet has 22 pages {handle}.", pack, ask=ask)

        decision = result.decisions[0]
        assert decision.outcome == OUTCOME_REMOVED
        assert decision.code == CODE_REPAIR_CHANGED
        assert "the number '22' is gone" in decision.reason
        assert "a repair never changes" in decision.reason
        # The sentence is removed as the model wrote it, never edited here.
        assert decision.text() == f"The packet has 22 pages {handle}."
        assert result.kept() == ()

    def test_a_repair_that_changes_a_quote_is_rejected(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f'The minutes say "to approve the Consent Agenda except item 9B" {handle}.')
        wrong = 'The minutes say "to approve the Consent Agenda except item 9B and 9E"'
        result = ground(f"{wrong} {handle}.", pack, ask=ask)

        decision = result.decisions[0]
        assert decision.outcome == OUTCOME_REMOVED
        assert decision.code == CODE_REPAIR_CHANGED
        assert "quote" in decision.reason

    def test_a_repair_that_changes_a_name_is_rejected(self, pack: EvidencePack) -> None:
        # The handle a sentence lacks is asked about before a name is read, so
        # this original is sent back for the handle and the mover it names is
        # what refuses the repair. The check reads a name against the evidence
        # too (:class:`TestTheNameInASentence`); a repair is refused whichever
        # rule the original broke.
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f"Council Member Kalkhofer moved it {handle}.")
        result = ground("Council Member McCoy moved it.", pack, ask=ask)

        decision = result.decisions[0]
        assert decision.outcome == OUTCOME_REMOVED
        assert decision.code == CODE_REPAIR_CHANGED
        assert "name" in decision.reason

    def test_a_repair_that_changes_a_url_is_rejected(self) -> None:
        span = Span(
            handle="[E1]",
            kind=KIND_RECORD,
            text="the agenda is at the city's own site",
            label="a page",
        )
        pack = EvidencePack(spans=(span,))
        ask = FakeAsk("See https://portal.test.invalid/b [E1].")
        result = ground("See https://portal.test.invalid/a [E9].", pack, ask=ask)
        assert result.decisions[0].code == CODE_REPAIR_CHANGED
        assert "URL" in result.decisions[0].reason

    def test_a_repair_that_only_respells_a_number_is_not_a_change(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f"The minutes approved it 9 to nothing {handle}.")
        result = ground("The minutes approved it nine to nothing [E9].", pack, ask=ask)
        # The repair only respelled the number, so nothing a repair may not
        # touch was touched, and the repaired sentence is checked on its merits.
        assert result.decisions[0].outcome == OUTCOME_REPAIRED
        before = facts("it carried nine to nothing")
        after = facts("it carried 9 to nothing")
        assert changed_facts(before, after) == ()

    def test_a_repair_that_adds_a_number_is_rejected(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f"The motion excepts item 12 and item 13 {handle}.")
        result = ground(f"The motion excepts item 12 {handle}.", pack, ask=ask)
        assert result.decisions[0].code == CODE_REPAIR_CHANGED
        assert "was added" in result.decisions[0].reason

    def test_a_model_that_says_nothing_removes_the_sentence(self, pack: EvidencePack) -> None:
        ask = FakeAsk()
        result = ground("The council approved the consent agenda.", pack, ask=ask)
        decision = result.decisions[0]
        assert decision.outcome == OUTCOME_REMOVED
        assert decision.code == CODE_NO_REPAIR
        assert decision.reason == "the fake had nothing more to say"

    def test_a_repair_that_still_cites_an_unknown_handle_is_removed(
        self, pack: EvidencePack
    ) -> None:
        ask = FakeAsk("The council approved the consent agenda [E9].")
        result = ground("The council approved the consent agenda.", pack, ask=ask)
        assert result.decisions[0].outcome == OUTCOME_REMOVED
        assert result.decisions[0].code == CODE_UNKNOWN_HANDLE

    def test_a_repair_that_is_not_the_same_sentence_is_held_to_its_own_check(
        self, pack: EvidencePack
    ) -> None:
        # The fake answers with a different sentence that also grounds. It is
        # checked as the sentence it is, and the decision records both.
        handle = a_handle(pack, KIND_RECORD)
        ask = FakeAsk(f"The minutes record the motion {handle}.")
        result = ground("The minutes record the motion.", pack, ask=ask)
        assert result.decisions[0].sentence == "The minutes record the motion."
        assert result.decisions[0].repaired == f"The minutes record the motion {handle}."

    def test_the_counts_say_what_happened_before_and_after(self, pack: EvidencePack) -> None:
        record = a_handle(pack, KIND_RECORD)
        video = a_handle(pack, KIND_VIDEO)
        answer = (
            f"The minutes record the motion {record}. "
            "The council approved the consent agenda. "
            f"The council approved it 7-0 {video}."
        )
        # One answer for the sentence that cites no handle. The tally sentence
        # is sent back too, and the fake has nothing left to say to it.
        ask = FakeAsk(f"The council approved the consent agenda {record}.")
        result = ground(answer, pack, ask=ask)

        assert result.before == {"sentences": 3, "passed": 1, "failed": 2}
        assert result.after == {"kept": 2, "repaired": 1, "removed": 1}
        assert len(result.decisions) == 3
        assert result.removed()[0].code == CODE_NO_REPAIR
        assert "3 sentence(s) were written, 1 passed the check as written" in result.sentence()
        assert "1 were repaired in one round, and 1 were removed" in result.sentence()


class TestTheCheckOverAWholeAnswer:
    """Every sentence is checked, and a whole answer reads its own verdicts."""

    def test_every_sentence_of_an_answer_is_checked(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_RECORD)
        verdicts = check_answer(
            f"The minutes record the motion {handle}. "
            "The council approved the consent agenda. "
            "Not found.",
            pack,
        )
        assert [verdict.code for verdict in verdicts] == [CODE_OK, CODE_NO_HANDLE, CODE_NOT_FOUND]
        assert [verdict.ok for verdict in verdicts] == [True, False, True]

    def test_a_sentence_is_split_where_a_sentence_ends(self) -> None:
        assert split_sentences("One. Two! Three?") == ("One.", "Two!", "Three?")
        # A date and a section number are not the ends of sentences, and a
        # splitter that thought they were would report a fault against the
        # model for a sentence the model never wrote.
        assert split_sentences("It met on Sept. 8, 2026. Section 2.95.060 applies.") == (
            "It met on Sept. 8, 2026.",
            "Section 2.95.060 applies.",
        )
        assert split_sentences("The vote was 7 - 0. Mr. McCoy moved it.") == (
            "The vote was 7 - 0.",
            "Mr. McCoy moved it.",
        )

    def test_a_verdict_says_what_it_found_in_plain_words(self, pack: EvidencePack) -> None:
        handle = a_handle(pack, KIND_VOTE, source_kind="minutes")
        good = check_sentence(f"The council approved it {handle}.", pack)
        bad = check_sentence("The council approved it.", pack)
        assert good.sentence_of_its_own() == f"Grounded ({CODE_OK})."
        assert bad.sentence_of_its_own().startswith(f"Not grounded ({CODE_NO_HANDLE}):")
        assert Verdict(sentence="x", ok=True, code=CODE_OK).handles == ()

    def test_the_facts_of_a_sentence_are_the_things_a_repair_may_not_change(self) -> None:
        before = facts(f'The minutes print "{OUTCOME}" by Alex Kalkhofer.')
        assert OUTCOME in before.quotes
        assert before.numbers == {"7", "0"}
        assert "Alex Kalkhofer" in before.names
        assert before.is_empty() is False
        assert facts("it was approved").urls == frozenset()
        # A handle is not one of those things: the E of "[E3]" is not a name,
        # so the repair that answers "it cites no handle" is not refused.
        assert facts("The minutes record the motion [E3].").names == frozenset()

    def test_a_fact_that_moved_or_appeared_is_a_change(self) -> None:
        before = Facts(quotes=frozenset({"a"}), numbers=frozenset({"7"}))
        after = Facts(quotes=frozenset({"b"}), numbers=frozenset({"7", "8"}))
        changed = changed_facts(before, after)
        assert "the quote 'a' is gone" in changed
        assert "the quote 'b' was added" in changed
        assert "the number '8' was added" in changed
        assert changed_facts(before, before) == ()


# -- The repair a real ladder would ask for ------------------------------------


class TestTheRepairThroughTheLadder:
    """A repair goes through the preflight and the ladder (spec 11.6, 11.5)."""

    @staticmethod
    def a_ladder() -> Ladder:
        """The answer task set to the one provider the test configured."""
        return Ladder.picked("answer", CLOUD_NAME)

    @staticmethod
    def a_machine_with_the_programs() -> Checks:
        """A machine where every program a provider runs is installed.

        A preflight asks PATH whether a command-line provider's program is
        there. That answer belongs to whoever runs the suite, so the tests that
        reach a model give it their own (PROJECT-BRIEF rule 11b).
        """
        return Checks(which=lambda program: f"/usr/bin/{program}")

    def test_a_provider_the_preflight_refuses_is_never_called(
        self, registry: ProviderRegistry, pack: EvidencePack
    ) -> None:
        called: list[str] = []

        def model_call(provider: Provider, rung: Rung, prompt: str) -> Attempt:
            called.append(provider.name)
            return Attempt(ok=True, output="this must never be reached")

        # The provider the ladder picks runs a program this machine does not
        # have, which is what a preflight refuses before anything is sent.
        ask = LadderAsk(
            ladder=Ladder.picked("answer", CLAUDE_NAME),
            registry=registry,
            model_call=model_call,
            checks=Checks(which=lambda program: None),
        )
        failed = Verdict(sentence="x", ok=False, code=CODE_NO_HANDLE, reason="it cites no handle")
        answer = ask(RepairRequest(sentence="x", verdict=failed, pack=pack))

        assert called == []
        assert not answer
        assert "provider unavailable" in answer.reason
        assert ask.repairs[0].ok is False
        assert ask.repairs[0].records[0].reason == REASON_UNAVAILABLE
        # What the preflight said about the provider is kept on the call, which
        # is where a caller reads why a repair was never asked for.
        assert "was not found on this computer" in ask.repairs[0].records[0].detail

    def test_the_ladder_runs_the_model_and_its_sentence_is_checked(
        self, registry: ProviderRegistry, pack: EvidencePack
    ) -> None:
        handle = a_handle(pack, KIND_RECORD)
        prompts: list[str] = []

        def model_call(provider: Provider, rung: Rung, prompt: str) -> Attempt:
            prompts.append(prompt)
            return Attempt(ok=True, output=f"The minutes record the motion {handle}.")

        ask = LadderAsk(
            ladder=self.a_ladder(),
            registry=registry,
            model_call=model_call,
            checks=self.a_machine_with_the_programs(),
        )
        result = ground("The minutes record the motion.", pack, ask=ask)

        assert result.decisions[0].outcome == OUTCOME_REPAIRED
        assert ask.repairs[0].ok is True
        assert ask.repairs[0].records[0].ran == CLOUD_NAME
        # What the model was given is the check's own words and the evidence.
        assert "One sentence did not pass the grounding check" in prompts[0]
        assert CONSENT_TEXT in prompts[0]
        assert handle in prompts[0]
        assert "Do not change a quote, a number, a name or a URL" in prompts[0]
        assert "Answer with one sentence and nothing else" in prompts[0]

    def test_a_model_that_fails_with_a_final_reason_does_not_move_the_ladder(
        self, registry: ProviderRegistry, pack: EvidencePack
    ) -> None:
        def model_call(provider: Provider, rung: Rung, prompt: str) -> Attempt:
            return Attempt(ok=False, reason="content refusal", detail="it declined")

        ask = LadderAsk(
            ladder=self.a_ladder(),
            registry=registry,
            model_call=model_call,
            checks=self.a_machine_with_the_programs(),
        )
        result = ground("The minutes record the motion.", pack, ask=ask)

        assert result.decisions[0].outcome == OUTCOME_REMOVED
        assert result.decisions[0].code == CODE_NO_REPAIR
        assert "which is final" in result.decisions[0].reason
        assert len(ask.repairs[0].records) == 1

    def test_the_repair_prompt_shows_the_evidence_the_sentence_cited(
        self, pack: EvidencePack
    ) -> None:
        handle = a_handle(pack, KIND_RECORD)
        verdict = check_sentence(f'The minutes say "not the printed words" {handle}.', pack)
        request = RepairRequest(sentence="x", verdict=verdict, pack=pack)
        prompt = request.prompt()
        assert CONSENT_TEXT in prompt
        assert "Why it failed:" in prompt
        assert "is not in the cited evidence" in prompt


# -- The real September 8, 2026 minutes ---------------------------------------


@needs(PACKET_MINUTES_SEP08)
class TestTheRealMinutes:
    """The checker against the recorded September 8, 2026 minutes.

    The recording is the run of the September 22, 2026 packet that carries the
    draft minutes of the September 8 session: 22 pages, packet pages 17 to 38.
    Every page number below is a packet page number, which is what spec 10.5
    wants a citation to carry.
    """

    @staticmethod
    def a_page() -> tuple[int, str]:
        """The first page of the run that prints a counted result."""
        reader = PdfReader(PACKET_MINUTES_SEP08)
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if "Carried" in text:
                return 17 + index, text
        raise AssertionError("the recorded minutes print no counted result")

    @staticmethod
    def a_page_at(page_number: int) -> str:
        """The text of one page of the run, by the packet page number it prints."""
        reader = PdfReader(PACKET_MINUTES_SEP08)
        return reader.pages[page_number - 17].extract_text() or ""

    @staticmethod
    def a_pack(text: str, page_number: int) -> EvidencePack:
        """A pack holding that one page, as the code would hold it."""
        return EvidencePack(
            spans=(
                Span(
                    handle="[E1]",
                    kind=KIND_RECORD,
                    text=text,
                    label=f"packet page {page_number}",
                    page_number=page_number,
                    sha256="recorded",
                ),
            )
        )

    def test_the_recorded_page_prints_a_counted_result(self) -> None:
        page_number, text = self.a_page()
        assert page_number == 19
        assert f"Carried: 7 {DASH} 0" in text
        assert NBSP in text, "the recorded pages print non-breaking spaces"

    def test_a_quote_of_the_recorded_line_passes_and_a_changed_one_fails(self) -> None:
        page_number, text = self.a_page()
        pack = self.a_pack(text, page_number)
        # A line the page prints with no count in it: a quote of the counted
        # line is the subject of the tally test below, not of this one.
        line = "There was no report from the City Manager"

        verbatim = check_sentence(f'The minutes print "{line}" [E1].', pack)
        assert verbatim.ok is True, verbatim.reason

        one_word = check_sentence(
            'The minutes print "There was no report from the City Treasurer" [E1].', pack
        )
        assert one_word.ok is False
        assert one_word.code == CODE_QUOTE

    def test_a_quote_of_the_recorded_names_survives_their_non_breaking_spaces(self) -> None:
        page_number, text = self.a_page()
        pack = self.a_pack(text, page_number)
        names = "Susie Hidalgo-Fahring, Sean McCoy"
        # The page prints those names with non-breaking spaces, so the words of
        # the quote match only after the one fold this module allows.
        assert f"Susie{NBSP}Hidalgo-Fahring,{NBSP}Sean{NBSP}McCoy" in text
        assert check_sentence(f'The minutes name "{names}" [E1].', pack).ok is True

        misspelled = check_sentence(
            'The minutes name "Susan Hidalgo-Fahring, Sean McCoy" [E1].', pack
        )
        assert misspelled.ok is False
        assert misspelled.code == CODE_QUOTE

    def test_the_count_the_page_prints_is_not_a_tally_it_can_give(self) -> None:
        page_number, text = self.a_page()
        pack = self.a_pack(text, page_number)
        # The page prints "Carried: 7 – 0", and a sentence may still not state
        # that count from it: a tally comes from a stored vote (spec 10.4).
        assert check_sentence(f"The council carried it 7 {DASH} 0 [E1].", pack).code == CODE_TALLY

    def test_a_number_the_recorded_page_never_says_fails(self) -> None:
        page_number, text = self.a_page()
        pack = self.a_pack(text, page_number)

        assert check_sentence("The council read it as item 41 [E1].", pack).code == CODE_NUMBER
        # And a number the page does print passes: it prints "September 29th".
        assert check_sentence("The council met again on September 29 [E1].", pack).ok is True

    def test_a_name_the_recorded_page_prints_passes_and_one_it_omits_fails(
        self, conn: sqlite3.Connection, stored: Stored
    ) -> None:
        # Packet page 32 prints the approved list of its counted vote, and
        # Marsing is not on it. The roster is the one the pack builds from the
        # database, so the two sentences differ only in the name they print.
        text = self.a_page_at(32)
        assert f"Carried: 5 {DASH} 1" in text, "the packet page number is right"
        roster = evidence_pack(conn, meeting_id=stored.meeting).roster
        page = Span(
            handle="[E1]",
            kind=KIND_RECORD,
            text=text,
            label="packet page 32",
            page_number=32,
            sha256="recorded",
        )
        pack = EvidencePack(spans=(page,), roster=roster)

        assert "Marsing" not in normalize(text), "the page does not print his name"
        assert check_sentence("Council Member McCoy moved it [E1].", pack).ok is True

        omitted = check_sentence("Council Member Marsing moved it [E1].", pack)
        assert omitted.ok is False
        assert omitted.code == CODE_NAME
        assert "Jake Marsing" in omitted.reason
