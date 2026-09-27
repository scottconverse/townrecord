"""The rules migration 0005 enforces by itself (spec 6.2, 7.2, 10.2, 10.4).

Every rule here is one the schema refuses to break, so a caller that gets it
wrong hears about it at the moment of the write and not three screens later.
"""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.repo import (
    add_overlap,
    insert_agenda_item,
    insert_jurisdiction,
    insert_meeting,
    insert_person,
    insert_record,
    insert_record_citation,
    insert_record_page,
    insert_seat,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    insert_video_citation,
    insert_vote,
    overlaps_of,
    primary_video,
)
from townrecord.repo.store import insert

from .conftest import Area, PutArtifact

#: The tables spec 6.2 asks for, by the name this schema gives them.
SPEC_6_2_TABLES = (
    "jurisdictions",
    "jurisdiction_overlaps",
    "bodies",
    "people",
    "seats",
    "sources",
    "meetings",
    "videos",
    "records",
    "record_pages",
    "transcripts",
    "segments",
    "agenda_items",
    "votes",
    "citations",
)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


@pytest.mark.parametrize("table", SPEC_6_2_TABLES)
def test_every_object_of_spec_6_2_has_a_table(conn: sqlite3.Connection, table: str) -> None:
    assert table in table_names(conn)


def test_a_segment_has_no_agenda_item_column(conn: sqlite3.Connection) -> None:
    """Spec 10.2, last paragraph: the item is not stored on the line.

    TownReporter shipped such a column and it answered null on all 47,592
    rows, because no code path ever wrote it.
    """
    assert column_names(conn, "segments") == {
        "id",
        "transcript_id",
        "start_ms",
        "end_ms",
        "text",
        "speaker_label",
    }


# --------------------------------------------------------------- the words --


def test_a_source_status_outside_the_list_is_refused(conn: sqlite3.Connection, area: Area) -> None:
    for status in ("suggested", "accepted", "rejected", "broken"):
        insert_source(
            conn,
            jurisdiction_id=area.city,
            type="meeting_portal",
            origin=f"https://portal.test.invalid/{status}",
            suggested_by="user",
            reason="Suggested while setting the area up.",
            status=status,
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_source(
            conn,
            jurisdiction_id=area.city,
            type="meeting_portal",
            origin="https://portal.test.invalid/maybe",
            suggested_by="user",
            reason="Suggested while setting the area up.",
            status="maybe",
        )


def test_a_source_type_outside_the_list_is_refused(conn: sqlite3.Connection, area: Area) -> None:
    for kind in (
        "meeting_portal",
        "video_channel",
        "records_archive",
        "website_news",
        "legislature_system",
    ):
        insert_source(
            conn,
            jurisdiction_id=area.city,
            type=kind,
            origin=f"https://portal.test.invalid/{kind}",
            suggested_by="user",
            reason="Suggested while setting the area up.",
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_source(
            conn,
            jurisdiction_id=area.city,
            type="newsletter",
            origin="https://portal.test.invalid/newsletter",
            suggested_by="user",
            reason="Suggested while setting the area up.",
        )


def test_a_meeting_type_outside_the_list_is_refused(conn: sqlite3.Connection, area: Area) -> None:
    for day, kind in enumerate(("regular", "study", "special", "executive"), start=1):
        insert_meeting(conn, body_id=area.body, starts_at=f"2026-10-{day:02d}T19:00:00", type=kind)
    with pytest.raises(sqlite3.IntegrityError):
        insert_meeting(conn, body_id=area.body, starts_at="2026-10-09T19:00:00", type="workshop")


def test_a_jurisdiction_type_outside_the_list_is_refused(conn: sqlite3.Connection) -> None:
    for kind in (
        "state",
        "county",
        "city",
        "school_district",
        "special_district",
        "federal",
    ):
        insert_jurisdiction(conn, type=kind, name=f"A {kind}")
    with pytest.raises(sqlite3.IntegrityError):
        insert_jurisdiction(conn, type="township", name="A township")


def test_a_transcript_origin_outside_the_list_is_refused(
    conn: sqlite3.Connection, video: int, put_artifact: PutArtifact
) -> None:
    """Each origin is a different capture, so each one is different bytes."""
    for origin in (
        "publisher_captions",
        "auto_captions",
        "sister_channel",
        "local_speech_to_text",
    ):
        artifact = put_artifact("transcript", f"WEBVTT {origin}\n".encode(), "vtt")
        insert_transcript(conn, video_id=video, artifact_id=artifact, origin=origin)
    artifact = put_artifact("transcript", b"WEBVTT machine_translation\n", "vtt")
    with pytest.raises(sqlite3.IntegrityError):
        insert_transcript(conn, video_id=video, artifact_id=artifact, origin="machine_translation")


def test_an_alignment_method_outside_the_list_is_refused(
    conn: sqlite3.Connection, area: Area
) -> None:
    for method in ("html_video_times", "spoken_transitions"):
        insert_agenda_item(
            conn,
            meeting_id=area.meeting,
            number=method,
            start_ms=0,
            end_ms=1000,
            alignment_method=method,
        )
    insert_agenda_item(conn, meeting_id=area.meeting, number="none", alignment_method="none")
    with pytest.raises(sqlite3.IntegrityError):
        insert_agenda_item(
            conn,
            meeting_id=area.meeting,
            number="guessed",
            start_ms=0,
            end_ms=1000,
            alignment_method="guessed",
        )


def test_a_vote_source_kind_outside_the_list_is_refused(
    conn: sqlite3.Connection, area: Area
) -> None:
    for kind in ("structured", "minutes", "packet", "transcript"):
        item = insert_agenda_item(conn, meeting_id=area.meeting, number=f"item-{kind}")
        insert_vote(
            conn,
            agenda_item_id=item,
            result="passed",
            source_kind=kind,
            evidence="The clerk recorded it.",
        )
    item = insert_agenda_item(conn, meeting_id=area.meeting, number="item-guess")
    with pytest.raises(sqlite3.IntegrityError):
        insert_vote(
            conn,
            agenda_item_id=item,
            result="passed",
            source_kind="rumour",
            evidence="Someone said so.",
        )


def test_a_record_kind_outside_the_list_is_refused(
    conn: sqlite3.Connection, area: Area, put_artifact: PutArtifact
) -> None:
    """Seven kinds of document, and a meeting may hold two of the same kind."""
    for kind in ("agenda", "packet", "minutes", "ordinance", "resolution", "budget", "report"):
        artifact = put_artifact("document", f"%PDF {kind}\n".encode(), "pdf")
        insert_record(conn, meeting_id=area.meeting, kind=kind, artifact_id=artifact)
    artifact = put_artifact("document", b"%PDF transcript\n", "pdf")
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(conn, meeting_id=area.meeting, kind="transcript", artifact_id=artifact)


def test_a_capture_state_or_readiness_outside_the_list_is_refused(
    conn: sqlite3.Connection, area: Area
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_video(
            conn,
            source_id=area.channel,
            platform_video_id="v-bad-state",
            capture_state="halfway",
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_video(
            conn,
            source_id=area.channel,
            platform_video_id="v-bad-readiness",
            readiness="probably-finished",
        )


# ------------------------------------------------------------------ the area --


def test_a_jurisdiction_identifier_needs_its_kind(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_jurisdiction(conn, type="county", name="Boulder County", official_id="08013")
    with pytest.raises(sqlite3.IntegrityError):
        insert_jurisdiction(conn, type="county", name="Boulder County", official_id_kind="fips")


def test_one_name_under_one_parent_is_stored_once(conn: sqlite3.Connection, area: Area) -> None:
    """A second Boulder County is either the same county or a typo."""
    with pytest.raises(sqlite3.IntegrityError):
        insert_jurisdiction(conn, type="county", name="Boulder County")
    insert_jurisdiction(conn, type="special_district", name="Longmont", parent_id=area.county)
    with pytest.raises(sqlite3.IntegrityError):
        insert_jurisdiction(conn, type="special_district", name="Longmont", parent_id=area.county)
    # Another type under the same parent is another jurisdiction.
    insert_jurisdiction(conn, type="school_district", name="Longmont", parent_id=area.county)


def test_a_jurisdiction_cannot_overlap_itself(conn: sqlite3.Connection, area: Area) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        add_overlap(conn, area.city, area.city)


def test_an_overlap_is_one_fact_stored_once_and_mirrored(
    conn: sqlite3.Connection, area: Area
) -> None:
    """A city in two counties overlaps both; either direction answers."""
    assert overlaps_of(conn, area.city) == [area.county]
    assert overlaps_of(conn, area.county) == [area.city]
    add_overlap(conn, area.city, area.county)
    assert overlaps_of(conn, area.city) == [area.county]

    conn.execute("DELETE FROM jurisdiction_overlaps WHERE jurisdiction_id = ?", (area.city,))
    assert overlaps_of(conn, area.city) == []
    assert overlaps_of(conn, area.county) == [], "the mirror row goes with it"


def test_a_source_cannot_appear_without_a_suggester_or_a_reason(
    conn: sqlite3.Connection, area: Area
) -> None:
    for suggested_by, reason in (("", "Because the page links to it."), ("user", "")):
        with pytest.raises(sqlite3.IntegrityError):
            insert_source(
                conn,
                jurisdiction_id=area.city,
                type="website_news",
                origin="https://news.test.invalid",
                suggested_by=suggested_by,
                reason=reason,
            )


def test_the_same_source_type_and_origin_is_stored_once(
    conn: sqlite3.Connection, area: Area
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_source(
            conn,
            jurisdiction_id=area.city,
            type="meeting_portal",
            origin="https://portal.test.invalid",
            suggested_by="user",
            reason="The same portal a second time.",
        )


def test_a_seat_ends_after_it_starts(conn: sqlite3.Connection, area: Area) -> None:
    person = insert_person(conn, name="A Council Member")
    insert_seat(
        conn,
        person_id=person,
        body_id=area.body,
        title="Council Member",
        start_date="2024-01-09",
        end_date="2028-01-11",
    )
    insert_seat(conn, person_id=person, body_id=area.body, title="Mayor", start_date="2026-01-13")
    with pytest.raises(sqlite3.IntegrityError):
        insert_seat(
            conn,
            person_id=person,
            body_id=area.body,
            title="Council Member",
            start_date="2028-01-11",
            end_date="2024-01-09",
        )


# ---------------------------------------------------------------- the videos --


def test_a_second_primary_video_for_one_meeting_is_refused(
    conn: sqlite3.Connection, area: Area, video: int
) -> None:
    """Spec 7.2 step 7: two channels record the meeting, one of them is primary."""
    with pytest.raises(sqlite3.IntegrityError):
        insert_video(
            conn,
            source_id=area.portal,
            meeting_id=area.meeting,
            platform_video_id="v-0002",
            is_primary=True,
        )


def test_a_second_video_that_is_not_primary_is_kept(
    conn: sqlite3.Connection, area: Area, video: int
) -> None:
    other = insert_video(
        conn, source_id=area.portal, meeting_id=area.meeting, platform_video_id="v-0002"
    )
    assert other != video
    primary = primary_video(conn, area.meeting)
    assert primary is not None, "both recordings are kept and one of them is primary"
    assert primary.id == video
    assert primary_video(conn, area.meeting + 1) is None, "another meeting has none"


def test_a_video_matched_to_no_meeting_is_outside_the_primary_rule(
    conn: sqlite3.Connection, area: Area
) -> None:
    """A listing is kept before it is matched (spec 7.2 step 7), so two
    unmatched listings have no meeting to be primary for."""
    for index in range(2):
        insert_video(
            conn,
            source_id=area.channel,
            platform_video_id=f"v-unmatched-{index}",
            is_primary=True,
        )


def test_deleting_a_meeting_that_has_videos_is_refused(
    conn: sqlite3.Connection, area: Area, video: int
) -> None:
    """Nothing cascades: a recording never disappears as a side effect."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM meetings WHERE id = ?", (area.meeting,))
    assert conn.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1


def test_deleting_a_jurisdiction_that_has_a_body_is_refused(
    conn: sqlite3.Connection, area: Area
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM jurisdictions WHERE id = ?", (area.city,))


# --------------------------------------------------------------- the records --


def test_a_record_must_point_at_an_artifact(conn: sqlite3.Connection, area: Area) -> None:
    """A record holds the artifact of the file, never a path (spec 8.6)."""
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(conn, meeting_id=area.meeting, kind="agenda", artifact_id=999_999)


def test_a_transcript_must_point_at_an_artifact(conn: sqlite3.Connection, video: int) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_transcript(conn, video_id=video, artifact_id=999_999, origin="publisher_captions")


def test_the_same_bytes_are_one_record_of_one_meeting(
    conn: sqlite3.Connection, area: Area, document_artifact: int
) -> None:
    insert_record(conn, meeting_id=area.meeting, kind="agenda", artifact_id=document_artifact)
    with pytest.raises(sqlite3.IntegrityError):
        insert_record(conn, meeting_id=area.meeting, kind="agenda", artifact_id=document_artifact)


def test_a_record_page_is_numbered_from_one(
    conn: sqlite3.Connection, area: Area, document_artifact: int
) -> None:
    record = insert_record(
        conn, meeting_id=area.meeting, kind="agenda", artifact_id=document_artifact, page_count=1
    )
    insert_record_page(
        conn, record_id=record, page_number=1, footer_page_number=1, text="Call to order"
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert_record_page(conn, record_id=record, page_number=0, text="Not a page.")
    with pytest.raises(sqlite3.IntegrityError):
        insert_record_page(conn, record_id=record, page_number=1, text="The same page twice.")


# ----------------------------------------------------------- the transcripts --


def test_a_final_transcript_says_when_it_settled(
    conn: sqlite3.Connection, video: int, transcript_artifact: int
) -> None:
    """Spec 8.7: a capture is provisional for 24 hours, then it settles."""
    insert_transcript(
        conn,
        video_id=video,
        artifact_id=transcript_artifact,
        origin="publisher_captions",
        is_provisional=False,
        settled_at="2026-09-10T01:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert_transcript(
            conn,
            video_id=video,
            artifact_id=transcript_artifact,
            origin="publisher_captions",
            is_provisional=False,
        )


def test_a_segment_ends_after_it_starts(conn: sqlite3.Connection, transcript: int) -> None:
    insert_segment(conn, transcript_id=transcript, start_ms=0, end_ms=5000, text="Good evening.")
    with pytest.raises(sqlite3.IntegrityError):
        insert_segment(
            conn, transcript_id=transcript, start_ms=5000, end_ms=1000, text="Backwards."
        )


# ------------------------------------------------------------- the items --


def test_an_aligned_item_carries_the_whole_range(conn: sqlite3.Connection, area: Area) -> None:
    insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="1.A",
        start_ms=600_000,
        end_ms=900_000,
        alignment_method="html_video_times",
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert_agenda_item(
            conn,
            meeting_id=area.meeting,
            number="1.B",
            start_ms=600_000,
            alignment_method="html_video_times",
        )
    with pytest.raises(sqlite3.IntegrityError):
        insert_agenda_item(
            conn,
            meeting_id=area.meeting,
            number="1.C",
            start_ms=600_000,
            end_ms=900_000,
            alignment_method="none",
        )


def test_an_item_number_appears_once_per_meeting(conn: sqlite3.Connection, area: Area) -> None:
    insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")
    with pytest.raises(sqlite3.IntegrityError):
        insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")


# ------------------------------------------------------------- the evidence --


def test_a_vote_cannot_appear_without_evidence(conn: sqlite3.Connection, area: Area) -> None:
    item = insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")
    with pytest.raises(sqlite3.IntegrityError):
        insert_vote(conn, agenda_item_id=item, result="passed", source_kind="minutes", evidence="")
    with pytest.raises(sqlite3.IntegrityError):
        insert_vote(
            conn, agenda_item_id=item, result="carried", source_kind="minutes", evidence="..."
        )


def test_a_transcript_only_vote_is_never_a_tally(conn: sqlite3.Connection, area: Area) -> None:
    """Spec 10.4: the transcript mentions the vote, it does not count it."""
    item = insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")
    insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="transcript",
        evidence="The mayor said the motion carried.",
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert(
            conn,
            "votes",
            {
                "agenda_item_id": item,
                "result": "passed",
                "source_kind": "transcript",
                "evidence": "The mayor said six to one.",
                "tally": '{"yes": 6, "no": 1}',
            },
        )


def test_an_item_keeps_one_vote_per_source_kind(conn: sqlite3.Connection, area: Area) -> None:
    """Spec 10.4: when sources disagree, all of them are shown and none is picked."""
    item = insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")
    insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="minutes",
        evidence="Minutes: the motion carried.",
    )
    insert_vote(
        conn,
        agenda_item_id=item,
        result="failed",
        source_kind="packet",
        evidence="The packet sheet says it failed.",
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert_vote(
            conn,
            agenda_item_id=item,
            result="passed",
            source_kind="minutes",
            evidence="Minutes again.",
        )


def test_a_video_citation_carries_its_video_and_its_seconds(
    conn: sqlite3.Connection, area: Area, video: int, transcript: int, transcript_artifact: int
) -> None:
    insert_video_citation(
        conn,
        video_id=video,
        transcript_id=transcript,
        artifact_id=transcript_artifact,
        excerpt="Good evening.",
        start_ms=0,
        end_ms=5000,
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert(
            conn,
            "citations",
            {
                "kind": "video",
                "video_id": video,
                "transcript_id": transcript,
                "artifact_id": transcript_artifact,
                "excerpt": "Good evening.",
            },
        )


def test_a_record_citation_carries_its_document_and_its_source(
    conn: sqlite3.Connection, area: Area, document_artifact: int
) -> None:
    record = insert_record(
        conn, meeting_id=area.meeting, kind="minutes", artifact_id=document_artifact
    )
    insert_record_citation(
        conn,
        record_id=record,
        source_id=area.portal,
        artifact_id=document_artifact,
        excerpt="Motion carried.",
        page_number=1,
    )
    with pytest.raises(sqlite3.IntegrityError):
        insert(
            conn,
            "citations",
            {
                "kind": "record",
                "record_id": record,
                "artifact_id": document_artifact,
                "excerpt": "Motion carried.",
            },
        )
