"""The shapes the read API answers with (spec 13.2, spec M5).

One model per answer, declared rather than returned as a bare dictionary, so
``/openapi.json`` describes every field and a caller can read the API before
reading the code. The models say what the data is; the routes in ``read.py``
build them and decide nothing else.

The rows of the repository layer are the same data with the column names of the
database. A model here is the published shape, so it may add a field that is
derived (a page count, a media type) and may leave out a column that no reader
needs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JurisdictionOut(BaseModel):
    """A level of government, with the ids of the jurisdictions it overlaps."""

    id: int
    type: str
    name: str
    official_id: str | None = None
    official_id_kind: str | None = None
    parent_id: int | None = None
    overlaps: list[int] = Field(default_factory=list)


class JurisdictionNode(JurisdictionOut):
    """A jurisdiction with the jurisdictions inside it (spec 6.1).

    The tree is built from ``parent_id``, so the nesting is a shape the API
    gives, not a second copy of the fact. A jurisdiction with no parent is a
    root.
    """

    children: list[JurisdictionNode] = Field(default_factory=list)


JurisdictionNode.model_rebuild()


class BodyOut(BaseModel):
    """A group that holds public meetings."""

    id: int
    jurisdiction_id: int
    name: str


class SourceOut(BaseModel):
    """A place where something is published, with its standing (spec 7.3)."""

    id: int
    jurisdiction_id: int
    body_id: int | None = None
    type: str
    origin: str
    status: str
    consecutive_failures: int
    last_error: str | None = None


class AreaOut(BaseModel):
    """The whole area: what levels exist, who meets, and where it is published."""

    jurisdictions: list[JurisdictionNode] = Field(default_factory=list)
    bodies: list[BodyOut] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)


class VideoOut(BaseModel):
    """A recording of a meeting, with how far it has been captured."""

    id: int
    platform_video_id: str
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    duration_s: int | None = None
    is_primary: bool
    capture_state: str
    readiness: str
    transcript_id: int | None = None


class DocumentOut(BaseModel):
    """A document of a meeting, and how much of its text has been read."""

    id: int
    kind: str
    title: str | None = None
    source_id: int | None = None
    artifact_id: int
    artifact_sha256: str
    media_type: str
    page_count: int | None = None
    pages_read: int


class MeetingSummary(BaseModel):
    """One meeting, with the state of its documents and its recordings.

    ``starts_at`` is the local start time as the body published it, with its
    offset, and is never converted (spec 16.2).
    """

    id: int
    body_id: int
    title: str | None = None
    starts_at: str
    type: str
    is_cancelled: bool
    is_continued: bool
    documents: list[DocumentOut] = Field(default_factory=list)
    videos: list[VideoOut] = Field(default_factory=list)


class VoteOut(BaseModel):
    """A result on an item, with the evidence it rests on (spec 10.4).

    ``source_label`` is the plain name of the source in the reader's words, and
    for a transcript vote it is the sentence the reading job leaves: the minutes
    are not there yet and the vote came off the video. ``precedence`` is the
    spec 10.4 order, 0 strongest. ``motion_id`` names the motion the outcome
    came out of, and ``citation_id`` the citation it rests on.
    """

    id: int
    result: str
    source_kind: str
    source_label: str
    precedence: int
    evidence: str
    tally: dict[str, Any] | None = None
    motion_id: int | None = None
    citation_id: int | None = None


class MotionOut(BaseModel):
    """One motion a meeting's minutes record (spec 10.4).

    The names are the ones the minutes printed, so an empty list is a list
    nobody was named in rather than one nobody read. ``page_number`` and
    ``citation_id`` say which page of which document the motion rests on.
    """

    id: int
    meeting_id: int
    record_id: int
    page_number: int
    ordinal: int
    mover: str
    seconder: str
    text: str
    result: str
    outcome: str
    approved: list[str] = Field(default_factory=list)
    dissented: list[str] = Field(default_factory=list)
    abstained: list[str] = Field(default_factory=list)
    tally: dict[str, Any] | None = None
    evidence: str
    citation_id: int | None = None


class VoteWithContext(VoteOut):
    """A vote with the item, the meeting and the body it was recorded on.

    A list read across meetings has to say where each vote came from, and the
    caller has no id to look the rest up by.
    """

    agenda_item_id: int
    item_number: str
    item_title: str
    identifiers: dict[str, Any] = Field(default_factory=dict)
    meeting_id: int
    meeting_title: str | None = None
    starts_at: str
    body_id: int
    body_name: str


class VotesResponse(BaseModel):
    """The votes one area holds that match what was asked for."""

    count: int
    votes: list[VoteWithContext] = Field(default_factory=list)


class CitationVerifyOut(BaseModel):
    """Whether a citation still rests on the bytes it points at (spec 10.5).

    ``artifact_sha256`` is the hash the citation was made with and
    ``computed_sha256`` the hash of the file as it is now. ``matches`` is the
    comparison, and ``reason`` says in plain words why not when it does not.
    """

    citation_id: int
    kind: str
    matches: bool
    artifact_sha256: str
    computed_sha256: str | None = None
    reason: str = ""
    excerpt: str
    meeting_id: int | None = None
    record_id: int | None = None
    video_id: int | None = None
    page_number: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class AgendaItemOut(BaseModel):
    """A numbered item, with the time range it was aligned to (spec 10.2).

    ``alignment_method`` is one of ``html_video_times``, ``spoken_transitions``
    or ``none``. Method ``none`` carries no range and ``alignment_reason`` says
    why in plain words.
    """

    id: int
    number: str
    title: str
    identifiers: dict[str, Any] = Field(default_factory=dict)
    start_ms: int | None = None
    end_ms: int | None = None
    alignment_method: str
    alignment_reason: str


class AgendaItemWithVotes(AgendaItemOut):
    """An agenda item, the votes recorded on it, and the motions made on it.

    The votes are strongest source first (spec 10.4). The motions are in the
    order they were moved, and the last one is the motion the item's outcome
    came out of.
    """

    votes: list[VoteOut] = Field(default_factory=list)
    motions: list[MotionOut] = Field(default_factory=list)


class MeetingDetail(BaseModel):
    """One meeting, everything read about it, and what is missing.

    ``notes`` is where an honest gap is stated in plain words: a meeting with
    no minutes of its own, or an item nobody could align to a time. An empty
    ``notes`` means nothing is missing, not that nothing was checked.
    """

    meeting: MeetingSummary
    body: BodyOut
    jurisdiction: JurisdictionOut
    items: list[AgendaItemWithVotes] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SegmentOut(BaseModel):
    """One line of speech, with the item it falls in when one is known."""

    id: int
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None = None
    agenda_item_id: int | None = None
    agenda_item_number: str | None = None


class TranscriptOut(BaseModel):
    """The transcript of a meeting, read from its primary recording.

    ``origin`` says where the text came from (``publisher_captions``,
    ``auto_captions``, ``sister_channel`` or ``local_speech_to_text``), and
    ``is_provisional`` says whether it has settled (spec 8.7).
    """

    meeting_id: int
    video_id: int
    platform_video_id: str
    transcript_id: int
    origin: str
    is_provisional: bool
    artifact_id: int
    artifact_sha256: str
    segments: list[SegmentOut] = Field(default_factory=list)


class RecordPageOut(BaseModel):
    """One page of a document, with its text and its printed footer number."""

    id: int
    record_id: int
    page_number: int
    footer_page_number: int | None = None
    text: str


class RecordOut(BaseModel):
    """One document of a meeting, as it is stored (spec 8.6)."""

    id: int
    meeting_id: int
    kind: str
    title: str | None = None
    source_id: int | None = None
    artifact_id: int
    artifact_sha256: str
    media_type: str
    page_count: int | None = None
    pages_read: int


class SearchCitationOut(BaseModel):
    """The evidence behind one search hit (spec 10.5).

    A video hit fills the video half: the video, the seconds, the verbatim
    excerpt, the SHA-256 of the transcript and where the transcript came from.
    A record hit fills the record half: the source, the meeting, the document,
    the page, the excerpt and the SHA-256 of the document.
    """

    kind: str
    excerpt: str
    meeting_id: int | None = None
    video_id: int | None = None
    platform_video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    transcript_id: int | None = None
    transcript_artifact_sha256: str | None = None
    transcript_origin: str | None = None
    record_id: int | None = None
    source_id: int | None = None
    source_origin: str | None = None
    page_number: int | None = None
    document_artifact_sha256: str | None = None


class SearchHitOut(BaseModel):
    """One line of speech or one page of a document that matched."""

    scope: str
    excerpt: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker_label: str | None = None
    page_number: int | None = None
    footer_page_number: int | None = None
    meeting_id: int | None = None
    meeting_title: str | None = None
    starts_at: str | None = None
    body_id: int | None = None
    body_name: str | None = None
    jurisdiction_id: int | None = None
    jurisdiction_name: str | None = None
    level: str | None = None
    citation: SearchCitationOut


class SearchResults(BaseModel):
    """The hits for one query, best match first within each kind of text."""

    query: str
    count: int
    hits: list[SearchHitOut] = Field(default_factory=list)
