"""Operator review and public snapshots over the existing local record service."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ..connector_storage import atomic
from ..jobs import enqueue
from ..repo import (
    all_sources,
    bodies,
    get_body,
    get_meeting,
    insert_body,
    insert_jurisdiction,
    insert_source,
    jurisdictions,
)
from . import connectors as connector_api
from . import read
from .auth import get_connection, require_token

router = APIRouter(tags=["community"])
WEB = Path(__file__).resolve().parent.parent / "web"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BodyInput(Input):
    community: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=200)


class SourceInput(Input):
    jurisdiction_id: int = Field(gt=0)
    url: HttpUrl
    provider: str = Field(min_length=1, max_length=100)
    date_from: date
    date_to: date


class Correction(Input):
    title: str = Field(min_length=1, max_length=500)
    starts_at: str = Field(min_length=10, max_length=40)
    body_id: int = Field(gt=0)
    is_cancelled: bool
    is_continued: bool
    reason: str = Field(min_length=1, max_length=2000)


class Reason(Input):
    reason: str = Field(min_length=1, max_length=2000)


class Association(Reason):
    meeting_id: int = Field(gt=0)


def operator_token(token=Depends(require_token)):
    if token is None or token["scope"] != "read_write":
        raise HTTPException(403, "An operator token with read_write scope is required.")
    return token


def event(conn, token, action, reason, meeting_id=None, detail=None):
    conn.execute(
        "INSERT INTO operator_events(meeting_id,actor,action,reason,detail) VALUES(?,?,?,?,?)",
        (meeting_id, token["name"], action, reason, json.dumps(detail or {})),
    )


def snapshot(request, conn, meeting_id):
    result = {
        "detail": read.get_meeting_detail(meeting_id, conn).model_dump(mode="json"),
        "transcript": None,
        "transcript_note": None,
    }
    try:
        result["transcript"] = read.get_meeting_transcript(
            request, meeting_id, conn, "json"
        ).model_dump(mode="json")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        result["transcript_note"] = str(exc.detail)
    # Public search uses the reviewed document text, never the mutable private index.
    result["document_text"] = [
        {
            "record_id": d["id"],
            "pages": [
                dict(p)
                for p in conn.execute(
                    "SELECT page_number,text FROM record_pages WHERE record_id=? ORDER "
                    "BY page_number",
                    (d["id"],),
                )
            ],
        }
        for d in result["detail"]["meeting"]["documents"]
    ]
    for document in result["detail"]["meeting"]["documents"]:
        row = read._record_row(conn, document["id"])
        document["citation_url"] = json.loads(row["artifact_meta"]).get("citation_url")
    return result


def published(conn, meeting_id):
    row = conn.execute(
        "SELECT * FROM publications WHERE meeting_id=? AND status='published'", (meeting_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "This meeting is not publicly available.")
    return {
        **json.loads(row["snapshot"]),
        "revision": row["revision"],
        "published_at": row["published_at"],
        "publication_note": row["reason"],
    }


@router.get("/", include_in_schema=False)
@router.get("/operator", include_in_schema=False)
def page():
    return FileResponse(WEB / "community.html", media_type="text/html")


@router.get("/assets/{name}", include_in_schema=False)
def asset(name: str):
    if name not in {"community.css", "community.js"}:
        raise HTTPException(404)
    return FileResponse(
        WEB / name, media_type="text/css" if name.endswith("css") else "text/javascript"
    )


@router.get("/v1/operator/overview")
def overview(token=Depends(operator_token), conn=Depends(get_connection)):
    sources = []
    for s in all_sources(conn):
        config = json.loads(s.settings).get("connector", {})
        sources.append({**asdict(s), "coverage": config.get("bounds", {})})
    return {
        "jurisdictions": [asdict(j) for j in jurisdictions(conn)],
        "bodies": [asdict(b) for b in bodies(conn)],
        "sources": sources,
        "jobs": [
            {**dict(r), "checkpoint": json.loads(r["checkpoint"]) if r["checkpoint"] else None}
            for r in conn.execute(
                "SELECT id,kind,state,last_error,attempts,checkpoint FROM jobs ORDER BY id DESC LIMIT 100"
            )
        ],
    }


@router.post("/v1/operator/bodies", status_code=201)
def add_body(data: BodyInput, token=Depends(operator_token), conn=Depends(get_connection)):
    with atomic(conn):
        city = conn.execute(
            "SELECT id FROM jurisdictions WHERE type='city' AND name=? AND parent_id IS NULL",
            (data.community,),
        ).fetchone()
        jid = city["id"] if city else insert_jurisdiction(conn, type="city", name=data.community)
        existing = conn.execute(
            "SELECT id FROM bodies WHERE jurisdiction_id=? AND name=?", (jid, data.body)
        ).fetchone()
        bid = existing["id"] if existing else insert_body(conn, jurisdiction_id=jid, name=data.body)
        event(
            conn, token, "configure_body", "Operator configured coverage", detail={"body_id": bid}
        )
    return {"body_id": bid, "jurisdiction_id": jid}


@router.post("/v1/operator/sources", status_code=201)
def add_source(data: SourceInput, token=Depends(operator_token), conn=Depends(get_connection)):
    if data.date_to < data.date_from:
        raise HTTPException(422, "Coverage end precedes its start.")
    if not conn.execute(
        "SELECT 1 FROM jurisdictions WHERE id=?", (data.jurisdiction_id,)
    ).fetchone():
        raise HTTPException(404, "Configure the community first.")
    url = str(data.url).rstrip("/")
    with atomic(conn):
        existing = conn.execute(
            "SELECT id,jurisdiction_id FROM sources WHERE type='meeting_portal' AND origin=?",
            (url,),
        ).fetchone()
        if existing and existing["jurisdiction_id"] != data.jurisdiction_id:
            raise HTTPException(409, "This source is already assigned to a different community.")
        sid = (
            existing["id"]
            if existing
            else insert_source(
                conn,
                jurisdiction_id=data.jurisdiction_id,
                type="meeting_portal",
                origin=url,
                suggested_by=token["name"],
                reason="Operator configured coverage",
                status="accepted",
            )
        )
        result = connector_api.configure_source(
            sid,
            {
                "provider": data.provider,
                "bounds": {
                    "date_from": data.date_from.isoformat(),
                    "date_to": data.date_to.isoformat(),
                },
            },
            conn,
        )
        event(
            conn, token, "configure_source", "Operator validated source", detail={"source_id": sid}
        )
    return result


@router.post("/v1/operator/sources/{source_id}/sync", status_code=202)
def sync_source(source_id: int, token=Depends(operator_token), conn=Depends(get_connection)):
    from .. import connectors

    local, _ = connectors.configured_source(conn, source_id)
    bounds = connectors.configuration(local).get("bounds", {})
    if not bounds.get("date_from") or not bounds.get("date_to"):
        raise HTTPException(422, "Configure coverage dates first.")
    result = connector_api.sync_source(
        source_id, {"from_date": bounds["date_from"], "to_date": bounds["date_to"]}, conn
    )
    event(conn, token, "retrieve", "Operator requested retrieval", detail=result)
    return result


@router.post("/v1/operator/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: int, token=Depends(operator_token), conn=Depends(get_connection)):
    with atomic(conn):
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found.")
        if row["state"] not in {"failed", "paused"}:
            raise HTTPException(409, "Only failed or paused jobs can be retried.")
        # Provider writes have a separate scoped grant/review workflow.
        if row["kind"] == "connector_operation":
            raise HTTPException(409, "Review provider operations in the connector workspace.")
        prior = conn.execute(
            "SELECT detail FROM operator_events WHERE action='retry' "
            "AND json_extract(detail,'$.previous_job')=? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if prior:
            return {"job_id": json.loads(prior["detail"])["job_id"]}
        new_id = enqueue(conn, row["kind"], json.loads(row["payload"]) if row["payload"] else None)
        event(
            conn,
            token,
            "retry",
            "Operator requested retry",
            detail={"previous_job": job_id, "job_id": new_id},
        )
    return {"job_id": new_id}


def filtered(data, q, body_id, date_from, date_to):
    rows = []
    for value in data:
        m = value["detail"]["meeting"]
        if body_id and str(m["body_id"]) != body_id:
            continue
        if (
            date_from
            and m["starts_at"][:10] < date_from
            or date_to
            and m["starts_at"][:10] > date_to
        ):
            continue
        if q and q.casefold() not in json.dumps(value, ensure_ascii=False).casefold():
            continue
        rows.append(
            {
                "id": m["id"],
                "title": m["title"],
                "starts_at": m["starts_at"],
                "body_name": value["detail"]["body"]["name"],
                "is_cancelled": m["is_cancelled"],
                "publication_status": value.get("publication_status"),
            }
        )
    return sorted(rows, key=lambda m: m["starts_at"], reverse=True)


@router.get("/v1/operator/meetings")
def operator_meetings(
    request: Request,
    q: str = Query("", max_length=200),
    body_id: str = "",
    date_from: str = "",
    date_to: str = "",
    token=Depends(operator_token),
    conn=Depends(get_connection),
):
    data = []
    for r in conn.execute(
        "SELECT m.id,p.status FROM meetings m LEFT JOIN publications p ON "
        "p.meeting_id=m.id ORDER BY m.starts_at DESC"
    ):
        value = snapshot(request, conn, r["id"])
        value["publication_status"] = r["status"] or "Not published"
        data.append(value)
    return filtered(data, q, body_id, date_from, date_to)


@router.get("/v1/operator/meetings/{meeting_id}")
def operator_meeting(
    request: Request, meeting_id: int, token=Depends(operator_token), conn=Depends(get_connection)
):
    value = snapshot(request, conn, meeting_id)
    value["history"] = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM operator_events WHERE meeting_id=? ORDER BY id DESC", (meeting_id,)
        )
    ]
    value["history"] += [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM meeting_changes WHERE meeting_id=? ORDER BY id DESC", (meeting_id,)
        )
    ]
    return value


@router.patch("/v1/operator/meetings/{meeting_id}")
def correct_meeting(
    meeting_id: int, data: Correction, token=Depends(operator_token), conn=Depends(get_connection)
):
    try:
        datetime.fromisoformat(data.starts_at)
    except ValueError as exc:
        raise HTTPException(
            422, "Use an ISO local date/time, preserving any source offset."
        ) from exc
    if get_meeting(conn, meeting_id) is None or get_body(conn, data.body_id) is None:
        raise HTTPException(404, "Meeting or body not found.")
    with atomic(conn):
        conn.execute(
            "UPDATE meetings SET "
            "title=?,starts_at=?,body_id=?,is_cancelled=?,is_continued=? WHERE id=?",
            (
                data.title,
                data.starts_at,
                data.body_id,
                int(data.is_cancelled),
                int(data.is_continued),
                meeting_id,
            ),
        )
        event(conn, token, "correct", data.reason, meeting_id, data.model_dump())
    return {"meeting_id": meeting_id, "public_version_unchanged": True}


@router.post("/v1/operator/meetings/{meeting_id}/publish")
def publish(
    request: Request,
    meeting_id: int,
    data: Reason,
    token=Depends(operator_token),
    conn=Depends(get_connection),
):
    with atomic(conn):
        value = snapshot(request, conn, meeting_id)
        for d in value["detail"]["meeting"]["documents"]:
            read.get_record_file(request, d["id"], conn)
        row = conn.execute(
            "SELECT revision FROM publications WHERE meeting_id=?", (meeting_id,)
        ).fetchone()
        revision = row["revision"] + 1 if row else 1
        encoded = json.dumps(value, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO "
            "publications(meeting_id,revision,status,snapshot,published_at,reason) "
            "VALUES(?,?,'published',?,?,?) "
            "ON CONFLICT(meeting_id) DO UPDATE SET "
            "revision=excluded.revision,status='published',snapshot=excluded.snapshot,published_at=excluded.published_at,reason=excluded.reason",
            (meeting_id, revision, encoded, now, data.reason),
        )
        conn.execute(
            "INSERT INTO "
            "publication_history(meeting_id,revision,snapshot,action,actor,reason) "
            "VALUES(?,?,?,'publish',?,?)",
            (meeting_id, revision, encoded, token["name"], data.reason),
        )
        event(conn, token, "publish", data.reason, meeting_id, {"revision": revision})
    return {"meeting_id": meeting_id, "revision": revision}


@router.post("/v1/operator/meetings/{meeting_id}/withdraw")
def withdraw(
    meeting_id: int, data: Reason, token=Depends(operator_token), conn=Depends(get_connection)
):
    with atomic(conn):
        row = conn.execute(
            "SELECT * FROM publications WHERE meeting_id=?", (meeting_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Meeting has not been published.")
        if row["status"] != "withdrawn":
            revision = row["revision"] + 1
            conn.execute(
                "UPDATE publications SET status='withdrawn',revision=?,reason=? WHERE meeting_id=?",
                (revision, data.reason, meeting_id),
            )
            conn.execute(
                "INSERT INTO "
                "publication_history(meeting_id,revision,snapshot,action,actor,reason) "
                "VALUES(?,?,?,'withdraw',?,?)",
                (meeting_id, revision, row["snapshot"], token["name"], data.reason),
            )
            event(conn, token, "withdraw", data.reason, meeting_id)
    return {"meeting_id": meeting_id, "status": "withdrawn"}


@router.get("/public/v1/bodies")
def public_bodies(conn=Depends(get_connection)):
    found = {}
    for row in conn.execute("SELECT snapshot FROM publications WHERE status='published'"):
        b = json.loads(row["snapshot"])["detail"]["body"]
        found[b["id"]] = b
    return list(found.values())


@router.get("/public/v1/meetings")
def public_meetings(
    q: str = Query("", max_length=200),
    body_id: str = "",
    date_from: str = "",
    date_to: str = "",
    conn=Depends(get_connection),
):
    data = [
        json.loads(r["snapshot"])
        for r in conn.execute("SELECT snapshot FROM publications WHERE status='published'")
    ]
    return filtered(data, q, body_id, date_from, date_to)


@router.get("/public/v1/meetings/{meeting_id}")
def public_meeting(meeting_id: int, conn=Depends(get_connection)):
    return published(conn, meeting_id)


@router.get("/public/v1/meetings/{meeting_id}/documents/{record_id}")
def public_document(
    request: Request, meeting_id: int, record_id: int, conn=Depends(get_connection)
):
    value = published(conn, meeting_id)
    if record_id not in {d["id"] for d in value["detail"]["meeting"]["documents"]}:
        raise HTTPException(404, "This document is not in the public version.")
    result = read.get_record_file(request, record_id, conn)
    result.headers["Content-Disposition"] = (
        f'attachment; filename="townrecord-document-{record_id}{Path(result.path).suffix}"'
    )
    result.headers["X-Content-Type-Options"] = "nosniff"
    result.headers["Cache-Control"] = "no-store"
    return result


@router.get("/v1/operator/meetings/{meeting_id}/handoff")
def export_handoff(
    request: Request, meeting_id: int, token=Depends(operator_token), conn=Depends(get_connection)
):
    value = published(conn, meeting_id)
    package = {
        "schema": "townrecord-evidence-v1",
        "meeting_id": meeting_id,
        "revision": value["revision"],
        "evidence": value,
    }
    return JSONResponse(
        package,
        headers={
            "Content-Disposition": f'attachment; filename="townrecord-meeting-{meeting_id}.json"'
        },
    )


@router.post("/v1/operator/records/{record_id}/associate")
def associate_record(
    record_id: int, data: Association, token=Depends(operator_token), conn=Depends(get_connection)
):
    with atomic(conn):
        row = conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None or get_meeting(conn, data.meeting_id) is None:
            raise HTTPException(404, "Record or destination meeting not found.")
        # Keep the complete revision chain together. Existing public snapshots
        # remain unchanged until the operator explicitly republishes them.
        ids = {record_id}
        while True:
            related = {
                r[0]
                for rid in ids
                for r in conn.execute(
                    "SELECT current_record_id FROM record_revisions WHERE previous_record_id=? "
                    "UNION SELECT previous_record_id FROM record_revisions "
                    "WHERE current_record_id=?",
                    (rid, rid),
                )
            }
            if related <= ids:
                break
            ids |= related
        for rid in ids:
            conn.execute(
                "UPDATE records SET origin_meeting_id=COALESCE(origin_meeting_id,meeting_id),"
                "meeting_id=? WHERE id=?",
                (data.meeting_id, rid),
            )
            conn.execute(
                "UPDATE connector_links SET meeting_id=? WHERE record_id=?", (data.meeting_id, rid)
            )
        detail = {
            "record_ids": sorted(ids),
            "from_meeting": row["meeting_id"],
            "to_meeting": data.meeting_id,
        }
        for mid in {row["meeting_id"], data.meeting_id}:
            event(conn, token, "associate_document", data.reason, mid, detail)
    return {"meeting_id": data.meeting_id, "public_version_unchanged": True}


@router.post("/v1/operator/videos/{video_id}/associate")
def associate_video(
    video_id: int, data: Association, token=Depends(operator_token), conn=Depends(get_connection)
):
    with atomic(conn):
        row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if row is None or get_meeting(conn, data.meeting_id) is None:
            raise HTTPException(404, "Recording or destination meeting not found.")
        conn.execute("UPDATE videos SET is_primary=0 WHERE meeting_id=?", (data.meeting_id,))
        conn.execute(
            "UPDATE videos SET meeting_id=?,is_primary=1 WHERE id=?", (data.meeting_id, video_id)
        )
        detail = {
            "video_id": video_id,
            "from_meeting": row["meeting_id"],
            "to_meeting": data.meeting_id,
        }
        for mid in {row["meeting_id"], data.meeting_id} - {None}:
            event(conn, token, "associate_recording", data.reason, mid, detail)
            enqueue(conn, "align_meeting", {"meeting_id": mid})
    return {"meeting_id": data.meeting_id, "public_version_unchanged": True}
