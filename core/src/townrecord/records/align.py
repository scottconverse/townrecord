"""The align job: give one meeting's agenda items a time in its video (spec 10.2).

The agenda items and their clerk published times come from the HTML agenda, the
spoken transitions come from the video's transcript, and
:func:`townrecord.align.align_agenda_with_transcript` decides what each item
gets and which of the three methods accounted for it.

The agenda is read again here rather than kept from the sync, because the video
time of an item has no column: what is stored on an item is the boundary the
aligner measured, and reading it back means reading the same page again. The
offset and its anchors go in one row per meeting (migration 0009), so a reader
can see what the boundaries rest on.

A meeting the portal published no HTML agenda for is aligned against its agenda
PDF instead: the items are read from the pages the page reader stored. They
carry no clerk times, so only the spoken record can place them. Reading those
pages is a third thing that puts a paused job back on the queue.

The job is safe to re-run: the same agenda and the same transcript write the
same rows, and the alignment row is updated in place rather than added to. A run
it pauses is not lost: it comes back to the queue when the transcript is stored
or the meeting is synced again (spec 16.3).
"""

from __future__ import annotations

from typing import Any

from ..adapters.base import AdapterError, Document, Meeting
from ..align.align import AlignmentResult, align_agenda_with_transcript
from ..jobs import JobContext
from ..repo import (
    Source,
    get_meeting,
    get_source,
    latest_transcript,
    primary_video,
    record_pages,
    records_of_meeting,
    save_meeting_alignment,
    segments_of,
    update_agenda_item_alignment,
    upsert_agenda_item,
)
from . import portal
from .agenda_pdf import parse_pdf_agenda


def align_meeting(ctx: JobContext) -> None:
    """Align one meeting's agenda items with its video's transcript.

    A gap that another job fills later pauses this one with the reason rather
    than failing it: a meeting whose video has no transcript yet is the ordinary
    case. What makes it run afterwards is named in the reason it leaves, and it
    is two things (spec 7.1 step 7): a transcript of that video is stored, or
    the meeting is listed again by a later sync. Both go through
    :func:`townrecord.records.request_alignment`, which puts this same job back
    on the queue.
    """
    request = portal.align_request(ctx.payload)
    meeting = get_meeting(ctx.conn, request.meeting_id)
    if meeting is None:
        ctx.pause(f"There is no meeting {request.meeting_id} to align.")
        return
    pdf_pages: list[str] | None = None
    source: Source | None = None
    if meeting.portal_html_template_id is None:
        pdf_pages = _agenda_pdf_pages(ctx, meeting.id)
        if pdf_pages is None:
            return
    else:
        source_id = request.source_id if request.source_id is not None else meeting.portal_source_id
        source = get_source(ctx.conn, source_id) if source_id is not None else None
        if source is None or source.type != portal.MEETING_PORTAL:
            ctx.pause(
                f"Meeting {meeting.id} was not listed by a meeting portal source, so its "
                "agenda cannot be read again."
            )
            return

    video = primary_video(ctx.conn, meeting.id)
    if video is None:
        ctx.pause(
            f"Meeting {meeting.id} has no primary video, so its agenda items have nothing "
            "to align to."
        )
        return
    transcript = latest_transcript(ctx.conn, video.id)
    if transcript is None:
        ctx.pause(
            f"Video {video.id} of meeting {meeting.id} has no transcript yet, so its agenda "
            "items cannot be aligned. This job runs again when a transcript of that video is "
            f"stored, or when meeting {meeting.id} is synced again."
        )
        return
    segments = segments_of(ctx.conn, transcript.id)
    if not segments:
        ctx.pause(
            f"Transcript {transcript.id} of video {video.id} has no lines, so there is nothing "
            "to align."
        )
        return

    agenda: list[Any] | None
    if pdf_pages is not None:
        agenda = parse_pdf_agenda(pdf_pages)
        if not agenda:
            ctx.pause(
                f"The agenda PDF of meeting {meeting.id} has no numbered items that could "
                "be read, so there is nothing to align."
            )
            return
    else:
        assert source is not None
        agenda = _read_agenda(ctx, source, meeting, video.duration_s)
        if agenda is None:
            return

    result = align_agenda_with_transcript(
        agenda,
        segments,
        video_duration_ms=None if video.duration_s is None else video.duration_s * 1000,
    )
    _write_items(ctx, meeting.id, result)
    save_meeting_alignment(
        ctx.conn,
        meeting_id=meeting.id,
        agenda_item_count=len(result.items),
        offset_s=result.offset.offset_s,
        offset_accepted=result.offset.accepted,
        offset_reason=result.offset.reason,
        anchors=_anchors(result),
        anchors_agreeing=len(result.offset.agreeing),
        counts=result.counts_by_method(),
        video_id=video.id,
        transcript_id=transcript.id,
        spoken_reason=result.spoken_reason,
        html_reason=result.html_reason,
    )
    ctx.save_checkpoint(
        {
            "meeting_id": meeting.id,
            "video_id": video.id,
            "transcript_id": transcript.id,
            "agenda_items": len(result.items),
            "counts_by_method": result.counts_by_method(),
            "offset_s": result.offset.offset_s,
            "offset_accepted": result.offset.accepted,
            "offset_reason": result.offset.reason,
            "anchors": len(result.offset.anchors),
            "anchors_agreeing": len(result.offset.agreeing),
            "spoken_reason": result.spoken_reason,
            "html_reason": result.html_reason,
            "dropped": list(result.dropped),
        }
    )


def _agenda_pdf_pages(ctx: JobContext, meeting_id: int) -> list[str] | None:
    """The text of each page of the meeting's agenda PDF, or None after a pause.

    A meeting with neither agenda pauses until a sync stores one. An agenda
    whose pages are not read yet pauses until the page reader has read them,
    and the page reader is what puts this job back on the queue.
    """
    agendas = [
        record
        for record in records_of_meeting(ctx.conn, meeting_id)
        if record.kind == portal.AGENDA_KIND
    ]
    if not agendas:
        ctx.pause(
            f"Meeting {meeting_id} has no HTML agenda and no agenda PDF, so it has no agenda "
            "items to align. This job runs again when the meeting is synced again."
        )
        return None
    record = agendas[-1]
    pages = record_pages(ctx.conn, record.id)
    if not pages:
        ctx.pause(
            f"Meeting {meeting_id} has no HTML agenda, and the pages of its agenda PDF "
            f"(record {record.id}) are not read yet. This job runs again when they are."
        )
        return None
    return [page.text for page in pages]


def _read_agenda(
    ctx: JobContext, source: Source, meeting: Meeting, video_duration_s: int | None
) -> list[Any] | None:
    """Read the meeting's HTML agenda again, with the video's length known.

    The video's length is what lets the adapter say whether a published time is
    inside the video (spec 10.2), and it is known here in a way it was not when
    the sync stored the items. A page that cannot be read pauses the job with
    the reason, because a job that cannot read its own input did not fail at
    anything.
    """
    document = Document(
        id=meeting.portal_html_template_id,
        template_id=meeting.portal_html_template_id,
        compile_output_type=3,
        template_name="HTML Agenda",
        meeting_id=meeting.portal_meeting_id,
    )
    adapter = portal.adapter_for(source)
    try:
        content = adapter.get_html_agenda(document)
    except AdapterError as exc:
        ctx.pause(
            f"The HTML agenda of meeting {meeting.id} could not be read: "
            f"{portal.adapter_problem(exc)}"
        )
        return None
    return adapter.parse_html_agenda(content.content, video_duration_s)


def _write_items(ctx: JobContext, meeting_id: int, result: AlignmentResult) -> None:
    """Write each item's boundary onto the item the sync already stored.

    The item is keyed by its number, which is what the agenda publishes and
    what the schema makes unique per meeting. An item the stored agenda does
    not have is stored here, because the agenda that was just read is the one
    being aligned; a title that changed is written and then given the boundary
    measured against it, so the two never disagree.
    """
    for item in result.items:
        agenda_item_id, _ = upsert_agenda_item(
            ctx.conn,
            meeting_id=meeting_id,
            number=item.number,
            title=item.title,
        )
        update_agenda_item_alignment(
            ctx.conn,
            agenda_item_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            alignment_method=item.method,
            alignment_reason=item.reason or "",
        )


def _anchors(result: AlignmentResult) -> list[dict[str, Any]]:
    """The anchors the offset was measured from, as rows a reader can read."""
    return [
        {
            "item_number": anchor.item_number,
            "agenda_seconds": anchor.agenda_seconds,
            "spoken_ms": anchor.spoken_ms,
            "difference_s": anchor.difference_s,
            "matched_text": anchor.matched_text,
            "agreeing": anchor in result.offset.agreeing,
        }
        for anchor in result.offset.anchors
    ]


__all__ = ["align_meeting"]
