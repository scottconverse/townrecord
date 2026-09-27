"""The records adapter interface every records system implements (spec 9.1).

An adapter lists meetings with their documents, fetches one document, and
answers a health check against a known meeting. `list_archive` (spec 9.1) is
optional and no adapter has needed it yet.

Every adapter takes its origin from a configured source. Nothing in this
package may default to a particular city (PROJECT-BRIEF rule D, spec 9.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

#: Spec 9.6: an HTML agenda is at most 5 MB by default.
HTML_LIMIT_BYTES = 5 * 1024 * 1024

#: Spec 9.6: a PDF is at most 25 MB by default.
PDF_LIMIT_BYTES = 25 * 1024 * 1024

#: Spec 9.6: large packets run 60 to 170 MB, so packets get their own higher
#: limit that the user can change. 200 MB clears the top of that range.
PACKET_LIMIT_BYTES = 200 * 1024 * 1024

#: Agenda item video times: the value is inside the video's length.
VIDEO_TIME_USABLE = "usable"

#: Agenda item video times: the value is longer than the video (spec 10.2).
VIDEO_TIME_OUTSIDE_VIDEO = "outside_video"

#: Agenda item video times: the agenda carries no value for the item.
VIDEO_TIME_MISSING = "missing"


class AdapterError(Exception):
    """A records adapter could not do what was asked, with a plain reason."""


class AdapterHttpError(AdapterError):
    """The records system answered with a status the adapter cannot use."""


class DocumentTooLarge(AdapterError):
    """A document is larger than the configured limit (spec 9.6)."""


class RedirectLimitExceeded(AdapterError):
    """A download redirected more often than the guard allows (spec 17)."""


@dataclass(frozen=True)
class Document:
    """One document that belongs to a meeting (spec 9.2).

    `meeting_id` comes from the meeting list and is what a citation uses. The
    signed storage link a download redirects to is never part of a Document.
    """

    id: int
    template_id: int
    compile_output_type: int
    template_name: str
    publish_date: str | None = None
    meeting_id: int | None = None


@dataclass(frozen=True)
class Meeting:
    """One meeting and its documents.

    `start` is the local time the records system gives, with no time zone
    added: TownRecord never invents one.
    """

    id: int
    title: str
    start: datetime
    video_url: str | None
    documents: tuple[Document, ...] = ()


@dataclass(frozen=True)
class DocumentContent:
    """A fetched document: its bytes, how to name them, and their hash.

    `source_url` and `citation_url` are portal URLs, never the signed storage
    link the download passes through (spec 9.2).
    """

    content: bytes
    content_type: str | None
    sha256: str
    size: int
    source_url: str
    citation_url: str


@dataclass(frozen=True)
class AgendaItem:
    """One item of an HTML agenda, with the clerk's video time if there is one."""

    number: str
    title: str
    video_seconds: int | None
    video_time_status: str


@dataclass(frozen=True)
class HealthResult:
    """What a health check found, in words the interface can show (spec 9.1)."""

    ok: bool
    base_url: str
    window_from: date
    window_to: date
    meetings_found: int
    checked: tuple[str, ...]
    reason: str | None

    def sentence(self) -> str:
        """One plain sentence, including what was checked (spec 16.3)."""
        window = f"{self.window_from.isoformat()} to {self.window_to.isoformat()}"
        if self.ok:
            return f"{self.base_url}: {self.meetings_found} meetings listed from {window}."
        return f"{self.base_url}: {self.reason} Checked {', '.join(self.checked)}."


class RecordsAdapter(Protocol):
    """The interface of spec 9.1."""

    def list_meetings(self, from_date: date, to_date: date) -> list[Meeting]:
        """List the meetings held between two dates, with their documents."""
        ...

    def get_document(self, document: Document) -> DocumentContent:
        """Return a document's bytes, its content type and its hash."""
        ...

    def health(self) -> HealthResult:
        """Test the adapter against one known recent meeting."""
        ...
