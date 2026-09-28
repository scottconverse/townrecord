"""The PrimeGov sync job: list a window and store what the portal published.

Spec 7.1 step 5 asks for the last 30 days on a first run, section 7.2 step 3
asks for the meetings and their documents, section 9.2 says which portal id is
kept, and section 9.5 says how a cancelled meeting is marked.

The job does three things with one window:

* each listed meeting becomes a row, with the portal's own meeting id kept in
  a column (``meetings.portal_meeting_id``), and a cancellation marked from
  the notice or the title;
* each compiled document becomes one ``download_record`` job on the heavy lane,
  and each HTML agenda is read here and now, because it is a small page that
  carries the clerk's agenda items (spec 9.2);
* each meeting that has agenda items and a primary video gets an
  ``align_meeting`` job.

The listing is the source's health check (spec 7.3). One document that cannot
be read is recorded with its reason in the job's checkpoint and does not stop
the window: a single broken agenda page must not be able to block a whole
source forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import AdapterError, Document
from ..adapters.base import Meeting as ListedMeeting
from ..jobs import JobContext
from ..repo import Source, record_source_failure, record_source_success
from . import portal


@dataclass
class _Tally:
    """What one window produced, for the checkpoint and the report."""

    listed: int = 0
    meetings_created: int = 0
    meetings_updated: int = 0
    cancelled: int = 0
    continued: int = 0
    videos_created: int = 0
    videos_linked: int = 0
    documents_queued: int = 0
    documents_pending: int = 0
    html_agendas_read: int = 0
    agenda_items_written: int = 0
    alignments_queued: int = 0
    skipped_total: int = 0
    skipped: list[str] = field(default_factory=list)

    def skip(self, sentence: str) -> None:
        """Record one thing the sync did not do, with its reason.

        Every skip is counted; only the first :data:`portal.MAX_REPORTED_SKIPS`
        are kept by name, because a window of a whole year can skip hundreds of
        meetings and a checkpoint that grows without bound is not a summary.
        """
        self.skipped_total += 1
        if len(self.skipped) < portal.MAX_REPORTED_SKIPS:
            self.skipped.append(sentence)

    def as_checkpoint(self, request: portal.SyncRequest, source: Source) -> dict[str, Any]:
        """The summary a reader finds on the job when it is done."""
        summary: dict[str, Any] = {
            "source_id": source.id,
            "origin": source.origin,
            "from_date": request.date_from.isoformat(),
            "to_date": request.date_to.isoformat(),
            "meetings_listed": self.listed,
            "meetings_created": self.meetings_created,
            "meetings_updated": self.meetings_updated,
            "meetings_cancelled": self.cancelled,
            "meetings_continued": self.continued,
            "videos_created": self.videos_created,
            "videos_linked": self.videos_linked,
            "documents_queued": self.documents_queued,
            "documents_pending": self.documents_pending,
            "html_agendas_read": self.html_agendas_read,
            "agenda_items_written": self.agenda_items_written,
            "alignments_queued": self.alignments_queued,
            "skipped_total": self.skipped_total,
            "skipped": list(self.skipped),
        }
        if self.skipped_total > portal.MAX_REPORTED_SKIPS:
            summary["skipped_note"] = (
                f"{self.skipped_total} things were skipped; the first "
                f"{portal.MAX_REPORTED_SKIPS} are listed."
            )
        return summary


def sync_primegov(ctx: JobContext) -> None:
    """Sync one configured meeting portal over one window (spec 7.2 step 3).

    Raises whatever the portal raised when the listing fails, so the job is
    FAILED with the real reason, and records that failure against the source
    first: three in a row make the source broken (spec 7.3).
    """
    request = portal.sync_request(ctx.payload, today=ctx.clock().date())
    try:
        source = portal.syncable_source(ctx.conn, request.source_id)
    except portal.SyncRefused as exc:
        ctx.pause(str(exc))
        return

    adapter = portal.adapter_for(source)
    try:
        listed = adapter.list_meetings(request.date_from, request.date_to)
    except AdapterError as exc:
        reason = portal.adapter_problem(exc)
        failures = record_source_failure(ctx.conn, source.id, reason)
        raise portal.SyncRefused(portal.failure_sentence(source, reason, failures)) from exc
    record_source_success(ctx.conn, source.id)

    tally = _Tally()
    for meeting in listed:
        ctx.heartbeat()
        _sync_meeting(ctx, source, meeting, tally)
    ctx.save_checkpoint(tally.as_checkpoint(request, source))


def _sync_meeting(ctx: JobContext, source: Source, listed: ListedMeeting, tally: _Tally) -> None:
    """Store one listed meeting, its video and its documents."""
    tally.listed += 1
    body = portal.resolve_body(ctx.conn, source, listed.title)
    if body is None:
        tally.skip(
            f"Meeting {listed.id} ({listed.title!r}) names no body of jurisdiction "
            f"{source.jurisdiction_id}: the sync looked for "
            f"{portal.body_name(listed.title)!r}."
        )
        return

    signal = portal.cancellation_signal(listed.title, listed.documents)
    if signal is not None:
        tally.cancelled += 1
    continued = portal.continuation_signal(listed.title)
    if continued is not None:
        tally.continued += 1
    meeting_id, created = _upsert(
        ctx, source, listed, body_id=body.id, signal=signal, is_continued=continued is not None
    )
    tally.meetings_created += 1 if created else 0
    tally.meetings_updated += 0 if created else 1

    if listed.video_url:
        _sync_video(ctx, source, meeting_id, listed, tally)
    for document in listed.documents:
        _sync_document(ctx, source, meeting_id, listed, document, tally)
    _queue_alignment(ctx, meeting_id, source, tally)


def _upsert(
    ctx: JobContext,
    source: Source,
    listed: ListedMeeting,
    *,
    body_id: int,
    signal: str | None,
    is_continued: bool = False,
) -> tuple[int, bool]:
    """Store the meeting or update the row a past sync made."""
    from ..repo import upsert_meeting

    return upsert_meeting(
        ctx.conn,
        body_id=body_id,
        starts_at=listed.start.isoformat(),
        title=listed.title or None,
        type=portal.meeting_type(listed.title),
        is_cancelled=signal is not None,
        is_continued=is_continued,
        portal_source_id=source.id,
        portal_meeting_id=listed.id,
        portal_html_template_id=portal.html_template_id(listed.documents),
    )


def _sync_video(
    ctx: JobContext, source: Source, meeting_id: int, listed: ListedMeeting, tally: _Tally
) -> None:
    """Record the meeting's video, adopting the row a channel already made.

    The listing gives a videoUrl and nothing else, so the platform id is read
    out of the URL. A row that already holds that platform id is the video (a
    watched channel's row, when the channel captured it), and the sync adds
    only what the row was missing: the meeting, the primary flag and the URL.
    The title and the source of that row are never rewritten (spec 7.2 step 7),
    which is why no title is invented here for a new row either.
    """
    from ..repo import all_sources, attach_video, insert_video, primary_video, videos_of_platform

    platform_id = portal.video_id(listed.video_url)
    if platform_id is None:
        tally.skip(
            f"Meeting {listed.id} lists a video at {listed.video_url!r}, which is not a link "
            "this sync can read an id out of, so no video is recorded for it."
        )
        return

    known = videos_of_platform(ctx.conn, platform_id)
    if known:
        chosen = _channel_row(all_sources(ctx.conn), known) or known[0]
        primary = primary_video(ctx.conn, meeting_id)
        is_primary = primary is None or primary.id == chosen.id
        if not is_primary:
            tally.skip(
                f"Video {chosen.id} was not made the primary video of meeting {meeting_id}: "
                f"video {primary.id} is already primary there."
            )
        attach_video(
            ctx.conn,
            chosen.id,
            meeting_id=meeting_id,
            is_primary=is_primary,
            url=listed.video_url,
        )
        tally.videos_linked += 1
        return

    insert_video(
        ctx.conn,
        source_id=source.id,
        platform_video_id=platform_id,
        meeting_id=meeting_id,
        url=listed.video_url,
        published_at=listed.start.isoformat(),
        is_primary=primary_video(ctx.conn, meeting_id) is None,
        source_note=(
            f"Listed by the meeting portal source {source.id} for meeting {listed.id}. "
            "No watched channel owns this video yet."
        ),
    )
    tally.videos_created += 1


def _channel_row(sources: list[Source], rows: list[Any]) -> Any | None:
    """The row of a watched channel, when one of these rows is a channel's.

    The only place this schema ties a video to a channel is the source of the
    row the channel captured, so that row is what a watched channel looks like
    here. When two sources listed one platform video, the channel's row wins.
    """
    channels = {source.id for source in sources if source.type == portal.VIDEO_CHANNEL}
    for video in rows:
        if video.source_id in channels:
            return video
    return None


def _sync_document(
    ctx: JobContext,
    source: Source,
    meeting_id: int,
    listed: ListedMeeting,
    document: Document,
    tally: _Tally,
) -> None:
    """Read an HTML agenda now, or queue a compiled document for the heavy lane."""
    if document.compile_output_type == 3:
        _read_html_agenda(ctx, source, meeting_id, listed, document, tally)
        return
    if document.compile_output_type != 1:
        tally.skip(
            f"Document {document.id} ({document.template_name!r}) is compileOutputType "
            f"{document.compile_output_type}, so the sync does not fetch it."
        )
        return
    if portal.record_kind(document.template_name) is None:
        tally.skip(
            f"Document {document.id} ({document.template_name!r}) has no record kind in the "
            "schema, so it is not downloaded. A cancellation notice is read from the listing."
        )
        return
    job = portal.DocumentJob(
        source_id=source.id,
        meeting_id=meeting_id,
        document_id=document.id,
        template_id=document.template_id,
        compile_output_type=document.compile_output_type,
        template_name=document.template_name,
    )
    if portal.enqueue_once(ctx, portal.DOWNLOAD_RECORD, job.as_payload()) is None:
        tally.documents_pending += 1
    else:
        tally.documents_queued += 1


def _read_html_agenda(
    ctx: JobContext,
    source: Source,
    meeting_id: int,
    listed: ListedMeeting,
    document: Document,
    tally: _Tally,
) -> None:
    """Fetch an HTML agenda and store its items, without any times.

    The item times of spec 10.2 are not stored here: alignment is a separate
    job, and the video's length is not known until it has been captured, so the
    status of a video time cannot be worked out at this moment. What is stored
    is the item, its number, its title and the identifiers of spec 10.1.
    """
    from ..repo import upsert_agenda_item

    adapter = portal.adapter_for(source)
    agenda_document = Document(
        id=document.id,
        template_id=document.template_id,
        compile_output_type=3,
        template_name=document.template_name,
        meeting_id=listed.id,
    )
    try:
        content = adapter.get_html_agenda(agenda_document)
    except AdapterError as exc:
        tally.skip(
            f"The HTML agenda of meeting {listed.id} (document {document.id}) could not be "
            f"read: {portal.adapter_problem(exc)}"
        )
        return
    items = adapter.parse_html_agenda(content.content, None)
    written = 0
    for item in items:
        _, created = upsert_agenda_item(
            ctx.conn,
            meeting_id=meeting_id,
            number=item.number,
            title=item.title,
            identifiers=portal.identifiers_of(item.title),
        )
        written += 1 if created else 0
    tally.html_agendas_read += 1
    tally.agenda_items_written += written


def _queue_alignment(ctx: JobContext, meeting_id: int, source: Source, tally: _Tally) -> None:
    """Queue the alignment of a meeting that has items and a video.

    The job is queued whether or not the video has a transcript yet. The
    transcript arrives from its own work, and the align job says plainly when it
    is not there yet. This sync is one of the two things that make the job run
    after it paused, so the ask goes through the same function the transcript
    writer calls: a paused job is put back on the queue rather than left beside
    a second job that would align the same meeting twice.
    """
    from ..repo import agenda_items, primary_video
    from .requests import SYNCED_AGAIN, request_alignment

    if not agenda_items(ctx.conn, meeting_id):
        return
    if primary_video(ctx.conn, meeting_id) is None:
        return
    reason = SYNCED_AGAIN.format(meeting_id=meeting_id)
    if request_alignment(ctx.conn, meeting_id, reason=reason, origin=ctx.origin) is not None:
        tally.alignments_queued += 1
