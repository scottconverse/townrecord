"""The download job: one compiled document, stored as bytes (spec 9.2, 9.6).

A packet runs 60 to 170 MB, so this is the heavy lane. What these tests hold it
to is the shape of what it writes: an artifact and a row that points at it,
the portal's own ids for a citation, and never the signed storage link the
download redirects through.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from townrecord.adapters.primegov import COMPILED_DOCUMENT_PATH
from townrecord.artifacts import get as get_artifact
from townrecord.jobs import DONE, FAILED, PAUSED, read_checkpoint
from townrecord.records.portal import DocumentJob
from townrecord.repo import record_for_portal_document, records_of_meeting

from .conftest import BASE_URL, SIGNED_MARKER, Area, FakePortal, Sync, document, meeting

#: The document the tests fetch: the September 8, 2026 agenda of the fixture
#: portal (compileOutputType 1, template 16805).
AGENDA_ID = 18613
AGENDA_TEMPLATE_ID = 16805

#: A meeting that lists it.
A_MEETING = 4100


def agenda_job(area: Area, *, meeting_id: int = 1, **overrides: Any) -> dict[str, Any]:
    """The payload a sync queues for the fixture portal's agenda."""
    values: dict[str, Any] = {
        "source_id": area.portal_id,
        "meeting_id": meeting_id,
        "document_id": AGENDA_ID,
        "template_id": AGENDA_TEMPLATE_ID,
        "compile_output_type": 1,
        "template_name": "Agenda",
    }
    values.update(overrides)
    return DocumentJob(**values).as_payload()


def a_stored_meeting(sync: Sync, wired: FakePortal, area: Area) -> int:
    """Sync one meeting that lists the agenda, and return the meeting's row id."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(AGENDA_ID, AGENDA_TEMPLATE_ID, 1, "Agenda"),),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    row = sync.conn.execute("SELECT id FROM meetings").fetchone()
    assert row is not None
    return int(row["id"])


def test_a_fetched_document_becomes_an_artifact_and_a_record(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """Spec 9.2: the bytes are an artifact, and the row points at it.

    The job this runs is the one the sync queued for the listing, rather than a
    second one for the same document: a document already stored is not fetched
    twice, which the test below holds the job to.
    """
    meeting_id = a_stored_meeting(sync, wired, area)
    queued = sync.jobs_of_kind("download_record")
    assert len(queued) == 1, "the sync queued the one document of the listing"
    job_id = int(queued[0]["id"])
    sync.lane("heavy")

    assert sync.job(job_id)["state"] == DONE
    assert wired.paths().count(COMPILED_DOCUMENT_PATH) == 1

    records = records_of_meeting(sync.conn, meeting_id)
    assert len(records) == 1
    stored = records[0]
    assert stored.kind == "agenda"
    assert stored.title == "Agenda"
    assert stored.source_id == area.portal_id
    assert stored.portal_document_id == AGENDA_ID
    assert stored.portal_template_id == AGENDA_TEMPLATE_ID
    assert stored.page_count is None, "reading the PDF's pages belongs to the reading job"
    read = sync.jobs_of_kind("extract_pages")
    assert len(read) == 1, "which is queued for the record this job just stored"
    assert json.loads(read[0]["payload"]) == {"record_id": stored.id}

    artifact = get_artifact(sync.conn, stored.artifact_id, storage_root)
    assert artifact is not None
    assert artifact.kind == "record"
    assert artifact.path.read_bytes() == wired.document_bytes
    assert artifact.sha256 == hashlib.sha256(wired.document_bytes).hexdigest()
    assert artifact.rel_path.endswith(".pdf"), "the content type gives the extension"
    assert record_for_portal_document(sync.conn, meeting_id, AGENDA_ID) == stored

    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["already_stored"] is False
    assert checkpoint["record_id"] == stored.id
    assert checkpoint["artifact_id"] == artifact.id
    assert checkpoint["size"] == len(wired.document_bytes)
    assert checkpoint["sha256"] == artifact.sha256
    assert checkpoint["content_type"] == "application/pdf"


def test_the_citation_names_the_portal_and_never_the_signed_link(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """Spec 9.2: the signed link expires in two days and cites nothing."""
    meeting_id = a_stored_meeting(sync, wired, area)
    sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")

    artifact = get_artifact(
        sync.conn,
        records_of_meeting(sync.conn, meeting_id)[0].artifact_id,
        storage_root,
    )
    assert artifact is not None
    citation = artifact.meta
    assert citation["origin"] == BASE_URL
    assert citation["source_id"] == area.portal_id
    assert citation["meeting_id"] == meeting_id
    assert citation["portal_meeting_id"] == A_MEETING
    assert citation["portal_document_id"] == AGENDA_ID
    assert citation["portal_template_id"] == AGENDA_TEMPLATE_ID
    assert citation["template_name"] == "Agenda"
    assert citation["citation_url"] == (
        f"{BASE_URL}/Portal/Meeting?meetingId={A_MEETING}&meetingTemplateId={AGENDA_TEMPLATE_ID}"
    )
    assert SIGNED_MARKER not in json.dumps(citation)
    assert SIGNED_MARKER not in artifact.rel_path


def test_a_document_already_stored_is_not_fetched_twice(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.2: the portal ids are the key, so a re-run writes the same one row."""
    meeting_id = a_stored_meeting(sync, wired, area)
    payload = agenda_job(area, meeting_id=meeting_id)
    sync.queue("download_record", payload)
    sync.lane("heavy")
    first = records_of_meeting(sync.conn, meeting_id)[0]
    hops = wired.paths().count(COMPILED_DOCUMENT_PATH)

    second_job = sync.queue("download_record", payload)
    sync.lane("heavy")

    assert sync.job(second_job)["state"] == DONE
    assert records_of_meeting(sync.conn, meeting_id) == [first], "no second row"
    assert wired.paths().count(COMPILED_DOCUMENT_PATH) == hops, "and nothing was fetched"
    assert sync.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    checkpoint = read_checkpoint(sync.conn, second_job)
    assert checkpoint == {
        "record_id": first.id,
        "artifact_id": first.artifact_id,
        "kind": "agenda",
        "already_stored": True,
    }


@pytest.mark.parametrize("streamed", [False, True])
def test_a_document_over_the_limit_is_refused_with_its_reason(
    area: Area,
    wired: FakePortal,
    sync: Sync,
    monkeypatch: pytest.MonkeyPatch,
    streamed: bool,
) -> None:
    """Spec 9.6: the limit is applied, and the refusal says which limit.

    Both ways a body can pass a limit: one that declares itself too big, and one
    that declares nothing and streams past the limit anyway.
    """
    monkeypatch.setenv("TOWNRECORD_PDF_LIMIT_BYTES", "1024")
    if streamed:
        wired.document_chunks = [b"x" * 700, b"x" * 700]
    else:
        wired.document_bytes = b"x" * 4096

    meeting_id = a_stored_meeting(sync, wired, area)
    job_id = sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")

    row = sync.job(job_id)
    assert row["state"] == FAILED
    expected = (
        "passed the limit of 1024 bytes while streaming"
        if streamed
        else "declares 4096 bytes, over the limit of 1024 bytes"
    )
    assert expected in row["last_error"]
    assert records_of_meeting(sync.conn, meeting_id) == [], "nothing half-stored"
    assert sync.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


def test_a_template_that_is_no_record_kind_pauses_with_its_name(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A cancellation notice is not a record of the meeting (spec 9.5)."""
    meeting_id = a_stored_meeting(sync, wired, area)
    job_id = sync.queue(
        "download_record",
        agenda_job(
            area,
            meeting_id=meeting_id,
            document_id=9001,
            template_name="Notice of Cancellation",
        ),
    )
    sync.lane("heavy")

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "'Notice of Cancellation' template" in row["last_error"]
    assert "not one of the record kinds" in row["last_error"]
    assert record_for_portal_document(sync.conn, meeting_id, 9001) is None, (
        "the notice is not a record of the meeting"
    )
    assert {record.portal_document_id for record in records_of_meeting(sync.conn, meeting_id)} == {
        AGENDA_ID
    }, "and the agenda of the listing is the only record there"


def test_a_download_for_no_meeting_at_all_pauses(area: Area, wired: FakePortal, sync: Sync) -> None:
    """A job whose meeting is gone says so rather than storing an orphan row."""
    job_id = sync.queue("download_record", agenda_job(area, meeting_id=999))
    sync.lane("heavy")

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == "There is no meeting 999 for document 18613."
    assert wired.requests == [], "nothing was fetched for a meeting that is not there"


def test_a_download_from_a_source_that_is_not_a_portal_pauses(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A channel publishes no meeting documents, and the reason names the type."""
    meeting_id = a_stored_meeting(sync, wired, area)
    job_id = sync.queue(
        "download_record", agenda_job(area, meeting_id=meeting_id, source_id=area.channel_id)
    )
    sync.lane("heavy")

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "is a video_channel source" in row["last_error"]
