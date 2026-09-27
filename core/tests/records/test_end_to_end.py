"""One real meeting, all four jobs, through the runner (spec 7.1, 9.2, 10.2).

The September 8, 2026 regular session of the recorded portal: the listing, its
two compiled documents, its HTML agenda, its YouTube video, the caption track
of that video, and the alignment of the one against the other. Every byte comes
from a fixture; nothing here reaches the network.

The captions do not arrive with the listing. They arrive later, from the
channel's own work, so the align job pauses the first time and runs on the
sync that follows (spec 7.1 step 7) — which is the loop this file is here to
hold the jobs to.
"""

from __future__ import annotations

from pathlib import Path

from townrecord.artifacts import store
from townrecord.captions import parse_srv3
from townrecord.jobs import DONE, PAUSED, read_checkpoint
from townrecord.repo import (
    agenda_items,
    get_meeting_alignment,
    insert_segment,
    insert_transcript,
    meeting_by_portal_id,
    primary_video,
    records_of_meeting,
)

from .conftest import (
    AGENDA_16805,
    CAPTIONS_16805,
    MEASURED_COUNTS,
    MEASURED_OFFSET_S,
    MEETING_3709,
    VIDEO_3709,
    VIDEO_3709_DURATION_S,
    Area,
    FakePortal,
    Sync,
    needs,
    trimmed_2026,
)

#: The tables a run of the window is allowed to add a row to, and must not on
#: a second run: the sync, the downloads and the alignment all write here.
COUNTED_TABLES = (
    "meetings",
    "videos",
    "agenda_items",
    "records",
    "artifacts",
    "meeting_alignments",
)


def _report(observed: object, expected: object, extra: str = "") -> str:
    """A failure message that prints what was measured beside what was expected."""
    return f"unit F measured {expected}; this run measured {observed}. {extra}".strip()


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_real_meeting_runs_through_all_four_jobs(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The whole window: sync, download, the HTML agenda, and the alignment."""
    wired.html_agenda = AGENDA_16805.read_bytes()
    wired.meetings = trimmed_2026()

    # -- the sync: the listing becomes a meeting, a video, two downloads ------
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    meeting = meeting_by_portal_id(sync.conn, area.portal_id, MEETING_3709)
    assert meeting is not None, "the recorded meeting was listed and stored"
    assert meeting.portal_html_template_id == 16805
    assert meeting.is_cancelled is False

    video = primary_video(sync.conn, meeting.id)
    assert video is not None and video.platform_video_id == VIDEO_3709

    downloads = sync.jobs_of_kind("download_record")
    assert len(downloads) == 2, "the agenda and the packet, and nothing else"
    assert {row["state"] for row in downloads} == {"queued"}

    # The alignment cannot run yet: the captions have not been fetched.
    paused = [row for row in sync.jobs_of_kind("align_meeting") if row["state"] == PAUSED]
    assert len(paused) == 1, "an align job was queued by the sync"
    assert "has no transcript yet" in paused[0]["last_error"]
    assert get_meeting_alignment(sync.conn, meeting.id) is None, "nothing was written"

    # -- the capture: the video's length, and the caption track ---------------
    sync.conn.execute(
        "UPDATE videos SET duration_s = ? WHERE id = ?", (VIDEO_3709_DURATION_S, video.id)
    )
    artifact = store(sync.conn, storage_root, "transcript", CAPTIONS_16805.read_bytes(), "srv3")
    transcript_id = insert_transcript(
        sync.conn, video_id=video.id, artifact_id=artifact.id, origin="auto_captions"
    )
    lines = parse_srv3(CAPTIONS_16805.read_bytes())
    for line in lines:
        insert_segment(
            sync.conn,
            transcript_id=transcript_id,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            text=line.text,
        )

    # -- the later sync queues the alignment again, and it runs ---------------
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    sync.lane("heavy")

    assert {row["state"] for row in sync.jobs_of_kind("download_record")} == {DONE}
    assert all(row["state"] == DONE for row in sync.jobs_of_kind("sync_primegov"))

    stored = records_of_meeting(sync.conn, meeting.id)
    assert {record.kind for record in stored} == {"agenda", "packet"}
    assert len({record.artifact_id for record in stored}) == 1, (
        "the fake portal serves one file for both documents, so one artifact is stored"
    )
    assert {record.portal_document_id for record in stored} == {18613, 18658}

    # -- the alignment --------------------------------------------------------
    align_jobs = sync.jobs_of_kind("align_meeting")
    done = [row for row in align_jobs if row["state"] == DONE]
    assert len(done) == 1, f"one align job ran to its end, of {len(align_jobs)}"
    checkpoint = read_checkpoint(sync.conn, done[0]["id"])
    assert checkpoint["counts_by_method"] == MEASURED_COUNTS, _report(
        checkpoint["counts_by_method"], MEASURED_COUNTS, f"({len(lines)} caption lines were read)"
    )

    alignment = get_meeting_alignment(sync.conn, meeting.id)
    assert alignment is not None
    assert alignment.video_id == video.id
    assert alignment.transcript_id == transcript_id
    assert alignment.agenda_item_count == sum(MEASURED_COUNTS.values())
    assert (
        alignment.spoken_transitions,
        alignment.html_video_times,
        alignment.no_alignment,
    ) == (
        MEASURED_COUNTS["spoken_transitions"],
        MEASURED_COUNTS["html_video_times"],
        MEASURED_COUNTS["none"],
    )
    assert alignment.offset_s == MEASURED_OFFSET_S, _report(
        alignment.offset_s, MEASURED_OFFSET_S, f"(reason: {alignment.offset_reason!r})"
    )
    assert alignment.offset_accepted is True
    assert alignment.anchors, "the offset rests on anchors, and they are stored"

    # -- and the items carry it -----------------------------------------------
    counts: dict[str, int] = {}
    for item in agenda_items(sync.conn, meeting.id):
        counts[item.alignment_method] = counts.get(item.alignment_method, 0) + 1
    assert counts == MEASURED_COUNTS, _report(counts, MEASURED_COUNTS)


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_end_to_end_result_is_the_same_on_a_second_run(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The whole window twice: one set of rows, and one alignment row."""
    wired.html_agenda = AGENDA_16805.read_bytes()
    wired.meetings = trimmed_2026()
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    sync.lane("heavy")

    meeting = meeting_by_portal_id(sync.conn, area.portal_id, MEETING_3709)
    assert meeting is not None
    video = primary_video(sync.conn, meeting.id)
    assert video is not None
    sync.conn.execute(
        "UPDATE videos SET duration_s = ? WHERE id = ?", (VIDEO_3709_DURATION_S, video.id)
    )
    artifact = store(sync.conn, storage_root, "transcript", CAPTIONS_16805.read_bytes(), "srv3")
    transcript_id = insert_transcript(
        sync.conn, video_id=video.id, artifact_id=artifact.id, origin="auto_captions"
    )
    for line in parse_srv3(CAPTIONS_16805.read_bytes()):
        insert_segment(
            sync.conn,
            transcript_id=transcript_id,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            text=line.text,
        )
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    sync.lane("heavy")

    before = {
        table: sync.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COUNTED_TABLES
    }
    first = get_meeting_alignment(sync.conn, meeting.id)
    assert first is not None

    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    sync.lane("heavy")

    after = {
        table: sync.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COUNTED_TABLES
    }
    assert after == before, "a second run of the window writes no second row"
    assert after["meeting_alignments"] == 1
    again = get_meeting_alignment(sync.conn, meeting.id)
    assert again is not None
    assert again.offset_s == first.offset_s
    assert again.anchors == first.anchors
