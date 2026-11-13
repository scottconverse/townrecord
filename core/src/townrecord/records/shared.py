"""Provider-neutral jobs; native IDs, PDF reading and media jobs stay native."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime

from civic_data_connectors import OperationResult
from civic_data_connectors.engine import canonical_hash, source_identity_key
from civic_data_connectors.storage import operation_cancelled, update_operation

from .. import artifacts, connectors
from ..connector_storage import AppStorage, atomic
from ..jobs import JobContext
from ..repo import (
    get_body,
    get_meeting,
    insert_meeting,
    insert_record,
    insert_record_citation,
    insert_record_page,
    record_source_failure,
    record_source_success,
    upsert_agenda_item,
)
from . import portal

SYNC_CONNECTOR = "sync_connector"
DOWNLOAD_CONNECTOR = "download_connector"
CONNECTOR_OPERATION = "connector_operation"


def operation_id(job_id: int) -> str:
    return f"townrecord-job:{job_id}"


def _context(ctx, local):
    context = connectors.source_context(local, operation_id=operation_id(ctx.job_id))
    if ctx.stop_event is not None:
        context = replace(context, cancellation=ctx.stop_event)
    return context


def _save_capture(store, capture, scope, limit):
    capture = deepcopy(capture)
    encoded = capture.pop("content_base64", None)
    if encoded is not None:
        content = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(content).hexdigest() != capture["sha256"]:
            raise ValueError("The shared capture hash does not match its bytes.")
        capture["storage_reference"] = store.save_capture(
            io.BytesIO(content), scope=scope, max_bytes=limit
        )
    return capture


def _meeting(conn, local, record, scope):
    key = source_identity_key(record["identity"])
    previous = conn.execute(
        "SELECT meeting_id FROM connector_links WHERE scope=? AND identity_key=?", (scope, key)
    ).fetchone()
    normalized = record["normalized"]
    if record["identity"]["record_type"] not in {"meeting", "event"}:
        return None
    body = portal.resolve_body(conn, local, normalized.get("body") or "")
    if body is None and not normalized.get("body") and local.body_id is not None:
        body = get_body(conn, local.body_id)
    if body is None:
        raise ValueError("The shared meeting names no configured body of this jurisdiction.")
    start = normalized.get("scheduled_start") or normalized.get("source_start")
    if not start:
        start = normalized.get("meeting_date")
    if not isinstance(start, str) or not start:
        raise ValueError(
            "The shared meeting has no supplied date; it remains in captured evidence."
        )
    # A supplied date without a time stays a date, not a fabricated midnight.
    datetime.fromisoformat(start.replace("Z", "+00:00"))
    meeting_id = None if previous is None else previous["meeting_id"]
    if meeting_id is None or get_meeting(conn, meeting_id) is None:
        collision = conn.execute(
            "SELECT id FROM meetings WHERE body_id=? AND starts_at=?", (body.id, start)
        ).fetchone()
        if collision is not None:
            raise ValueError(
                f"Meeting identity collision with local meeting {collision['id']}; "
                "explicit review is required, and existing portal IDs were not changed."
            )
        meeting_id = insert_meeting(
            conn,
            body_id=body.id,
            starts_at=start,
            title=normalized.get("title"),
        )
    status = normalized.get("source_status")
    cancelled = (
        str(status).casefold() in {"cancelled", "canceled"}
        or isinstance(status, dict)
        and bool(status.get("canceled") or status.get("cancelled"))
        or portal.cancellation_signal(normalized.get("title") or "", ()) is not None
    )
    continued = portal.continuation_signal(normalized.get("title") or "") is not None
    conn.execute(
        "UPDATE meetings SET title=?, starts_at=?, is_cancelled=MAX(is_cancelled,?), "
        "is_continued=MAX(is_continued,?) WHERE id=?",
        (normalized.get("title"), start, int(cancelled), int(continued), meeting_id),
    )
    for index, item in enumerate(normalized.get("agenda_items") or [], start=1):
        number = str(
            item.get("agenda_number") or item.get("number") or item.get("external_id") or index
        )
        upsert_agenda_item(
            conn,
            meeting_id=meeting_id,
            number=number,
            title=item.get("title") or "",
            identifiers=portal.identifiers_of(item.get("title") or ""),
        )
    return meeting_id


def sync_connector(ctx: JobContext) -> None:
    request = portal.sync_request(ctx.payload, today=ctx.clock().date())
    local, source = connectors.configured_source(ctx.conn, request.source_id)
    store, engine = AppStorage(ctx.conn), connectors.engine_for(ctx.conn)
    context = _context(ctx, local)
    bounds = connectors.bounds_for(
        local, date_from=request.date_from.isoformat(), date_to=request.date_to.isoformat()
    )
    if operation_cancelled(store, context):
        ctx.save_checkpoint({"status": "cancelled", "operation_id": context.operation_id})
        return
    if "fetch_fields" in ctx.payload:
        fields = ctx.payload["fetch_fields"]
        if not isinstance(fields, dict) or set(fields) - {
            "api_resource",
            "external_id",
            "offset",
            "filters",
        }:
            raise ValueError("Unsupported account fetch fields.")
        result = engine.execute(
            "fetch",
            {
                "source": source.public_dict(),
                **fields,
                "bounds": asdict(replace(bounds, date_from=None, date_to=None)),
            },
            context=context,
        )
        if result.data and "capture" in result.data:
            result = replace(
                result,
                data={
                    "source": result.data["source"],
                    "records": result.data["records"],
                    "captures": [result.data["capture"]],
                    "bundles": [result.data],
                },
            )
    elif "content_base64" in ctx.payload:
        content = base64.b64decode(ctx.payload["content_base64"], validate=True)
        result = engine.normalize(
            source,
            content,
            input_format=source.input_format or "meeting-export",
            bounds=bounds,
            context=context,
        )
        if result.data and "capture" in result.data:
            result = replace(
                result,
                data={
                    "source": result.data["source"],
                    "records": result.data["records"],
                    "captures": [result.data["capture"]],
                    "bundles": [result.data],
                },
            )
    else:
        result = engine.discover(source, bounds=bounds, context=context)
    checkpoint = {
        **result.public_dict(),
        "data": {"source_id": local.id, "meetings": [], "bundles": []},
    }
    if result.status in {"failed", "unsupported"}:
        record_source_failure(ctx.conn, local.id, "Shared discovery did not complete.")
        ctx.save_checkpoint(checkpoint)
        raise ValueError("Shared discovery failed; see the job checkpoint errors and coverage.")
    failures = []
    for incoming in (result.data or {}).get("bundles", []):
        if operation_cancelled(store, context):
            checkpoint["status"] = "cancelled"
            break
        bundle = deepcopy(incoming)
        with atomic(ctx.conn):
            bundle["capture"] = _save_capture(
                store, bundle["capture"], context.scope, bounds.transfer_bytes
            )
            imported = engine.execute(
                "import_bundle",
                {"bundle": bundle, "persist": True},
                context=replace(context, operation_id=None),
            )
            if imported.status not in {"completed", "partial"}:
                raise ValueError("The shared bundle could not be persisted.")
            checkpoint["data"]["bundles"].append(imported.data["bundle_key"])
        for record in bundle["records"]:
            if operation_cancelled(store, context):
                checkpoint["status"] = "cancelled"
                break
            key = source_identity_key(record["identity"])
            try:
                with atomic(ctx.conn):
                    meeting_id = _meeting(ctx.conn, local, record, context.scope)
                    ctx.conn.execute(
                        "INSERT INTO connector_links(scope,identity_key,source_id,meeting_id) "
                        "VALUES (?,?,?,?) ON CONFLICT(scope,identity_key) DO UPDATE "
                        "SET meeting_id=excluded.meeting_id",
                        (context.scope, key, local.id, meeting_id),
                    )
                    if meeting_id is not None:
                        _queue_documents(ctx, local, meeting_id, record)
                        _media(ctx, local, meeting_id, record)
                        _supplied_text(ctx, local, meeting_id, record, context.scope)
                        checkpoint["data"]["meetings"].append(meeting_id)
                        portal.enqueue_once(ctx, portal.ALIGN_MEETING, {"meeting_id": meeting_id})
                    ctx.save_checkpoint(checkpoint)
            except ValueError as exc:
                failures.append({"record_key": key, "reason": str(exc)})
        ctx.heartbeat()
    if failures:
        checkpoint["status"] = "partial"
        checkpoint["errors"] += [
            {
                "code": "destination_conflict",
                "message": item["reason"],
                "details": item,
                "retryable": False,
                "source": source.url,
            }
            for item in failures
        ]
    if failures or result.errors or checkpoint["status"] in {"partial", "cancelled"}:
        record_source_failure(ctx.conn, local.id, "Shared sync has incomplete coverage.")
    else:
        record_source_success(ctx.conn, local.id)
    ctx.save_checkpoint(checkpoint)
    update_operation(store, context.operation_id, checkpoint, scope=context.scope)


def _queue_documents(ctx, local, meeting_id, record):
    for index, _document in enumerate(record["normalized"].get("documents") or []):
        portal.enqueue_once(
            ctx,
            DOWNLOAD_CONNECTOR,
            {
                "source_id": local.id,
                "meeting_id": meeting_id,
                "record_key": source_identity_key(record["identity"]),
                "index": index,
                "revision": canonical_hash(
                    {
                        "documents": record["normalized"].get("documents"),
                        "source_revision": record.get("source_revision"),
                        "capture_sha256": record.get("capture_sha256"),
                    }
                ),
            },
        )


def _media(ctx, local, meeting_id, record):
    from ..adapters.base import Meeting
    from .sync import _sync_video, _Tally

    for media in record["normalized"].get("recordings") or []:
        url = media.get("url")
        if url and portal.video_id(url):
            # Preserve the native channel adoption/caption job behavior.
            listed = Meeting(
                0,
                record["normalized"].get("title") or "",
                datetime.fromisoformat(get_meeting(ctx.conn, meeting_id).starts_at),
                url,
            )
            _sync_video(ctx, local, meeting_id, listed, _Tally())


def _supplied_text(ctx, local, meeting_id, record, scope):
    segments = record.get("text") or []
    if not segments:
        return
    payload = json.dumps(segments, ensure_ascii=False).encode()
    key = source_identity_key(record["identity"]) + ":supplied-text:" + canonical_hash(segments)
    if ctx.conn.execute(
        "SELECT 1 FROM connector_links WHERE scope=? AND identity_key=?", (scope, key)
    ).fetchone():
        return
    artifact = artifacts.store(
        ctx.conn,
        portal.settings().storage_root,
        "record",
        payload,
        "json",
        meta={
            "source_id": local.id,
            "meeting_id": meeting_id,
            "citation_url": record["source_url"],
            "text_provenance": "source_supplied",
        },
    )
    record_id = insert_record(
        ctx.conn,
        meeting_id=meeting_id,
        kind="report",
        artifact_id=artifact.id,
        source_id=local.id,
        title="Source-supplied text evidence",
    )
    for index, segment in enumerate(segments, start=1):
        text = segment.get("text") or ""
        insert_record_page(ctx.conn, record_id=record_id, page_number=index, text=text)
        insert_record_citation(
            ctx.conn,
            record_id=record_id,
            source_id=local.id,
            artifact_id=artifact.id,
            excerpt=text,
            page_number=index,
        )
    ctx.conn.execute(
        "INSERT INTO connector_links(scope,identity_key,source_id,meeting_id,record_id) "
        "VALUES (?,?,?,?,?)",
        (scope, key, local.id, meeting_id, record_id),
    )


def download_connector(ctx: JobContext) -> None:
    payload = ctx.payload
    local, source = connectors.configured_source(ctx.conn, int(payload["source_id"]))
    store, engine = AppStorage(ctx.conn), connectors.engine_for(ctx.conn)
    context = _context(ctx, local)
    if operation_cancelled(store, context):
        ctx.save_checkpoint({"status": "cancelled"})
        return
    item = store.get("records", payload["record_key"], scope=context.scope)
    if item is None:
        raise ValueError("The capture-listed record is unavailable.")
    link = ctx.conn.execute(
        "SELECT meeting_id FROM connector_links WHERE scope=? AND identity_key=?",
        (context.scope, payload["record_key"]),
    ).fetchone()
    if link is None or link["meeting_id"] != payload["meeting_id"]:
        raise ValueError("The attachment destination is not linked to this captured record.")
    attachment_key = payload["record_key"] + ":document:" + str(payload["index"])
    known = ctx.conn.execute(
        "SELECT record_id FROM connector_links WHERE scope=? AND identity_key=?",
        (context.scope, attachment_key),
    ).fetchone()
    revision = store.get("attachment_versions", attachment_key, scope=context.scope)
    if known and revision and revision.value.get("revision") == payload["revision"]:
        ctx.save_checkpoint({"record_id": known["record_id"], "already_stored": True})
        return
    bounds = connectors.bounds_for(local)
    result = engine.fetch_attachment(
        source, item.value, index=payload["index"], bounds=bounds, context=context
    )
    attachment = (result.data or {}).get("attachment")
    if result.status == "cancelled" or operation_cancelled(store, context):
        ctx.save_checkpoint({"status": "cancelled"})
        return
    if attachment is None:
        ctx.save_checkpoint({**result.public_dict(), "data": {"source_id": local.id}})
        raise ValueError("A listed attachment is unavailable; see job coverage. It may be retried.")
    content = (
        base64.b64decode(attachment["content_base64"], validate=True)
        if "content_base64" in attachment
        else attachment.get("text", "").encode()
    )
    if len(content) > bounds.transfer_bytes:
        raise ValueError("The attachment exceeds the transfer byte limit.")
    content_type = attachment.get("content_type")
    document = item.value["normalized"]["documents"][payload["index"]]
    kind = portal.record_kind(document.get("kind") or document.get("title") or "") or "report"
    with atomic(ctx.conn):
        artifact = artifacts.store(
            ctx.conn,
            portal.settings().storage_root,
            "record",
            content,
            portal.content_extension(content_type),
            meta={
                "source_id": local.id,
                "meeting_id": payload["meeting_id"],
                "citation_url": attachment["citation_url"],
                "content_type": content_type,
                "connector_identity": item.value["identity"],
            },
        )
        record_id = (
            known["record_id"]
            if known
            else insert_record(
                ctx.conn,
                meeting_id=payload["meeting_id"],
                source_id=local.id,
                kind=kind,
                artifact_id=artifact.id,
                title=document.get("title") or kind,
            )
        )
        if known:
            # Existing citations retain their original artifact IDs and bytes.
            ctx.conn.execute(
                "UPDATE records SET artifact_id=? WHERE id=?", (artifact.id, record_id)
            )
        else:
            ctx.conn.execute(
                "INSERT INTO connector_links(scope,identity_key,source_id,meeting_id,record_id) "
                "VALUES (?,?,?,?,?)",
                (context.scope, attachment_key, local.id, payload["meeting_id"], record_id),
            )
        store.put(
            "attachment_versions",
            attachment_key,
            {
                "revision": payload["revision"],
                "artifact_id": artifact.id,
                "citation_url": attachment["citation_url"],
            },
            scope=context.scope,
            expected_version=revision.version if revision else None,
            create_only=revision is None,
        )
        insert_record_citation(
            ctx.conn,
            record_id=record_id,
            source_id=local.id,
            artifact_id=artifact.id,
            excerpt=document.get("title") or kind,
        )
        if portal.content_extension(content_type) == "pdf":
            if known:
                ctx.conn.execute("DELETE FROM record_pages WHERE record_id=?", (record_id,))
            portal.enqueue_once(ctx, portal.EXTRACT_PAGES, {"record_id": record_id})
        elif content_type and content_type.split(";")[0] == "text/plain":
            if known:
                ctx.conn.execute("DELETE FROM record_pages WHERE record_id=?", (record_id,))
            insert_record_page(
                ctx.conn, record_id=record_id, page_number=1, text=content.decode("utf-8")
            )
        ctx.save_checkpoint(
            {
                "status": result.status,
                "record_id": record_id,
                "artifact_id": artifact.id,
                "sha256": artifact.sha256,
                "size": artifact.size,
                "already_stored": False,
            }
        )


def connector_operation(ctx: JobContext) -> None:
    """Run bounded SDK write operations in a worker, never in an API request."""
    payload = ctx.payload
    token = ctx.conn.execute(
        "SELECT scope FROM api_tokens WHERE id=?", (payload["actor_token_id"],)
    ).fetchone()
    if token is None or token["scope"] != "read_write":
        raise ValueError("The requesting write token is no longer authorized.")
    local, source = connectors.configured_source(ctx.conn, payload["source_id"])
    context = replace(
        connectors.write_context(ctx.conn, local.id, payload["actor_token_id"]),
        operation_id=operation_id(ctx.job_id),
    )
    if ctx.stop_event is not None:
        context = replace(context, cancellation=ctx.stop_event)
    store, engine = AppStorage(ctx.conn), connectors.engine_for(ctx.conn)
    if operation_cancelled(store, context):
        ctx.save_checkpoint(OperationResult("cancelled").public_dict())
        return
    operation = payload["operation"]
    if operation not in {"plan_write", "commit_write"}:
        raise ValueError("This job only supports the exact write plan/commit protocol.")
    request = payload["request"]
    if operation == "plan_write":
        request = {
            **request,
            "source": source.public_dict(),
            "bounds": request.get("bounds", {}),
        }
    result = engine.execute(operation, request, context=context)
    ctx.save_checkpoint(result.public_dict())
    if result.status in {"failed", "unsupported"}:
        raise ValueError("The write operation was refused; see the checkpoint errors.")
