"""The download job: fetch one compiled document and store it (spec 9.2, 9.6).

A packet runs 60 to 170 MB (spec 9.6), so this runs on the heavy lane and the
limit that applies to it is the packet limit.

The row this writes points at an artifact and at the portal ids the document
was published under, never at a path and never at the signed storage link the
download passes through (spec 9.2). Nothing here opens the bytes: what a stored
PDF says is read by the ``extract_pages`` job, which this one queues when the
bytes it stored are a PDF (spec 9.6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters.base import Document
from ..artifacts import store
from ..jobs import JobContext
from ..repo import get_meeting, get_source, insert_record, record_for_portal_document
from . import portal


def download_record(ctx: JobContext) -> None:
    """Fetch the document one download job names and store it.

    A document that is already stored is not fetched again: the portal ids are
    the key, so a re-run of the same job writes the same one row. A document
    over the limit raises ``DocumentTooLarge``, which fails this job with the
    limit and the size in its reason and does not touch the source's health
    (spec 9.6): one oversized packet is not a broken portal.
    """
    job = portal.document_job(ctx.payload)
    source = get_source(ctx.conn, job.source_id)
    if source is None:
        ctx.pause(
            f"There is no source {job.source_id} to download document {job.document_id} from."
        )
        return
    if source.type != portal.MEETING_PORTAL:
        ctx.pause(
            f"Source {source.id} is a {source.type} source, which publishes no meeting documents."
        )
        return

    meeting = get_meeting(ctx.conn, job.meeting_id)
    if meeting is None:
        ctx.pause(f"There is no meeting {job.meeting_id} for document {job.document_id}.")
        return
    if meeting.portal_meeting_id is None:
        ctx.pause(
            f"Meeting {meeting.id} carries no portal meeting id, so document "
            f"{job.document_id} cannot be cited back to the portal that listed it."
        )
        return

    stored = record_for_portal_document(ctx.conn, meeting.id, job.document_id)
    if stored is not None:
        ctx.save_checkpoint(
            {
                "record_id": stored.id,
                "artifact_id": stored.artifact_id,
                "kind": stored.kind,
                "already_stored": True,
            }
        )
        return

    kind = portal.record_kind(job.template_name)
    if kind is None:
        ctx.pause(
            f"Document {job.document_id} is a {job.template_name!r} template, which is not one "
            "of the record kinds of the schema."
        )
        return

    document = Document(
        id=job.document_id,
        template_id=job.template_id,
        compile_output_type=job.compile_output_type,
        template_name=job.template_name,
        meeting_id=meeting.portal_meeting_id,
    )
    content = portal.adapter_for(source).get_document(document)
    artifact = store(
        ctx.conn,
        portal.settings().storage_root,
        "record",
        content.content,
        portal.content_extension(content.content_type),
        meta=_citation(source, meeting, job, content.citation_url, content.content_type),
    )
    record_id = insert_record(
        ctx.conn,
        meeting_id=meeting.id,
        kind=kind,
        artifact_id=artifact.id,
        source_id=source.id,
        title=job.template_name or None,
        portal_document_id=job.document_id,
        portal_template_id=job.template_id,
    )
    read = _ask_for_reading(ctx, record_id, artifact.rel_path)
    ctx.save_checkpoint(
        {
            "record_id": record_id,
            "artifact_id": artifact.id,
            "kind": kind,
            "size": content.size,
            "sha256": content.sha256,
            "content_type": content.content_type,
            "already_stored": False,
            "pages_queued": read is not None,
        }
    )


def _ask_for_reading(ctx: JobContext, record_id: int, rel_path: str) -> int | None:
    """Queue the reading of a stored PDF, and of nothing else.

    Only a PDF has pages to read, so only a PDF is queued: a document of any
    other kind would be a job that could only pause. The queueing is asked for
    once, so a second download of the same bytes adds no second reading.
    """
    if Path(rel_path).suffix.casefold().lstrip(".") != portal.PDF_EXTENSION:
        return None
    return portal.enqueue_once(ctx, portal.EXTRACT_PAGES, portal.PageJob(record_id).as_payload())


def _citation(
    source: Any,
    meeting: Any,
    job: portal.DocumentJob,
    citation_url: str,
    content_type: str | None,
) -> dict[str, Any]:
    """What a reader needs to cite this file back to where it came from.

    The portal's own ids and the portal's own URL. The signed storage link the
    download redirected through expires in about two days and would be a dead
    citation, so it is nowhere here (spec 9.2).
    """
    return {
        "source_id": source.id,
        "origin": source.origin,
        "meeting_id": meeting.id,
        "portal_meeting_id": meeting.portal_meeting_id,
        "portal_document_id": job.document_id,
        "portal_template_id": job.template_id,
        "template_name": job.template_name,
        "citation_url": citation_url,
        "content_type": content_type,
    }
