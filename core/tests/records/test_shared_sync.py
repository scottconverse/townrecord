"""Saved calendar sync through the real jobs and isolated SQLite database."""

import base64
import json

from civic_data_connectors import create_engine
from civic_data_connectors.interchange import read_bundle
from civic_data_connectors.providers import Response
from civic_data_connectors.storage import SQLiteStorage

from townrecord.connector_storage import AppStorage
from townrecord.jobs import read_checkpoint
from townrecord.repo import insert_source


def test_read_tokens_forward_only_for_the_configured_provider(monkeypatch):
    from types import SimpleNamespace

    from townrecord.connectors import source_context

    monkeypatch.setenv(
        "TOWNRECORD_CONNECTOR_CREDENTIALS",
        json.dumps(
            {
                "wistia_token": "read-token",
                "legistar_token": "different-account",
                "wistia": {"token": "write-token"},
                "telvue": {"access_key": "unrelated"},
            }
        ),
    )
    local = SimpleNamespace(
        jurisdiction_id=7,
        settings=json.dumps({"connector": {"provider": "wistia", "namespace": "account-a"}}),
    )
    caller = source_context(local)
    assert caller.credentials == {"wistia_token": "read-token", "wistia": {"token": "write-token"}}
    assert caller.allowed_namespaces == frozenset({"account-a"})
    assert caller.tenant == "7"


def test_account_fetch_job_preserves_partial_and_scopes_credentials(
    area, sync, wired, monkeypatch, client, read_write_token
):
    from townrecord import connectors

    origin = "https://connect.telvue.com"
    secret = "synthetic-access-secret"
    monkeypatch.setenv(
        "TOWNRECORD_CONNECTOR_CREDENTIALS",
        json.dumps(
            {
                "telvue": {
                    "origin": origin,
                    "namespace": "fixture-account",
                    "tenant": str(area.jurisdiction_id),
                    "access_key": secret,
                    "secret_passphrase": "synthetic-passphrase",
                }
            }
        ),
    )
    calls = []

    class Network:
        def request(self, url, **options):
            calls.append((url, options))
            return Response(
                url,
                json.dumps({"videos": [{"id": 32261, "title": "Council"}]}).encode(),
                "application/json",
            )

    monkeypatch.setattr(
        connectors,
        "engine_for",
        lambda conn, **kw: create_engine(
            storage=AppStorage(conn), provider_transport=Network(), **kw
        ),
    )
    source = insert_source(
        sync.conn,
        jurisdiction_id=area.jurisdiction_id,
        type="meeting_portal",
        origin=origin,
        suggested_by="test",
        reason="account fixture",
        status="accepted",
        body_id=area.body_id,
    )
    headers = {"Authorization": f"Bearer {read_write_token}"}
    path = f"/v1/connectors/sources/{source}"
    configured = client.put(
        path, json={"provider": "telvue", "namespace": "fixture-account"}, headers=headers
    )
    assert configured.status_code == 200, configured.text
    assert (
        client.post(
            path + "/sync", json={"fetch_fields": {"credentials": {}}}, headers=headers
        ).status_code
        == 422
    )
    queued = client.post(
        path + "/sync", json={"fetch_fields": {"api_resource": "videos"}}, headers=headers
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()["job_id"]
    sync.lane("normal")
    assert sync.job(job)["state"] == "done", sync.job(job)["last_error"]
    checkpoint = read_checkpoint(sync.conn, job)
    assert checkpoint["status"] == "partial" and checkpoint["coverage"]["api_resource"] == "videos"
    assert checkpoint["data"]["bundles"] and checkpoint["data"]["meetings"] == []
    assert calls[0][0] == origin + "/api/v2/videos.json"
    assert secret not in json.dumps(checkpoint) and secret not in sync.job(job)["payload"]

    essential = "https://meetingsgov.devmunicode.com"
    monkeypatch.setenv(
        "TOWNRECORD_CONNECTOR_CREDENTIALS",
        json.dumps(
            {
                "municode-meetings": {
                    "origin": essential,
                    "namespace": "fixture-account",
                    "tenant": str(area.jurisdiction_id),
                    "token": "synthetic-bearer-secret",
                }
            }
        ),
    )
    raw_source = insert_source(
        sync.conn,
        jurisdiction_id=area.jurisdiction_id,
        type="meeting_portal",
        origin=essential,
        suggested_by="test",
        reason="unmapped account fixture",
        status="accepted",
        body_id=area.body_id,
    )
    raw_path = f"/v1/connectors/sources/{raw_source}"
    assert (
        client.put(
            raw_path,
            json={"provider": "municode-meetings", "namespace": "fixture-account"},
            headers=headers,
        ).status_code
        == 200
    )
    queued = client.post(
        raw_path + "/sync",
        json={"fetch_fields": {"api_resource": "meeting", "external_id": "42"}},
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    raw_job = queued.json()["job_id"]
    sync.lane("normal")
    assert sync.job(raw_job)["state"] == "done", sync.job(raw_job)["last_error"]
    raw = read_checkpoint(sync.conn, raw_job)
    assert raw["status"] == "partial" and "raw response only" in raw["coverage"]["schema_mapping"]
    assert raw["data"]["bundles"] and raw["data"]["meetings"] == []
    monkeypatch.setenv(
        "TOWNRECORD_CONNECTOR_CREDENTIALS",
        json.dumps(
            {
                "municode-meetings": {
                    "origin": essential,
                    "namespace": "other-account",
                    "tenant": str(area.jurisdiction_id),
                    "token": "synthetic-bearer-secret",
                }
            }
        ),
    )
    before = len(calls)
    denied = client.post(
        raw_path + "/sync",
        json={"fetch_fields": {"api_resource": "meeting", "external_id": "42"}},
        headers=headers,
    )
    sync.lane("normal")
    assert sync.job(denied.json()["job_id"])["state"] == "failed"
    assert len(calls) == before


def test_calendar_sync_is_idempotent_and_distinct_sources_remain_separate(area, sync):
    content = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:abc\r\n"
        b"DTSTART:20260908T190000\r\nSUMMARY:Council meeting\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    settings = json.dumps(
        {"connector": {"provider": "first-party", "input_format": "public-meeting-ics"}}
    )

    def source(origin):
        return insert_source(
            sync.conn,
            jurisdiction_id=area.jurisdiction_id,
            type="meeting_portal",
            origin=origin,
            suggested_by="test",
            reason="calendar",
            status="accepted",
            body_id=area.body_id,
            settings=settings,
        )

    def run(source_id):
        job = sync.queue(
            "sync_connector",
            {
                "source_id": source_id,
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
        sync.lane("normal")
        assert sync.job(job)["state"] == "done", sync.job(job)["last_error"]
        return read_checkpoint(sync.conn, job)

    first = source("https://calendar.test.invalid/meetings.ics")
    assert run(first)["status"] == "completed"
    assert run(first)["status"] == "completed"
    assert sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 1

    second = source("https://other.test.invalid/meetings.ics")
    result = run(second)
    assert result["status"] == "completed"
    assert sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 2
    assert run(second)["status"] == "completed"
    assert sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 2


def test_escribe_document_reaches_existing_search_and_export(
    area, sync, wired, monkeypatch, tmp_path, client, read_token, read_write_token
):
    from townrecord import connectors

    origin = "https://synthetic.escribemeetings.com"

    class Network:
        def request(self, url, **options):
            if options.get("method") == "POST":
                meeting = {
                    "ID": "42",
                    "MeetingName": "City Council",
                    "StartDate": "2026/09/08 19:00:00",
                    "Url": "/Meeting.aspx?Id=42",
                    "MeetingDocumentLink": [
                        {"Type": "Agenda", "Title": "Agenda", "Url": "/agenda.txt"}
                    ],
                }
                return Response(url, json.dumps({"d": [meeting]}).encode(), "application/json")
            assert url == origin + "/agenda.txt"
            return Response(url, b"Harbor sidewalk allocation.", "text/plain")

    monkeypatch.setattr(
        connectors,
        "engine_for",
        lambda conn, **kw: create_engine(
            storage=AppStorage(conn), provider_transport=Network(), **kw
        ),
    )
    source = insert_source(
        sync.conn,
        jurisdiction_id=area.jurisdiction_id,
        type="meeting_portal",
        origin=origin,
        suggested_by="test",
        reason="meeting source",
        status="accepted",
        body_id=area.body_id,
    )
    writer = {"Authorization": f"Bearer {read_write_token}"}
    reader = {"Authorization": f"Bearer {read_token}"}
    path = f"/v1/connectors/sources/{source}"
    assert client.put(path, json={"provider": "escribe"}, headers=reader).status_code == 403
    configured = client.put(path, json={"provider": "escribe"}, headers=writer)
    assert configured.status_code == 200, configured.text
    queued = client.post(
        path + "/sync", json={"from_date": "2026-09-01", "to_date": "2026-09-30"}, headers=writer
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()["job_id"]
    sync.lane("normal")
    sync.lane("heavy")
    assert sync.job(job)["state"] == "done", sync.job(job)["last_error"]
    downloads = sync.jobs_of_kind("download_connector")
    assert len(downloads) == 1 and downloads[0]["state"] == "done"
    assert (
        sync.conn.execute(
            "SELECT COUNT(*) FROM record_page_search WHERE record_page_search MATCH ?",
            ("sidewalk",),
        ).fetchone()[0]
        == 1
    )
    assert (
        sync.conn.execute("SELECT COUNT(*) FROM citations WHERE source_id=?", (source,)).fetchone()[
            0
        ]
        == 1
    )
    assert client.get("/v1/search", params={"q": "sidewalk"}).status_code == 401
    headers = {"Authorization": f"Bearer {read_token}"}
    search = client.get("/v1/search", params={"q": "sidewalk"}, headers=headers)
    assert search.status_code == 200 and "sidewalk" in search.text.lower()
    response = client.get(f"/v1/connectors/sources/{source}/export", headers=headers)
    assert response.status_code == 200, response.text
    bundle = read_bundle(response.content, capture_storage=SQLiteStorage(tmp_path / "import.db"))
    assert len(bundle["records"]) == 1
    for format in ("json", "jsonl"):
        portable = client.get(
            f"/v1/connectors/sources/{source}/export", params={"format": format}, headers=headers
        )
        assert portable.status_code == 200, portable.text
        assert len(read_bundle(portable.content, format=format)["records"]) == 1
    for format in ("csv", "ics"):
        projection = client.get(
            f"/v1/connectors/sources/{source}/export", params={"format": format}, headers=headers
        )
        assert projection.status_code == 200, projection.text
        assert projection.content


def test_shared_packet_limit_retry_and_reopened_worker_keep_record_identity(
    area, sync, wired, monkeypatch, client, read_write_token, db_path
):
    from townrecord import connectors
    from townrecord.db import connect

    origin = "https://synthetic.escribemeetings.com"
    document = b"x" * 4096
    download_limits = []
    monkeypatch.setenv("TOWNRECORD_PACKET_LIMIT_BYTES", "1024")

    class Network:
        def request(self, url, **options):
            if options.get("method") == "POST":
                meeting = {
                    "ID": "42",
                    "MeetingName": "City Council",
                    "StartDate": "2026/09/08 19:00:00",
                    "Url": "/Meeting.aspx?Id=42",
                    "MeetingDocumentLink": [{"Type": "Agenda", "Url": "/packet.txt"}],
                }
                return Response(url, json.dumps({"d": [meeting]}).encode(), "application/json")
            assert url == origin + "/packet.txt"
            download_limits.append(options["byte_limit"])
            return Response(url, document, "text/plain")

    monkeypatch.setattr(
        connectors,
        "engine_for",
        lambda conn, **kw: create_engine(
            storage=AppStorage(conn), provider_transport=Network(), **kw
        ),
    )
    source = insert_source(
        sync.conn,
        jurisdiction_id=area.jurisdiction_id,
        type="meeting_portal",
        origin=origin,
        suggested_by="test",
        reason="bounded shared packet",
        status="accepted",
        body_id=area.body_id,
    )
    headers = {"Authorization": f"Bearer {read_write_token}"}
    path = f"/v1/connectors/sources/{source}"
    assert client.put(path, json={"provider": "escribe"}, headers=headers).status_code == 200

    def run():
        response = client.post(
            path + "/sync",
            json={"from_date": "2026-09-01", "to_date": "2026-09-30"},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        sync.lane("normal")
        sync.lane("heavy")  # Each lane creates a new Runner using the file database.
        return sync.jobs_of_kind("download_connector")[-1]

    rejected = run()
    assert rejected["state"] == "failed"
    checkpoint = read_checkpoint(sync.conn, rejected["id"])
    assert checkpoint["status"] == "partial" and checkpoint["errors"]
    assert sync.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    assert download_limits == [1024]
    document = b"Harbor sidewalk allocation after retry."
    assert run()["state"] == "done"
    fresh = connect(db_path)
    try:
        before = [tuple(row) for row in fresh.execute("SELECT id,artifact_id FROM records")]
        citations = [tuple(row) for row in fresh.execute("SELECT id,record_id FROM citations")]
        assert len(before) == len(citations) == 1
        assert (
            fresh.execute(
                "SELECT COUNT(*) FROM record_page_search WHERE record_page_search MATCH 'sidewalk'"
            ).fetchone()[0]
            == 1
        )
    finally:
        fresh.close()
    assert run()["state"] == "done"
    assert (
        read_checkpoint(sync.conn, sync.jobs_of_kind("download_connector")[-1]["id"])[
            "already_stored"
        ]
        is True
    )
    assert [tuple(row) for row in sync.conn.execute("SELECT id,artifact_id FROM records")] == before
    assert [
        tuple(row) for row in sync.conn.execute("SELECT id,record_id FROM citations")
    ] == citations
    assert download_limits == [1024, 1024, 1024]
    document = b"Revised allocation."
    assert run()["state"] == "done"
    assert [
        tuple(row)
        for row in sync.conn.execute(
            "SELECT id,artifact_id FROM records WHERE id=?", (before[0][0],)
        )
    ] == before
    assert sync.conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0] == 1
    document = b"Harbor sidewalk allocation after retry."
    assert run()["state"] == "done"
    assert sync.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3
    assert sync.conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0] == 2


def test_configured_native_write_jobs_revalidate_grants_and_readback(
    area, sync, wired, monkeypatch, client, read_write_token
):
    from urllib.parse import parse_qs, urlsplit

    from civic_data_connectors.write_adapters import default_adapters
    from civic_data_connectors.write_transport import WriteResponse

    from townrecord import connectors

    class Network:
        mock = True

        def __init__(self):
            self.rows = {}
            self.calls = []

        def send(self, url, *, method, body, headers, byte_limit, deadline_seconds):
            parsed = urlsplit(url)
            assert parsed.hostname == "webapi.legistar.com"
            assert parsed.path.startswith("/v1/fixture-city/Events")
            assert parse_qs(parsed.query)["token"] == ["synthetic-only"]
            self.calls.append(method)
            if method == "POST":
                row = json.loads(body)
                row.update(EventId=101, EventRowVersion="AQ==")
                self.rows["101"] = row
            else:
                assert method == "GET"
                row = self.rows[parsed.path.rsplit("/", 1)[-1]]
            return WriteResponse(200, json.dumps(row).encode())

    network = Network()
    monkeypatch.setattr(
        connectors,
        "engine_for",
        lambda conn: create_engine(
            storage=AppStorage(conn), write_adapters=default_adapters(network)
        ),
    )
    namespace = "//webapi.legistar.com/v1/fixture-city"
    monkeypatch.setenv(
        "TOWNRECORD_CONNECTOR_CREDENTIALS",
        json.dumps(
            {
                "legistar": {
                    "namespace": namespace,
                    "token": "synthetic-only",
                    "permissions": ["event:create"],
                }
            }
        ),
    )
    token_id = sync.conn.execute("SELECT id FROM api_tokens WHERE scope='read_write'").fetchone()[0]
    grants = {str(token_id): ["write:plan", "write:commit"]}
    monkeypatch.setenv("TOWNRECORD_CONNECTOR_WRITE_GRANTS", json.dumps(grants))
    source = insert_source(
        sync.conn,
        jurisdiction_id=area.jurisdiction_id,
        type="meeting_portal",
        origin="https://webapi.legistar.com/v1/fixture-city/events",
        suggested_by="test",
        reason="synthetic authorized write",
        status="accepted",
        body_id=area.body_id,
    )
    headers = {"Authorization": f"Bearer {read_write_token}"}
    path = f"/v1/connectors/sources/{source}"
    configured = client.put(
        path, json={"provider": "legistar", "namespace": namespace}, headers=headers
    )
    assert configured.status_code == 200, configured.text
    queued = client.post(
        path + "/plan",
        json={
            "changes": [
                {
                    "key": "event-1",
                    "entity": "event",
                    "operation": "create",
                    "fields": {"EventDate": "2026-11-11T00:00:00", "EventBodyId": 4},
                }
            ]
        },
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()["job_id"]
    sync.lane("normal")
    assert sync.job(job)["state"] == "done", sync.job(job)["last_error"]
    planned = read_checkpoint(sync.conn, job)
    assert planned["status"] == "completed", planned
    approval = {key: planned["data"][key] for key in ("plan_id", "plan_hash")}
    assert not network.calls
    queued = client.post(path + "/commit", json=approval, headers=headers)
    assert queued.status_code == 202, queued.text
    job = queued.json()["job_id"]
    # Grants are checked again inside the durable worker before any native request.
    monkeypatch.setenv("TOWNRECORD_CONNECTOR_WRITE_GRANTS", "{}")
    sync.lane("normal")
    assert sync.job(job)["state"] == "failed"
    assert not network.calls
    monkeypatch.setenv("TOWNRECORD_CONNECTOR_WRITE_GRANTS", json.dumps(grants))
    queued = client.post(path + "/commit", json=approval, headers=headers)
    assert queued.status_code == 202, queued.text
    job = queued.json()["job_id"]
    sync.lane("normal")
    assert sync.job(job)["state"] == "done", sync.job(job)["last_error"]
    committed = read_checkpoint(sync.conn, job)
    assert committed["status"] == "completed", committed
    assert committed["data"]["mock"] is True
    assert committed["data"]["live_acceptance"] == "NOT_RUN"
    assert committed["data"]["items"][0]["readback"]["EventBodyId"] == 4
    receipt = client.get(path + "/receipts/" + approval["plan_id"], headers=headers)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["data"] == committed["data"]
    assert "synthetic-only" not in receipt.text
    duplicate = client.post(path + "/commit", json=approval, headers=headers)
    sync.lane("normal")
    assert sync.job(duplicate.json()["job_id"])["state"] == "done"
    assert network.calls.count("POST") == 1
