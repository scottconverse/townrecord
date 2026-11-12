"""Authenticated configuration and fast queue entry points for the shared SDK."""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from civic_data_connectors import Source
from civic_data_connectors.interchange import open_artifact
from civic_data_connectors.storage import update_operation
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from .. import connectors
from ..connector_storage import AppStorage
from ..jobs import enqueue, get
from ..records import shared
from ..repo import all_sources, get_source
from .auth import get_connection, require_token

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])


def _exact(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise HTTPException(422, "The request contains unsupported fields.")


def _source(conn, source_id):
    try:
        return connectors.configured_source(conn, source_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/capabilities")
def capabilities(conn: sqlite3.Connection = Depends(get_connection)):
    return connectors.engine_for(conn).execute("providers", {}).public_dict()


@router.get("/sources")
def sources(conn: sqlite3.Connection = Depends(get_connection)):
    return [
        {
            "id": source.id,
            "origin": source.origin,
            "status": source.status,
            "body_id": source.body_id,
            "connector": connectors.configuration(source),
        }
        for source in all_sources(conn)
        if source.type == "meeting_portal"
    ]


@router.put("/sources/{source_id}")
def configure_source(
    source_id: int,
    configuration: dict,
    conn: sqlite3.Connection = Depends(get_connection),
):
    """Configure an existing accepted source without rewriting other settings."""
    _exact(configuration, {"provider", "namespace", "body", "input_format", "options", "bounds"})
    local = get_source(conn, source_id)
    if local is None or local.type != "meeting_portal":
        raise HTTPException(404, "There is no meeting portal source with this ID.")
    try:
        source = Source.from_dict(
            {
                **{key: value for key, value in configuration.items() if key != "bounds"},
                "url": local.origin,
            }
        )
        result = connectors.engine_for(conn).execute(
            "validate_source",
            {"source": source.public_dict()},
            context=connectors.source_context(local),
        )
        if result.status not in {"completed", "partial"}:
            raise HTTPException(422, result.public_dict())
        supplied = result.data.get("source", source.public_dict())
        settings = json.loads(local.settings)
        settings["connector"] = {
            **{key: value for key, value in supplied.items() if key != "url"},
            "bounds": configuration.get("bounds", {}),
        }
        from dataclasses import replace

        candidate = replace(local, settings=json.dumps(settings))
        connectors.bounds_for(candidate)
        conn.execute("UPDATE sources SET settings=? WHERE id=?", (candidate.settings, source_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "source_id": source_id,
        "connector": settings["connector"],
        "validation": result.public_dict(),
    }


@router.post("/sources/{source_id}/sync", status_code=202)
def sync_source(
    source_id: int,
    payload: dict,
    conn: sqlite3.Connection = Depends(get_connection),
):
    _exact(payload, {"from_date", "to_date", "content_base64", "fetch_fields"})
    local, _source_value = _source(conn, source_id)
    from datetime import date

    from ..records.portal import sync_request

    try:
        if "fetch_fields" in payload:
            if "content_base64" in payload or "from_date" in payload or "to_date" in payload:
                raise ValueError(
                    "Account fetch uses documented provider fields, "
                    "not a saved capture or date window."
                )
            _exact(payload["fetch_fields"], {"api_resource", "external_id", "offset", "filters"})
        sync_request({"source_id": source_id, **payload}, today=date.today())
        if "content_base64" in payload:
            limit = connectors.bounds_for(local).parser_bytes
            if len(payload["content_base64"]) > 4 * ((limit + 2) // 3):
                raise ValueError("The capture exceeds the parser byte limit.")
            if len(base64.b64decode(payload["content_base64"], validate=True)) > limit:
                raise ValueError("The capture exceeds the parser byte limit.")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"job_id": enqueue(conn, shared.SYNC_CONNECTOR, {"source_id": source_id, **payload})}


def _job(conn, job_id, token):
    row = get(conn, job_id)
    if row is None or row["kind"] not in {
        shared.SYNC_CONNECTOR,
        shared.DOWNLOAD_CONNECTOR,
        shared.CONNECTOR_OPERATION,
    }:
        raise HTTPException(404, "There is no connector job with this ID.")
    payload = json.loads(row["payload"])
    local, _source_value = _source(conn, payload["source_id"])
    context = connectors.source_context(local, operation_id=shared.operation_id(job_id))
    if row["kind"] == shared.CONNECTOR_OPERATION:
        if payload["actor_token_id"] != token["id"]:
            raise HTTPException(404, "There is no connector job in this caller's scope.")
        context = connectors.write_context(conn, local.id, token["id"])
    return row, context


@router.get("/jobs/{job_id}")
def job_status(
    job_id: int,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    row, _context_value = _job(conn, job_id, token)
    return {
        "job_id": job_id,
        "kind": row["kind"],
        "state": row["state"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "result": json.loads(row["checkpoint"]) if row["checkpoint"] else None,
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    row, context = _job(conn, job_id, token)
    if row["state"] in {"done", "failed"}:
        return {"job_id": job_id, "cancel_requested": False, "state": row["state"]}
    update_operation(
        AppStorage(conn),
        shared.operation_id(job_id),
        {"cancel_requested": True},
        scope=context.scope,
    )
    return {"job_id": job_id, "cancel_requested": True}


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: int,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    row, _context_value = _job(conn, job_id, token)
    if row["state"] not in {"failed", "paused", "done"}:
        raise HTTPException(409, "The original job is still active.")
    return {"job_id": enqueue(conn, row["kind"], json.loads(row["payload"]))}


@router.get("/sources/{source_id}/search")
def search_source(
    source_id: int,
    q: str = "",
    phrase: str = "",
    conn: sqlite3.Connection = Depends(get_connection),
):
    local, source = _source(conn, source_id)
    return (
        connectors.engine_for(conn)
        .execute(
            "search",
            {
                "query": {
                    "terms": [q] if q else [],
                    "phrases": [phrase] if phrase else [],
                    "providers": [source.provider],
                    **({"namespaces": [source.namespace]} if source.namespace else {}),
                }
            },
            context=connectors.source_context(local),
        )
        .public_dict()
    )


@router.get("/sources/{source_id}/export")
def export_source(
    source_id: int,
    format: str = "zip",
    conn: sqlite3.Connection = Depends(get_connection),
):
    local, _source_value = _source(conn, source_id)
    engine, context = connectors.engine_for(conn), connectors.source_context(local)
    records = [
        value.value
        for row in conn.execute(
            "SELECT identity_key FROM connector_links WHERE scope=? AND source_id=?",
            (context.scope, source_id),
        )
        if (value := engine.storage.get("records", row["identity_key"], scope=context.scope))
    ]
    result = engine.execute(
        "export",
        {
            "records": records,
            "format": format,
            "delivery": "storage",
            "bounds": asdict(connectors.bounds_for(local)),
        },
        context=context,
    )
    if result.status not in {"completed", "partial"}:
        raise HTTPException(422, result.public_dict())
    metadata, stream = open_artifact(engine, result.data["artifact_id"], context=context)

    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "X-Content-SHA256": metadata["sha256"],
            "Content-Disposition": 'attachment; filename="townrecord-export.'
            + (format if format in {"json", "jsonl", "zip", "csv", "ics"} else "bin")
            + '"',
        },
    )


def _enqueue_write(conn, source_id, token, operation, payload):
    local, _source_value = _source(conn, source_id)
    context = connectors.write_context(conn, local.id, token["id"])
    permission = "write:plan" if operation == "plan_write" else "write:commit"
    if permission not in context.authorization:
        raise HTTPException(
            403, "The server has not granted this token the requested write operation."
        )
    return {
        "job_id": enqueue(
            conn,
            shared.CONNECTOR_OPERATION,
            {
                "source_id": source_id,
                "actor_token_id": token["id"],
                "operation": operation,
                "request": payload,
            },
        )
    }


@router.post("/sources/{source_id}/plan", status_code=202)
def plan_write(
    source_id: int,
    payload: dict,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    _exact(payload, {"changes", "bounds"})
    return _enqueue_write(conn, source_id, token, "plan_write", payload)


@router.post("/sources/{source_id}/commit", status_code=202)
def commit_write(
    source_id: int,
    payload: dict,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    _exact(payload, {"plan_id", "plan_hash"})
    if set(payload) != {"plan_id", "plan_hash"}:
        raise HTTPException(422, "Commit requires the exact reviewed plan_id and plan_hash.")
    return _enqueue_write(conn, source_id, token, "commit_write", payload)


@router.get("/sources/{source_id}/receipts/{plan_id}")
def receipt(
    source_id: int,
    plan_id: str,
    token=Depends(require_token),
    conn: sqlite3.Connection = Depends(get_connection),
):
    local, _source_value = _source(conn, source_id)
    return (
        connectors.engine_for(conn)
        .receipt(
            {"plan_id": plan_id}, context=connectors.write_context(conn, local.id, token["id"])
        )
        .public_dict()
    )


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def connector_ui(request: Request):
    """Offline browser control panel, protected by the application's token check."""
    return HTMLResponse(Path(__file__).with_name("connectors.html").read_text(encoding="utf-8"))
