"""Community baseline regressions: stable identity and retained evidence."""

import json

from townrecord.artifacts import get as get_artifact
from townrecord.repo import get_meeting, get_record, records_of_meeting, upsert_meeting

from .test_download_record import a_stored_meeting, agenda_job


def test_rescheduled_provider_identity_keeps_links_and_records_change(area, sync):
    args = dict(body_id=area.body_id, portal_source_id=area.portal_id, portal_meeting_id=901)
    meeting_id, _ = upsert_meeting(sync.conn, starts_at="2026-09-01T19:00:00", **args)
    updated_id, created = upsert_meeting(sync.conn, starts_at="2026-09-02T19:00:00", **args)
    assert updated_id == meeting_id and not created
    assert get_meeting(sync.conn, meeting_id).starts_at == "2026-09-02T19:00:00"
    history = sync.conn.execute("SELECT * FROM meeting_changes").fetchall()
    assert len(history) == 1
    assert json.loads(history[0]["previous_state"])["starts_at"] == "2026-09-01T19:00:00"
    upsert_meeting(sync.conn, starts_at="2026-09-02T19:00:00", **args)
    assert sync.conn.execute("SELECT COUNT(*) FROM meeting_changes").fetchone()[0] == 1


def test_distinct_ids_at_same_time_remain_distinct(area, sync):
    args = dict(
        body_id=area.body_id,
        starts_at="2026-09-01T19:00:00",
        portal_source_id=area.portal_id,
        title="Council",
    )
    first, _ = upsert_meeting(sync.conn, portal_meeting_id=901, **args)
    second, created = upsert_meeting(sync.conn, portal_meeting_id=902, **args)
    assert created and first != second
    assert get_meeting(sync.conn, first).portal_meeting_id == 901
    assert upsert_meeting(sync.conn, portal_meeting_id=902, **args) == (second, False)


def test_changed_document_preserves_old_bytes_and_selects_latest(area, wired, sync, storage_root):
    meeting_id = a_stored_meeting(sync, wired, area)
    original_bytes = wired.document_bytes
    sync.lane("heavy")
    original = records_of_meeting(sync.conn, meeting_id)[0]
    wired.document_bytes += b"\n% revised public agenda\n"
    sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")
    current = records_of_meeting(sync.conn, meeting_id)
    assert len(current) == 1 and current[0].id != original.id
    assert get_record(sync.conn, original.id) == original
    assert (
        get_artifact(sync.conn, original.artifact_id, storage_root).path.read_bytes()
        == original_bytes
    )
    assert (
        get_artifact(sync.conn, current[0].artifact_id, storage_root).path.read_bytes()
        == wired.document_bytes
    )
    revision = sync.conn.execute("SELECT * FROM record_revisions").fetchone()
    assert (revision["previous_record_id"], revision["current_record_id"]) == (
        original.id,
        current[0].id,
    )
    sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")
    assert records_of_meeting(sync.conn, meeting_id) == current
    assert sync.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    wired.document_bytes = original_bytes
    sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")
    reverted = records_of_meeting(sync.conn, meeting_id)
    assert len(reverted) == 1 and reverted[0].id > current[0].id
    assert reverted[0].artifact_id == original.artifact_id
    assert sync.conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0] == 2


def test_failed_revalidation_keeps_the_available_document(area, wired, sync):
    meeting_id = a_stored_meeting(sync, wired, area)
    sync.lane("heavy")
    before = records_of_meeting(sync.conn, meeting_id)
    wired.document_bytes = b""
    wired.document_content_type = "text/html"
    # A failed upstream request must not erase the existing record.
    wired.redirect_hops = 20
    job = sync.queue("download_record", agenda_job(area, meeting_id=meeting_id))
    sync.lane("heavy")
    assert sync.job(job)["state"] == "failed"
    assert records_of_meeting(sync.conn, meeting_id) == before


def test_corrected_document_association_survives_source_refresh(
    area, wired, sync, client, read_write_token
):
    from townrecord.repo import insert_meeting

    original_meeting = a_stored_meeting(sync, wired, area)
    sync.lane("heavy")
    original = records_of_meeting(sync.conn, original_meeting)[0]
    destination = insert_meeting(sync.conn, body_id=area.body_id, starts_at="2026-09-09T19:00:00")
    response = client.post(
        f"/v1/operator/records/{original.id}/associate",
        json={"meeting_id": destination, "reason": "Corrected misplaced attachment"},
        headers={"Authorization": f"Bearer {read_write_token}"},
    )
    assert response.status_code == 200, response.text
    wired.document_bytes += b"\n% changed after association correction\n"
    sync.queue("download_record", agenda_job(area, meeting_id=original_meeting))
    sync.lane("heavy")
    assert records_of_meeting(sync.conn, original_meeting) == []
    current = records_of_meeting(sync.conn, destination)
    assert len(current) == 1 and current[0].id != original.id
    assert get_record(sync.conn, original.id).meeting_id == destination
