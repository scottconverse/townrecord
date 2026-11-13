"""Reviewed publication is isolated from private acquisitions and later corrections."""

from townrecord import artifacts
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_record,
    insert_record_page,
)


def test_review_publish_correct_withdraw_and_private_access(
    client, conn, read_write_token, read_token, api_storage_root
):
    writer = {"Authorization": f"Bearer {read_write_token}"}
    reader = {"Authorization": f"Bearer {read_token}"}
    city = insert_jurisdiction(conn, type="city", name="Example community")
    body = insert_body(conn, jurisdiction_id=city, name="Council")
    meeting = insert_meeting(
        conn, body_id=body, starts_at="2026-11-01T19:00:00", title="Council meeting"
    )
    item = artifacts.store(
        conn,
        api_storage_root,
        "record",
        b"Original harbor budget",
        "txt",
        meta={"citation_url": "https://example.org/agenda.txt"},
    )
    record = insert_record(conn, meeting_id=meeting, kind="agenda", artifact_id=item.id)
    insert_record_page(conn, record_id=record, page_number=1, text="Original harbor budget")
    public = f"/public/v1/meetings/{meeting}"
    private = f"/v1/operator/meetings/{meeting}"
    assert client.get("/").status_code == 200
    assert client.get("/operator").status_code == 200
    assert client.get("/public/v1/meetings").json() == []
    assert client.get(public).status_code == 404
    assert client.get(private).status_code == 401
    assert client.get(private, headers=reader).status_code == 403
    assert client.post(private + "/publish", json={"reason": "Reviewed"}).status_code == 401
    reviewed = client.get(private, headers=writer)
    assert reviewed.status_code == 200, reviewed.text
    assert (
        reviewed.json()["detail"]["meeting"]["documents"][0]["citation_url"]
        == "https://example.org/agenda.txt"
    )
    response = client.post(
        private + "/publish", json={"reason": "Compared against source"}, headers=writer
    )
    assert response.status_code == 200, response.text
    assert client.get(public).json()["revision"] == 1
    assert len(client.get("/public/v1/meetings?q=harbor").json()) == 1
    assert client.get("/public/v1/meetings?q=notpresent").json() == []
    response = client.get(public + f"/documents/{record}")
    assert response.content == b"Original harbor budget"
    assert ".txt" in response.headers["Content-Disposition"]
    assert client.get(public + "/documents/9999").status_code == 404
    assert client.get(f"/v1/records/{record}/file").status_code == 401
    correction = {
        "title": "Corrected title",
        "starts_at": "2026-11-02T19:00:00",
        "body_id": body,
        "is_cancelled": False,
        "is_continued": False,
        "reason": "Source rescheduled",
    }
    response = client.patch(private, json=correction, headers=writer)
    assert response.status_code == 200, response.text
    assert client.get(public).json()["detail"]["meeting"]["title"] == "Council meeting"
    assert (
        client.get(private, headers=writer).json()["detail"]["meeting"]["title"]
        == "Corrected title"
    )
    assert (
        client.post(
            private + "/publish", json={"reason": "Reviewed correction"}, headers=writer
        ).json()["revision"]
        == 2
    )
    assert client.get(public).json()["detail"]["meeting"]["title"] == "Corrected title"
    assert (
        client.post(
            private + "/withdraw", json={"reason": "Review requested"}, headers=writer
        ).status_code
        == 200
    )
    assert client.get(public).status_code == 404
    assert client.get(public + f"/documents/{record}").status_code == 404
    assert client.get("/public/v1/meetings?q=harbor").json() == []
    assert client.get(private, headers=writer).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM publication_history").fetchone()[0] == 3


def test_two_bodies_and_invalid_configuration_leave_no_partial_source(
    client, conn, read_write_token
):
    writer = {"Authorization": f"Bearer {read_write_token}"}
    for name in ["Council", "Planning commission"]:
        response = client.post(
            "/v1/operator/bodies", json={"community": "Example", "body": name}, headers=writer
        )
        assert response.status_code == 201, response.text
    overview = client.get("/v1/operator/overview", headers=writer)
    assert overview.status_code == 200, overview.text
    assert len(overview.json()["bodies"]) == 2
    city = overview.json()["jurisdictions"][0]["id"]
    response = client.post(
        "/v1/operator/sources",
        json={
            "jurisdiction_id": city,
            "provider": "not-a-provider",
            "url": "https://example.org/meetings",
            "date_from": "2026-11-01",
            "date_to": "2026-11-12",
        },
        headers=writer,
    )
    assert response.status_code == 422, response.text
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_staged_recording_minutes_and_cancellation_require_republication(
    client, conn, read_write_token, api_storage_root
):
    """Synthetic lifecycle; approval wording belongs to the source document."""
    from townrecord.repo import insert_segment, insert_source, insert_transcript, insert_video

    writer = {"Authorization": f"Bearer {read_write_token}"}
    city = insert_jurisdiction(conn, type="city", name="Lifecycle fixture")
    body = insert_body(conn, jurisdiction_id=city, name="Council")
    meeting = insert_meeting(conn, body_id=body, starts_at="2026-10-01T19:00:00")
    private = f"/v1/operator/meetings/{meeting}"
    public = f"/public/v1/meetings/{meeting}"

    def document(kind, text):
        artifact = artifacts.store(
            conn,
            api_storage_root,
            "record",
            text.encode(),
            "txt",
            meta={"citation_url": f"https://example.org/{kind}.txt"},
        )
        record = insert_record(conn, meeting_id=meeting, kind=kind, artifact_id=artifact.id)
        insert_record_page(conn, record_id=record, page_number=1, text=text)
        return record

    def publish(reason):
        response = client.post(private + "/publish", json={"reason": reason}, headers=writer)
        assert response.status_code == 200, response.text
        return client.get(public).json()

    agenda = document("agenda", "Agenda: harbor works")
    first = publish("Reviewed agenda; recording and minutes not available yet")
    assert first["transcript"] is None
    assert len(first["detail"]["meeting"]["documents"]) == 1
    source = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://youtube.com/@example",
        suggested_by="operator",
        reason="Synthetic acceptance source",
    )
    video = insert_video(
        conn,
        source_id=source,
        platform_video_id="fixture",
        meeting_id=meeting,
        is_primary=True,
        url="https://www.youtube.com/watch?v=fixture",
    )
    artifact = artifacts.store(conn, api_storage_root, "transcript", b"Harbor discussion", "txt")
    transcript = insert_transcript(
        conn, video_id=video, artifact_id=artifact.id, origin="local_speech_to_text"
    )
    insert_segment(
        conn, transcript_id=transcript, start_ms=1000, end_ms=3000, text="Harbor discussion"
    )
    assert client.get(public).json()["transcript"] is None
    recorded = publish("Reviewed later recording; machine transcript is not official minutes")
    assert recorded["transcript"]["segments"][0]["text"] == "Harbor discussion"
    minutes = document(
        "minutes", "Source states: minutes approved October 15. Harbor works passed."
    )
    assert len(client.get(public).json()["detail"]["meeting"]["documents"]) == 1
    complete = publish("Reviewed source-approved minutes received later")
    assert {d["id"] for d in complete["detail"]["meeting"]["documents"]} == {agenda, minutes}
    assert client.get(public + f"/documents/{minutes}").content.startswith(b"Source states")
    response = client.patch(
        private,
        headers=writer,
        json={
            "title": "Continued Council",
            "starts_at": "2026-10-02T19:00:00",
            "body_id": body,
            "is_cancelled": True,
            "is_continued": True,
            "reason": "Synthetic source cancellation and continuation notice",
        },
    )
    assert response.status_code == 200
    assert not client.get(public).json()["detail"]["meeting"]["is_cancelled"]
    changed = publish("Reviewed cancellation notice")
    assert changed["detail"]["meeting"]["is_cancelled"]
    assert changed["detail"]["meeting"]["is_continued"]
    assert changed["revision"] == 4


def test_request_connection_can_finish_on_another_pool_thread(db_path):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from townrecord.api.auth import get_connection

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_path=db_path)))
    dependency = get_connection(request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        connection = pool.submit(next, dependency).result()
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        dependency.close()


def test_operator_tool_setup_and_transcription_are_authorized_and_idempotent(
    client, conn, read_write_token, read_token
):
    from townrecord.repo import insert_source, insert_video

    writer = {"Authorization": f"Bearer {read_write_token}"}
    reader = {"Authorization": f"Bearer {read_token}"}
    setup = "/v1/operator/tools/textflowkit/setup"
    assert client.post(setup).status_code == 401
    assert client.post(setup, headers=reader).status_code == 403
    first = client.post(setup, headers=writer)
    assert first.status_code == 202
    assert client.post(setup, headers=writer).json() == first.json()
    assert client.post("/v1/operator/tools/arbitrary/setup", headers=writer).status_code == 404
    city = insert_jurisdiction(conn, type="city", name="Example")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://youtube.com/@example",
        suggested_by="operator",
        reason="Test source",
    )
    video = insert_video(conn, source_id=source, platform_video_id="example")
    endpoint = f"/v1/operator/videos/{video}/transcribe"
    data = {"reason": "Source captions unavailable"}
    assert client.post(endpoint, json=data).status_code == 401
    assert client.post(endpoint, json=data, headers=reader).status_code == 403
    first = client.post(endpoint, json=data, headers=writer)
    assert first.status_code == 202, first.text
    assert client.post(endpoint, json=data, headers=writer).json() == first.json()
    job = conn.execute(
        "SELECT kind,lane FROM jobs WHERE id=?", (first.json()["job_id"],)
    ).fetchone()
    assert tuple(job) == ("transcribe", "heavy")
    assert (
        client.post("/v1/operator/videos/999999/transcribe", json=data, headers=writer).status_code
        == 404
    )
