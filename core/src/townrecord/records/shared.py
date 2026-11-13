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


class BodyOutsideCoverage(ValueError):
    """The source names a body the operator has not selected for this instance."""


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
    body = portal.resolve_body(conn, local, normalized.get("body") or normalized.get("title") or "")
    if body is None and not normalized.get("body") and local.body_id is not None:
        body = get_body(conn, local.body_id)
    if body is None:
        named = normalized.get("body") or normalized.get("title")
        if named:
            raise BodyOutsideCoverage(str(named))
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
    primegov_id = None
    if record["identity"]["provider"] == "primegov":
        primegov_id = int(record["identity"]["external_id"])
        native = conn.execute(
            "SELECT id FROM meetings WHERE portal_source_id=? AND portal_meeting_id=?",
            (local.id, primegov_id),
        ).fetchone()
        if meeting_id is None and native:
            meeting_id = native["id"]
    if meeting_id is None or get_meeting(conn, meeting_id) is None:
        meeting_id = insert_meeting(
            conn,
            body_id=body.id,
            starts_at=start,
            title=normalized.get("title"),
        )
    if primegov_id is not None:
        template = next(
            (
                d.get("source_metadata", {}).get("template_id")
                for d in normalized.get("documents", [])
                if d.get("source_metadata", {}).get("compile_output_type") == 3
            ),
            None,
        )
        conn.execute(
            "UPDATE meetings SET portal_source_id=?,portal_meeting_id=?,"
            "portal_html_template_id=COALESCE(?,portal_html_template_id) WHERE id=?",
            (local.id, primegov_id, template, meeting_id),
        )
    status = normalized.get("source_status")
    cancelled = (
        str(status).casefold() in {"cancelled", "canceled"}
        or isinstance(status, dict)
        and bool(status.get("canceled") or status.get("cancelled"))
        or portal.cancellation_signal(normalized.get("title") or "", ()) is not None
        or any(d.get("kind") in {"cancel", "cancellation"} for d in normalized.get("documents", []))
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
            except BodyOutsideCoverage as exc:
                checkpoint["coverage"].setdefault("outside_configured_bodies", []).append(
                    {"record_key": key, "source_title": str(exc)}
                )
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
        identity_key=key,
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
    document = item.value["normalized"]["documents"][payload["index"]]
    # A document URL is stable when the provider reorders its attachment list.
    document_identity = document.get("external_id") or document.get("url")
    if not document_identity:
        raise ValueError("The attachment has no stable provider identity or URL.")
    attachment_key = payload["record_key"] + ":document:" + canonical_hash(document_identity)
    bounds = connectors.bounds_for(local)
    if (
        item.value["identity"]["provider"] == "primegov"
        and document.get("source_metadata", {}).get("compile_output_type") == 3
    ):
        # Reuse the existing PrimeGov HTML endpoint; the shared saved-format
        # mapping uses the PDF compile endpoint for this HTML-only template.
        # Keep QuorumRelay's public-network and exact-origin guards in force.
        from urllib.parse import urlsplit

        from civic_data_connectors.providers.contracts import PublicTransport

        template_id = int(document["source_metadata"]["template_id"])
        url = portal.adapter_for(local).html_agenda_url(template_id)
        parsed = urlsplit(local.origin)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        response = PublicTransport().request(
            url,
            byte_limit=bounds.transfer_bytes,
            deadline_seconds=bounds.deadline_seconds,
            allowed_origins=(origin,),
            allow_redirects=False,
        )
        if response.status != 200:
            raise ValueError(
                "The provider HTML agenda is unavailable; retry after checking the source."
            )
        result = OperationResult(
            "completed",
            {
                "attachment": {
                    "content_base64": base64.b64encode(response.content).decode("ascii"),
                    "content_type": response.content_type,
                    "citation_url": document.get("citation_url") or url,
                }
            },
        )
    else:
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
    if item.value["identity"]["provider"] == "primegov" and (
        b"<title>document not found</title>" in content.lower()
        or b"the requested meeting document is no longer available" in content.lower()
    ):
        raise ValueError(
            "The provider returned a document-unavailable page, not a meeting document."
        )
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
        # Upgrade old index-based attachment links only when the saved source
        # citation proves which URL they represented; reordered lists are safe.
        old_links = ctx.conn.execute(
            "SELECT l.identity_key,a.meta FROM connector_links l "
            "JOIN records r ON r.id=l.record_id JOIN artifacts a ON a.id=r.artifact_id "
            "WHERE l.scope=? AND l.source_id=?",
            (context.scope, local.id),
        ).fetchall()
        old_prefix = payload["record_key"] + ":document:"
        for old in old_links:
            old_key = old["identity_key"]
            if (
                old_key.startswith(old_prefix)
                and old_key[len(old_prefix) :].isdigit()
                and json.loads(old["meta"]).get("citation_url")
                in {document.get("url"), document.get("citation_url")}
                and not ctx.conn.execute(
                    "SELECT 1 FROM connector_links WHERE scope=? AND identity_key=?",
                    (context.scope, attachment_key),
                ).fetchone()
            ):
                ctx.conn.execute(
                    "UPDATE connector_links SET identity_key=? WHERE scope=? AND identity_key=?",
                    (attachment_key, context.scope, old_key),
                )
                ctx.conn.execute(
                    "UPDATE records SET identity_key=? WHERE id=(SELECT record_id "
                    "FROM connector_links WHERE scope=? AND identity_key=?)",
                    (attachment_key, context.scope, attachment_key),
                )
        known = ctx.conn.execute(
            "SELECT r.id,r.artifact_id,r.meeting_id FROM connector_links l "
            "JOIN records r ON r.id=l.record_id "
            "WHERE l.scope=? AND l.identity_key=?",
            (context.scope, attachment_key),
        ).fetchone()
        native_document = None
        if item.value["identity"]["provider"] == "primegov":
            native_document = int(document["external_id"])
            if known is None:
                known = ctx.conn.execute(
                    "SELECT id,artifact_id,meeting_id FROM records WHERE source_id=? "
                    "AND portal_document_id=? ORDER BY id DESC LIMIT 1",
                    (local.id, native_document),
                ).fetchone()
        revision = store.get("attachment_versions", attachment_key, scope=context.scope)
        if known and known["artifact_id"] == artifact.id:
            ctx.conn.execute(
                "INSERT INTO connector_links(scope,identity_key,source_id,meeting_id,record_id) "
                "VALUES(?,?,?,?,?) ON CONFLICT(scope,identity_key) DO NOTHING",
                (context.scope, attachment_key, local.id, known["meeting_id"], known["id"]),
            )
            ctx.save_checkpoint({"record_id": known["id"], "already_stored": True})
            return
        record_id = insert_record(
            ctx.conn,
            meeting_id=known["meeting_id"] if known else payload["meeting_id"],
            source_id=local.id,
            kind=kind,
            artifact_id=artifact.id,
            title=document.get("title") or kind,
            identity_key=attachment_key,
            portal_document_id=native_document,
            portal_template_id=document.get("source_metadata", {}).get("template_id")
            if native_document is not None
            else None,
        )
        if known:
            ctx.conn.execute(
                "INSERT INTO record_revisions(previous_record_id,current_record_id) VALUES(?,?)",
                (known["id"], record_id),
            )
        ctx.conn.execute(
            "INSERT INTO connector_links(scope,identity_key,source_id,meeting_id,record_id) "
            "VALUES (?,?,?,?,?) ON CONFLICT(scope,identity_key) DO UPDATE "
            "SET record_id=excluded.record_id,meeting_id=excluded.meeting_id",
            (
                context.scope,
                attachment_key,
                local.id,
                known["meeting_id"] if known else payload["meeting_id"],
                record_id,
            ),
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
        if (
            item.value["identity"]["provider"] == "primegov"
            and portal.content_extension(content_type) == "html"
        ):
            destination = known["meeting_id"] if known else payload["meeting_id"]
            for agenda in portal.adapter_for(local).parse_html_agenda(content, None):
                upsert_agenda_item(
                    ctx.conn,
                    meeting_id=destination,
                    number=agenda.number,
                    title=agenda.title,
                    identifiers=portal.identifiers_of(agenda.title),
                )
            portal.enqueue_once(ctx, portal.ALIGN_MEETING, {"meeting_id": destination})
        if portal.content_extension(content_type) == "pdf":
            portal.enqueue_once(ctx, portal.EXTRACT_PAGES, {"record_id": record_id})
        elif content_type and content_type.split(";")[0] == "text/plain":
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
