"""The read endpoints (spec 12.4, spec 13.2).

Every route here is a read. Each one opens one connection, runs a small number
of indexed statements and answers, so nothing on this surface does long work
inside a request (rule A). Work that takes minutes, such as capturing a video
or transcribing it, belongs to a job, not to a request.

Every route sits under the application's token check (spec 13.1, decision 10
rule 2): this router carries no exemption of its own, and the app in ``app.py``
adds the check to everything it serves. An unknown id is a 404 with one plain
sentence, never a stack trace and never an empty object standing in for a
record that does not exist (rule F).

A gap in the data is reported as a gap. A meeting with no minutes says so in
``notes``. A recording with no transcript answers 404 saying that, rather than
200 with an empty list of lines, because nobody having transcribed a meeting
and a meeting that was transcribed to nothing are two different facts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response

from .. import artifacts
from ..repo import (
    VOTE_SOURCE_KINDS,
    AgendaItem,
    Motion,
    Vote,
    agenda_items,
    citation_sha256,
    find_votes,
    get_body,
    get_citation,
    get_meeting,
    items_for_segments,
    latest_transcript,
    meetings_of_body,
    minutes_expectation,
    motions_of_item,
    primary_video,
    search,
    segments_of,
    votes_of_item,
)
from ..repo import area as area_repo
from .auth import get_connection
from .models import (
    AgendaItemOut,
    AgendaItemWithVotes,
    AreaOut,
    BodyOut,
    CitationVerifyOut,
    DocumentOut,
    JurisdictionNode,
    JurisdictionOut,
    MeetingDetail,
    MeetingSummary,
    MotionOut,
    RecordOut,
    RecordPageOut,
    SearchCitationOut,
    SearchHitOut,
    SearchResults,
    SegmentOut,
    SourceOut,
    TranscriptOut,
    VideoOut,
    VoteOut,
    VotesResponse,
    VoteWithContext,
)

router = APIRouter(prefix="/v1", tags=["read"])

#: The media type of an artifact, by the extension of its stored name. The
#: artifact table has no content type column (migration 0004), so the type is
#: derived: the ``content_type`` of the artifact's meta when the capture
#: recorded one, and otherwise the extension of the name it was stored under.
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "vtt": "text/vtt; charset=utf-8",
    "srv3": "application/xml",
    "xml": "application/xml",
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "jsonl": "application/x-ndjson",
    "zip": "application/zip",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
}

#: What a file is when nothing says otherwise.
DEFAULT_MEDIA_TYPE = "application/octet-stream"

#: The one record kind whose absence is worth a sentence of its own.
_MINUTES = "minutes"

#: The shapes a transcript is published in (spec 13.2).
TRANSCRIPT_FORMATS = ("json", "vtt", "txt")

#: What each source a vote can come from is called in the reader's words. The
#: transcript label is the sentence the reading job leaves behind, spelled the
#: same way so a reader meets one wording and not two; a test pins it to the
#: constant the job itself writes.
SOURCE_LABELS: dict[str, str] = {
    "structured": "a structured record",
    "minutes": "the minutes",
    "packet": "the meeting packet",
    "transcript": "from video; minutes not yet available",
}

#: The unknown case, for a source kind added after this map was written.
UNKNOWN_SOURCE_LABEL = "an unnamed source"


def source_label(source_kind: str) -> str:
    """Return the plain name of a vote's source."""
    return SOURCE_LABELS.get(source_kind, UNKNOWN_SOURCE_LABEL)


def vote_precedence(source_kind: str) -> int:
    """Return the spec 10.4 rank of a source kind, 0 strongest.

    An unknown kind ranks below every known one, so a source added later never
    displaces the ones whose order the spec fixes.
    """
    try:
        return VOTE_SOURCE_KINDS.index(source_kind)
    except ValueError:
        return len(VOTE_SOURCE_KINDS)


def not_found(message: str) -> HTTPException:
    """Build the 404 the API answers an unknown id with, in plain words."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def media_type_of(meta: Any, rel_path: str) -> str:
    """Return the media type of an artifact from its meta and its stored name."""
    if isinstance(meta, dict):
        declared = meta.get("content_type")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    suffix = Path(rel_path).suffix.lstrip(".").lower()
    return _MEDIA_TYPES.get(suffix, DEFAULT_MEDIA_TYPE)


def _meta_dict(text: Any) -> dict[str, Any]:
    """Read the artifact meta column as an object. Anything else is empty."""
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _storage_root(request: Request) -> Path:
    """Return the artifact storage root this run was configured with."""
    return Path(request.app.state.storage_root).expanduser()


def _body_out(conn: sqlite3.Connection, body_id: int) -> BodyOut:
    row = get_body(conn, body_id)
    if row is None:
        raise not_found(f"There is no body {body_id}.")
    return BodyOut(id=row.id, jurisdiction_id=row.jurisdiction_id, name=row.name)


def _jurisdiction_out(conn: sqlite3.Connection, jurisdiction_id: int) -> JurisdictionOut:
    row = area_repo.get_jurisdiction(conn, jurisdiction_id)
    if row is None:
        raise not_found(f"There is no jurisdiction {jurisdiction_id}.")
    return JurisdictionOut(
        id=row.id,
        type=row.type,
        name=row.name,
        official_id=row.official_id,
        official_id_kind=row.official_id_kind,
        parent_id=row.parent_id,
        overlaps=area_repo.overlaps_of(conn, row.id),
    )


def _document_out(row: sqlite3.Row) -> DocumentOut:
    return DocumentOut(
        id=int(row["id"]),
        kind=str(row["kind"]),
        title=row["title"],
        source_id=row["source_id"],
        artifact_id=int(row["artifact_id"]),
        artifact_sha256=str(row["sha256"]),
        media_type=media_type_of(_meta_dict(row["artifact_meta"]), str(row["rel_path"])),
        page_count=row["page_count"],
        pages_read=int(row["pages_read"]),
    )


def _documents_of(conn: sqlite3.Connection, meeting_id: int) -> list[DocumentOut]:
    """Return the documents of a meeting, with the facts about their files.

    One statement: the document, the artifact it points at, and how many of
    its pages carry text. A meeting holds a handful of documents, so this is
    a small read even for a long meeting.
    """
    rows = conn.execute(
        "SELECT records.*, artifacts.sha256 AS sha256, artifacts.meta AS artifact_meta, "
        "artifacts.rel_path AS rel_path, "
        "(SELECT COUNT(*) FROM record_pages WHERE record_pages.record_id = records.id) "
        "AS pages_read "
        "FROM records JOIN artifacts ON artifacts.id = records.artifact_id "
        "WHERE records.meeting_id = ? ORDER BY records.kind, records.id",
        (meeting_id,),
    ).fetchall()
    return [_document_out(row) for row in rows]


def _videos_of(conn: sqlite3.Connection, meeting_id: int) -> list[VideoOut]:
    """Return the recordings of a meeting, primary first, with their transcript."""
    rows = conn.execute(
        "SELECT videos.*, "
        "(SELECT transcripts.id FROM transcripts WHERE transcripts.video_id = videos.id "
        " ORDER BY transcripts.is_provisional, transcripts.id DESC LIMIT 1) AS transcript_id "
        "FROM videos WHERE videos.meeting_id = ? ORDER BY videos.is_primary DESC, videos.id",
        (meeting_id,),
    ).fetchall()
    return [
        VideoOut(
            id=int(row["id"]),
            platform_video_id=str(row["platform_video_id"]),
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            duration_s=row["duration_s"],
            is_primary=bool(row["is_primary"]),
            capture_state=str(row["capture_state"]),
            readiness=str(row["readiness"]),
            transcript_id=row["transcript_id"],
        )
        for row in rows
    ]


def meeting_summary(conn: sqlite3.Connection, meeting: Any) -> MeetingSummary:
    """Build one meeting answer from a meeting row."""
    return MeetingSummary(
        id=meeting.id,
        body_id=meeting.body_id,
        title=meeting.title,
        starts_at=meeting.starts_at,
        type=meeting.type,
        is_cancelled=meeting.is_cancelled,
        is_continued=meeting.is_continued,
        documents=_documents_of(conn, meeting.id),
        videos=_videos_of(conn, meeting.id),
    )


def _item_out(item: AgendaItem) -> AgendaItemOut:
    return AgendaItemOut(
        id=item.id,
        number=item.number,
        title=item.title,
        identifiers=item.identifiers,
        start_ms=item.start_ms,
        end_ms=item.end_ms,
        alignment_method=item.alignment_method,
        alignment_reason=item.alignment_reason,
    )


def _vote_out(vote: Vote) -> VoteOut:
    """Shape one vote for the answer, with its source named in plain words."""
    return VoteOut(
        id=vote.id,
        result=vote.result,
        source_kind=vote.source_kind,
        source_label=source_label(vote.source_kind),
        precedence=vote_precedence(vote.source_kind),
        evidence=vote.evidence,
        tally=vote.tally,
        motion_id=vote.motion_id,
        citation_id=vote.citation_id,
    )


def _motion_out(motion: Motion) -> MotionOut:
    """Shape one motion for the answer, names and tally as the minutes printed them."""
    return MotionOut(
        id=motion.id,
        meeting_id=motion.meeting_id,
        record_id=motion.record_id,
        page_number=motion.page_number,
        ordinal=motion.ordinal,
        mover=motion.mover,
        seconder=motion.seconder,
        text=motion.text,
        result=motion.result,
        outcome=motion.outcome,
        approved=list(motion.approved),
        dissented=list(motion.dissented),
        abstained=list(motion.abstained),
        tally=motion.tally,
        evidence=motion.evidence,
        citation_id=motion.citation_id,
    )


def _item_with_votes(conn: sqlite3.Connection, item: AgendaItem) -> AgendaItemWithVotes:
    """One item with its votes and the motions made on it."""
    return AgendaItemWithVotes(
        **_item_out(item).model_dump(),
        votes=[_vote_out(vote) for vote in votes_of_item(conn, item.id)],
        motions=[_motion_out(motion) for motion in motions_of_item(conn, item.id)],
    )


def _notes_for(
    conn: sqlite3.Connection,
    meeting_id: int,
    documents: list[DocumentOut],
    videos: list[VideoOut],
    items: list[AgendaItem],
) -> list[str]:
    """Return the plain sentences for what this meeting is missing.

    An empty list means nothing checked came up missing. Each sentence names
    the gap and stops there; none of them stands in for data (rule F).
    """
    notes: list[str] = []
    if not documents:
        notes.append("No documents are stored for this meeting yet.")
    elif not any(document.kind == _MINUTES for document in documents):
        # The reading job searched and paused with its reason on its own row.
        # That sentence says where the minutes are expected, which is more than
        # the fact that they are not stored, so it is the one a reader gets
        # (spec 10.4). With no such row there is nothing more to say.
        notes.append(
            minutes_expectation(conn, meeting_id) or "No minutes are stored for this meeting yet."
        )
    if not videos:
        notes.append("No recording is listed for this meeting yet.")
    elif not any(video.transcript_id is not None for video in videos):
        notes.append("The recording of this meeting has no transcript yet.")
    if not items:
        notes.append("No agenda items are stored for this meeting yet.")
    for item in items:
        if item.alignment_method == "none":
            reason = item.alignment_reason.strip() or "no reason was recorded"
            notes.append(f"Item {item.number} has no time in the recording: {_sentence(reason)}")
    return notes


def _sentence(text: str) -> str:
    """End a fragment with one full stop, however the fragment was written.

    A reason is written by a person or a model and may already be a sentence.
    Adding a second stop to it reads as a defect, so the stop is only added
    when it is not already there.
    """
    stripped = text.strip()
    return stripped if stripped.endswith((".", "!", "?")) else f"{stripped}."


def _transcript_of(
    conn: sqlite3.Connection, meeting_id: int, storage_root: Path
) -> tuple[Any, Any, Any, str, list[Any]]:
    """Return the meeting, video, transcript, artifact hash and its segments.

    Each missing step answers 404 with the step that is missing, because a
    meeting with no recording and a recording with no transcript are two
    different gaps.
    """
    meeting = get_meeting(conn, meeting_id)
    if meeting is None:
        raise not_found(f"There is no meeting {meeting_id}.")
    video = primary_video(conn, meeting_id)
    if video is None:
        raise not_found(f"Meeting {meeting_id} has no recording, so it has no transcript.")
    transcript = latest_transcript(conn, video.id)
    if transcript is None:
        raise not_found(f"The recording of meeting {meeting_id} has no transcript yet.")
    artifact = artifacts.get(conn, transcript.artifact_id, storage_root)
    if artifact is None:
        raise not_found(
            f"The transcript {transcript.id} points at artifact {transcript.artifact_id}, "
            f"which is not in the database."
        )
    return meeting, video, transcript, artifact.sha256, segments_of(conn, transcript.id)


def _vtt_timestamp(milliseconds: int) -> str:
    """Write milliseconds as the WebVTT ``HH:MM:SS.mmm`` timestamp."""
    total = max(0, int(milliseconds))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _vtt_escape(text: str) -> str:
    """Escape the two characters WebVTT reads as markup.

    A line of speech is data. Without this, a less than sign in the text would
    open a tag and the reader would drop everything up to the next one.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;")


def render_vtt(segments: list[Any]) -> str:
    """Render segments as WebVTT.

    A cue that holds no text is left out, because a cue with no payload is not
    a cue. A speaker label is written as a voice span, which the parser of
    ``captions.parse`` strips again, so a round trip through this text gives
    back the same start times and the same words.
    """
    lines = ["WEBVTT", ""]
    for segment in segments:
        if not segment.text.strip():
            continue
        start = _vtt_timestamp(segment.start_ms)
        end = _vtt_timestamp(max(segment.end_ms, segment.start_ms))
        lines.append(f"{start} --> {end}")
        body = _vtt_escape(segment.text)
        if segment.speaker_label:
            lines.append(f"<v {segment.speaker_label}>{body}</v>")
        else:
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


def render_text(segments: list[Any]) -> str:
    """Render segments as plain text, one line of speech per line."""
    lines: list[str] = []
    for segment in segments:
        stamp = _vtt_timestamp(segment.start_ms)[:8]
        speaker = f"{segment.speaker_label}: " if segment.speaker_label else ""
        lines.append(f"[{stamp}] {speaker}{segment.text}")
    return "\n".join(lines) + ("\n" if lines else "")


@router.get("/area", response_model=AreaOut, summary="The area: levels, bodies and sources")
def get_area(conn: sqlite3.Connection = Depends(get_connection)) -> AreaOut:
    """Return every jurisdiction as a tree, and the bodies and sources flat.

    The tree is built from ``parent_id`` here rather than stored a second
    time, so moving a jurisdiction under another one is a change to one row
    (decision 10 rule C).
    """
    every = area_repo.jurisdictions(conn)
    nodes = {
        row.id: JurisdictionNode(
            id=row.id,
            type=row.type,
            name=row.name,
            official_id=row.official_id,
            official_id_kind=row.official_id_kind,
            parent_id=row.parent_id,
            overlaps=area_repo.overlaps_of(conn, row.id),
            children=[],
        )
        for row in every
    }
    roots: list[JurisdictionNode] = []
    for row in every:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id) if row.parent_id is not None else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
    return AreaOut(
        jurisdictions=roots,
        bodies=[
            BodyOut(id=row.id, jurisdiction_id=row.jurisdiction_id, name=row.name)
            for row in area_repo.bodies(conn)
        ],
        sources=[
            SourceOut(
                id=row.id,
                jurisdiction_id=row.jurisdiction_id,
                body_id=row.body_id,
                type=row.type,
                origin=row.origin,
                status=row.status,
                consecutive_failures=row.consecutive_failures,
                last_error=row.last_error,
            )
            for row in area_repo.all_sources(conn)
        ],
    )


@router.get(
    "/bodies/{body_id}/meetings",
    response_model=list[MeetingSummary],
    summary="The meetings of one body, with document and video status",
)
def get_body_meetings(
    body_id: int,
    conn: sqlite3.Connection = Depends(get_connection),
    date_from: Annotated[
        str | None,
        Query(alias="from", description="Earliest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(alias="to", description="Latest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
) -> list[MeetingSummary]:
    """Return the meetings of a body, earliest first.

    The dates are local dates as the body published them and are compared on
    the published string, so a meeting never moves across a day boundary
    because the server sits in another time zone (spec 16.2).
    """
    if get_body(conn, body_id) is None:
        raise not_found(f"There is no body {body_id}.")
    return [
        meeting_summary(conn, meeting)
        for meeting in meetings_of_body(conn, body_id, date_from, date_to)
    ]


@router.get(
    "/meetings/{meeting_id}",
    response_model=MeetingDetail,
    summary="One meeting, with its items, votes and missing-record notes",
)
def get_meeting_detail(
    meeting_id: int, conn: sqlite3.Connection = Depends(get_connection)
) -> MeetingDetail:
    """Return one meeting with everything read about it, and what is missing."""
    meeting = get_meeting(conn, meeting_id)
    if meeting is None:
        raise not_found(f"There is no meeting {meeting_id}.")
    items = agenda_items(conn, meeting_id)
    documents = _documents_of(conn, meeting_id)
    videos = _videos_of(conn, meeting_id)
    return MeetingDetail(
        meeting=meeting_summary(conn, meeting),
        body=_body_out(conn, meeting.body_id),
        jurisdiction=_jurisdiction_out(
            conn, area_repo.get_body(conn, meeting.body_id).jurisdiction_id
        ),
        items=[_item_with_votes(conn, item) for item in items],
        notes=_notes_for(conn, meeting_id, documents, videos, items),
    )


@router.get(
    "/meetings/{meeting_id}/items",
    response_model=list[AgendaItemWithVotes],
    summary="The agenda items of one meeting, with their times and votes",
)
def get_meeting_items(
    meeting_id: int, conn: sqlite3.Connection = Depends(get_connection)
) -> list[AgendaItemWithVotes]:
    """Return the agenda items of a meeting, in time order, untimed ones last."""
    if get_meeting(conn, meeting_id) is None:
        raise not_found(f"There is no meeting {meeting_id}.")
    return [_item_with_votes(conn, item) for item in agenda_items(conn, meeting_id)]


@router.get(
    "/meetings/{meeting_id}/transcript",
    response_model=TranscriptOut,
    responses={
        200: {
            "content": {
                "text/vtt": {"schema": {"type": "string"}},
                "text/plain": {"schema": {"type": "string"}},
            },
            "description": "The transcript as WebVTT or as plain text.",
        }
    },
    summary="The transcript of one meeting, as json, vtt or txt",
)
def get_meeting_transcript(
    request: Request,
    meeting_id: int,
    conn: sqlite3.Connection = Depends(get_connection),
    format: Annotated[
        str,
        Query(description="json, vtt or txt. Anything else is refused."),
    ] = "json",
) -> TranscriptOut | Response:
    """Return the transcript of the primary recording of a meeting.

    The lines carry their times, their speaker when the source named one, and
    the agenda item each one falls in. The item is resolved from the item time
    ranges at read time, never stored on the line (spec 10.2), so a later
    alignment changes this answer without rewriting a single segment.
    """
    if format not in TRANSCRIPT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format is json, vtt or txt. It was {format!r}.",
        )
    meeting, video, transcript, sha256, segments = _transcript_of(
        conn, meeting_id, _storage_root(request)
    )
    if format != "json":
        rendered = render_vtt(segments) if format == "vtt" else render_text(segments)
        media = "text/vtt; charset=utf-8" if format == "vtt" else "text/plain; charset=utf-8"
        return Response(content=rendered, media_type=media)
    by_item = items_for_segments(conn, transcript.id)
    return TranscriptOut(
        meeting_id=meeting.id,
        video_id=video.id,
        platform_video_id=video.platform_video_id,
        transcript_id=transcript.id,
        origin=transcript.origin,
        is_provisional=transcript.is_provisional,
        artifact_id=transcript.artifact_id,
        artifact_sha256=sha256,
        segments=[
            SegmentOut(
                id=segment.id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=segment.speaker_label,
                agenda_item_id=(
                    None if by_item.get(segment.id) is None else by_item[segment.id].id
                ),
                agenda_item_number=(
                    None if by_item.get(segment.id) is None else by_item[segment.id].number
                ),
            )
            for segment in segments
        ],
    )


def _record_row(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row:
    """Return the document row joined to its artifact, or raise a plain 404.

    The document, the file it is stored as, and how many of its pages carry
    text, in one statement.
    """
    row = conn.execute(
        "SELECT records.*, artifacts.sha256 AS sha256, artifacts.meta AS artifact_meta, "
        "artifacts.rel_path AS rel_path, "
        "(SELECT COUNT(*) FROM record_pages WHERE record_pages.record_id = records.id) "
        "AS pages_read "
        "FROM records JOIN artifacts ON artifacts.id = records.artifact_id "
        "WHERE records.id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        raise not_found(f"There is no record {record_id}.")
    return row


@router.get("/records/{record_id}", response_model=RecordOut, summary="One document")
def get_record(record_id: int, conn: sqlite3.Connection = Depends(get_connection)) -> RecordOut:
    """Return one document with the facts about the file it is stored as."""
    row = _record_row(conn, record_id)
    return RecordOut(
        meeting_id=int(row["meeting_id"]),
        **_document_out(row).model_dump(),
    )


@router.get("/records/{record_id}/file", summary="The stored file of a document")
def get_record_file(
    request: Request, record_id: int, conn: sqlite3.Connection = Depends(get_connection)
) -> FileResponse:
    """Return the bytes of the document exactly as they were stored.

    The bytes are served from the content-addressed store, so they are the
    bytes the SHA-256 in the name claims: the artifact is never rewritten
    (spec 8.6).
    """
    artifact_id = int(_record_row(conn, record_id)["artifact_id"])
    root = _storage_root(request)
    artifact = artifacts.get(conn, artifact_id, root)
    if artifact is None:
        raise not_found(
            f"Record {record_id} points at artifact {artifact_id}, which is not in the database."
        )
    path = (root / artifact.rel_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise not_found(f"Artifact {artifact.id} is stored outside the storage root.")
    if not path.is_file():
        raise not_found(
            f"The file for artifact {artifact.id} ({artifact.rel_path}) is missing "
            f"from the storage root."
        )
    return FileResponse(path, media_type=media_type_of(artifact.meta, artifact.rel_path))


@router.get(
    "/records/{record_id}/pages/{page_number}",
    response_model=RecordPageOut,
    summary="One page of a document",
)
def get_record_page(
    record_id: int, page_number: int, conn: sqlite3.Connection = Depends(get_connection)
) -> RecordPageOut:
    """Return one page of a document, by its page number as published."""
    row = conn.execute(
        "SELECT * FROM record_pages WHERE record_id = ? AND page_number = ?",
        (record_id, page_number),
    ).fetchone()
    if row is None:
        raise not_found(f"Record {record_id} has no page {page_number}.")
    return RecordPageOut(
        id=int(row["id"]),
        record_id=int(row["record_id"]),
        page_number=int(row["page_number"]),
        footer_page_number=row["footer_page_number"],
        text=str(row["text"]),
    )


@router.get("/search", response_model=SearchResults, summary="Full-text search")
def get_search(
    conn: sqlite3.Connection = Depends(get_connection),
    q: Annotated[str, Query(description="The text to search for. It is data, not syntax.")] = "",
    level: Annotated[
        str | None, Query(description="A jurisdiction level, for example city.")
    ] = None,
    body: Annotated[int | None, Query(description="A body id.")] = None,
    date_from: Annotated[
        str | None,
        Query(alias="from", description="Earliest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(alias="to", description="Latest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
    type: Annotated[
        str | None,
        Query(description="A record kind, which limits the answer to documents."),
    ] = None,
    limit: Annotated[int, Query(ge=0, le=200, description="The most hits to return.")] = 50,
) -> SearchResults:
    """Search the spoken lines and the record pages of the area (spec 12.4).

    User text never reaches the query syntax of the index. A query is split
    into words and each word is quoted, so ``ordinance "2026-54`` and
    ``AND OR`` are searched for as the words they look like, and neither can
    raise (spec 13.2).
    """
    hits = search(
        conn,
        q,
        level=level,
        body=body,
        date_from=date_from,
        date_to=date_to,
        type=type,
        limit=limit,
    )
    return SearchResults(
        query=q,
        count=len(hits),
        hits=[
            SearchHitOut(
                scope=hit.scope,
                excerpt=hit.excerpt,
                start_ms=hit.start_ms,
                end_ms=hit.end_ms,
                speaker_label=hit.speaker_label,
                page_number=hit.page_number,
                footer_page_number=hit.footer_page_number,
                meeting_id=hit.meeting_id,
                meeting_title=hit.meeting_title,
                starts_at=hit.starts_at,
                body_id=hit.body_id,
                body_name=hit.body_name,
                jurisdiction_id=hit.jurisdiction_id,
                jurisdiction_name=hit.jurisdiction_name,
                level=hit.level,
                citation=SearchCitationOut(**vars(hit.citation)),
            )
            for hit in hits
        ],
    )


@router.get(
    "/votes",
    response_model=VotesResponse,
    summary="The votes of the area, strongest source first",
)
def get_votes(
    conn: sqlite3.Connection = Depends(get_connection),
    body: Annotated[int | None, Query(description="A body id.")] = None,
    identifier: Annotated[
        str | None,
        Query(description="An agenda item identifier, by key or by value."),
    ] = None,
    date_from: Annotated[
        str | None,
        Query(alias="from", description="Earliest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(alias="to", description="Latest meeting date, inclusive, as YYYY-MM-DD."),
    ] = None,
) -> VotesResponse:
    """Return the votes an area holds, newest meeting first (spec 13.2).

    Every filter is optional and they narrow together. Where two sources
    disagree about an item, both are returned and neither is picked, in the
    spec 10.4 order, so the caller sees the disagreement instead of a winner
    chosen for them (rule E).

    A vote that came off the video is labelled with the sentence the reading
    job leaves, and it carries no tally: a mention in speech is not a count
    (spec 10.4).
    """
    if body is not None and get_body(conn, body) is None:
        raise not_found(f"There is no body {body}.")
    rows = find_votes(
        conn,
        body_id=body,
        identifier=identifier,
        from_date=date_from,
        to_date=date_to,
    )
    return VotesResponse(
        count=len(rows),
        votes=[
            VoteWithContext(
                **_vote_out(row.vote).model_dump(),
                agenda_item_id=row.agenda_item_id,
                item_number=row.item_number,
                item_title=row.item_title,
                identifiers=row.identifiers,
                meeting_id=row.meeting_id,
                meeting_title=row.meeting_title,
                starts_at=row.starts_at,
                body_id=row.body_id,
                body_name=row.body_name,
            )
            for row in rows
        ],
    )


@router.get(
    "/citations/{citation_id}/verify",
    response_model=CitationVerifyOut,
    summary="Whether a citation still rests on the bytes it points at",
)
def get_citation_verify(
    request: Request,
    citation_id: int,
    conn: sqlite3.Connection = Depends(get_connection),
) -> CitationVerifyOut:
    """Recompute the cited artifact's hash and say whether it still matches.

    The hash is read from the artifact row and never from the citation, and the
    file is hashed again here, so a citation to a file that was written over is
    caught rather than answered with the hash it was created with (spec 10.5).

    The page or the moment the citation points at comes back with the answer,
    so a caller can go and look at it without a second request.
    """
    citation = get_citation(conn, citation_id)
    if citation is None:
        raise not_found(f"There is no citation {citation_id}.")
    claimed = citation_sha256(conn, citation_id)
    if claimed is None:
        raise not_found(
            f"Citation {citation_id} points at artifact {citation.artifact_id}, "
            f"which is not in the database."
        )
    root = _storage_root(request)
    artifact = artifacts.get(conn, citation.artifact_id, root)
    computed: str | None = None
    reason = ""
    if artifact is None:
        reason = f"The artifact {citation.artifact_id} is not in the database."
    else:
        path = (root / artifact.rel_path).resolve()
        if not path.is_relative_to(root.resolve()):
            reason = f"Artifact {artifact.id} is stored outside the storage root."
        elif not path.is_file():
            reason = f"The file for artifact {artifact.id} ({artifact.rel_path}) is missing."
        else:
            computed = artifacts.hash_file(path)
            if computed != claimed:
                reason = (
                    "The file no longer matches the hash the citation was made with: "
                    f"it is now {computed}."
                )
    return CitationVerifyOut(
        citation_id=citation.id,
        kind=citation.kind,
        matches=reason == "",
        artifact_sha256=claimed,
        computed_sha256=computed,
        reason=reason,
        excerpt=citation.excerpt,
        meeting_id=(
            None if citation.record_id is None else _record_meeting(conn, citation.record_id)
        ),
        record_id=citation.record_id,
        video_id=citation.video_id,
        page_number=citation.page_number,
        start_ms=citation.start_ms,
        end_ms=citation.end_ms,
    )


def _record_meeting(conn: sqlite3.Connection, record_id: int) -> int | None:
    """Return the meeting a document belongs to, or None when the row is gone."""
    row = conn.execute("SELECT meeting_id FROM records WHERE id = ?", (record_id,)).fetchone()
    return None if row is None else int(row["meeting_id"])
