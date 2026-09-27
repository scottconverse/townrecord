"""The PrimeGov records adapter (spec 9.2).

PrimeGov publishes a JSON meeting list, compiled PDFs behind a redirect to a
signed storage link, and HTML agendas that carry the clerk's video time for
each item. The base URL always comes from a configured source: this module
never names a city (spec 9.3, PROJECT-BRIEF rule D).

The signed storage link expires in about two days (measured September 27,
2026). It is followed and then forgotten: everything TownRecord keeps or cites
is a portal URL.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

import httpx

from .. import __version__
from .base import (
    PACKET_LIMIT_BYTES,
    PDF_LIMIT_BYTES,
    VIDEO_TIME_MISSING,
    VIDEO_TIME_OUTSIDE_VIDEO,
    VIDEO_TIME_USABLE,
    AdapterError,
    AdapterHttpError,
    AgendaItem,
    Document,
    DocumentContent,
    DocumentTooLarge,
    HealthResult,
    Meeting,
    RedirectLimitExceeded,
)

#: Paths of the public portal, relative to the configured base URL (spec 9.2).
ARCHIVED_MEETINGS_PATH = "/api/v2/PublicPortal/ListArchivedMeetings"
UPCOMING_MEETINGS_PATH = "/api/v2/PublicPortal/ListUpcomingMeetings"
COMPILED_DOCUMENT_PATH = "/Public/CompiledDocument"
PORTAL_MEETING_PATH = "/Portal/Meeting"

#: The adapter names itself honestly, with its own name first (spec 9.2).
USER_AGENT_FORMAT = "TownRecord/{version} (+https://github.com/scottconverse/townrecord)"

#: The outbound fetch guard allows at most four redirects (spec 17).
MAX_REDIRECTS = 4

#: Redirect statuses the adapter follows by hand so that it can count them.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_CHARSET_IN_CONTENT_TYPE = re.compile(r"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.IGNORECASE)
_CHARSET_IN_META = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.IGNORECASE | re.DOTALL
)
_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def user_agent(version: str | None = None) -> str:
    """Return the User-Agent every request of this adapter carries (spec 9.2)."""
    return USER_AGENT_FORMAT.format(version=version or __version__)


def _codec_name(text: str) -> str:
    """Normalize a charset name from a header or a meta tag."""
    return text.strip().strip("\"'").strip().lower()


def detect_encoding(raw: bytes, content_type: str | None = None) -> str:
    """Return the encoding of an HTML body, in this order of evidence.

    1. A byte-order mark, which wins over everything (WHATWG).
    2. A charset in the response's Content-Type header.
    3. A charset in a meta tag of the document.
    4. UTF-8, when the bytes are valid UTF-8.
    5. cp1252, the HTML default when nothing is declared (WHATWG).

    Rule 4 before rule 5 is a measured choice: the recorded agendas (measured
    September 27, 2026) are valid UTF-8, and their only non-ASCII runs are
    0xC2 0xA0, 0xE2 0x80 0x94 and 0xE2 0x80 0x99, which read as a
    non-breaking space, an em dash and a right single quotation mark.
    """
    for prefix, encoding in _BOMS:
        if raw.startswith(prefix):
            return encoding
    if content_type:
        found = _CHARSET_IN_CONTENT_TYPE.search(content_type)
        if found:
            return _codec_name(found.group(1))
    found = _CHARSET_IN_META.search(raw)
    if found:
        return _codec_name(found.group(1).decode("ascii", "replace"))
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def decode_html(raw: bytes, content_type: str | None = None) -> str:
    """Decode an HTML body to text without dropping characters silently.

    A declared encoding that does not fit the bytes falls back to replacement
    characters rather than an exception, which is what a browser does.
    """
    encoding = detect_encoding(raw, content_type)
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class _Node:
    """One element of the small tree the agenda parser walks."""

    __slots__ = ("attrs", "children", "tag")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    def text(self) -> str:
        """Return all the text under this node, tags removed."""
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts)

    def find_all(
        self,
        *,
        tag: str | None = None,
        class_name: str | None = None,
        attr: str | None = None,
    ) -> Iterator[_Node]:
        """Yield the nodes below this one that match, in document order."""
        for child in self.children:
            if not isinstance(child, _Node):
                continue
            if _matches(child, tag, class_name, attr):
                yield child
            yield from child.find_all(tag=tag, class_name=class_name, attr=attr)

    def find(self, **keys: Any) -> _Node | None:
        """Return the first matching node below this one, or None."""
        return next(self.find_all(**keys), None)


def _matches(node: _Node, tag: str | None, class_name: str | None, attr: str | None) -> bool:
    """Say whether one node satisfies the three optional keys of find_all."""
    if tag is not None and node.tag != tag:
        return False
    if class_name is not None and class_name not in node.attrs.get("class", "").split():
        return False
    return not (attr is not None and attr not in node.attrs)


class _TreeBuilder(HTMLParser):
    """Build a tolerant tree. Unclosed tags do not lose their content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(_Node(tag, {key: value or "" for key, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Collapse whitespace, including the non-breaking spaces PrimeGov emits."""
    return _WHITESPACE.sub(" ", text.replace("\xa0", " ")).strip()


def _number_stem(text: str) -> str:
    """Drop the trailing period of an agenda number: "9." becomes "9"."""
    return text.strip().rstrip(".").strip()


def _video_status(seconds: int | None, video_duration_s: int | None) -> str:
    """Classify one data-videolocation value (spec 10.2).

    A value longer than the video is outside the video. Whether the transcript
    near that time mentions the item is a later step and is not checked here.
    """
    if seconds is None:
        return VIDEO_TIME_MISSING
    if video_duration_s is not None and seconds > video_duration_s:
        return VIDEO_TIME_OUTSIDE_VIDEO
    return VIDEO_TIME_USABLE


def _parse_html(text: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def _seconds(value: str | None) -> int | None:
    """Read a data-videolocation value. Anything but digits counts as absent."""
    if value is None:
        return None
    text = value.strip()
    return int(text) if text.isdigit() else None


class PrimeGovAdapter:
    """Reads one PrimeGov portal. `base_url` is configured, never defaulted."""

    def __init__(
        self,
        base_url: str,
        http_client: httpx.Client,
        *,
        pdf_limit_bytes: int = PDF_LIMIT_BYTES,
        packet_limit_bytes: int = PACKET_LIMIT_BYTES,
        version: str | None = None,
    ) -> None:
        if not str(base_url or "").strip():
            raise ValueError(
                "PrimeGovAdapter needs a base URL. TownRecord never defaults to one city."
            )
        self.base_url = str(base_url).strip().rstrip("/")
        self.http = http_client
        self.pdf_limit_bytes = pdf_limit_bytes
        self.packet_limit_bytes = packet_limit_bytes
        self._headers = {"User-Agent": user_agent(version)}

    # -- URLs -----------------------------------------------------------------

    def archived_meetings_url(self, year: int) -> str:
        return f"{self.base_url}{ARCHIVED_MEETINGS_PATH}?year={year}"

    def upcoming_meetings_url(self) -> str:
        return f"{self.base_url}{UPCOMING_MEETINGS_PATH}"

    def portal_document_url(self, template_id: int) -> str:
        """The portal download URL of a compiled document (spec 9.2)."""
        return (
            f"{self.base_url}{COMPILED_DOCUMENT_PATH}"
            f"?meetingTemplateId={template_id}&compileOutputType=1"
        )

    def citation_url(self, meeting_id: int, template_id: int) -> str:
        """The portal URL a citation names, never the signed storage link."""
        return (
            f"{self.base_url}{PORTAL_MEETING_PATH}"
            f"?meetingId={meeting_id}&meetingTemplateId={template_id}"
        )

    # -- Listing --------------------------------------------------------------

    def list_meetings(self, from_date: date | datetime, to_date: date | datetime) -> list[Meeting]:
        """List meetings between two dates, newest last, from both lists.

        The archived list is asked once per year in the range. Meetings that
        appear in both lists are kept once, as the archived copy.
        """
        first = _as_date(from_date)
        last = _as_date(to_date)
        if last < first:
            raise AdapterError("The end of the date range is before its start.")

        found: dict[int, Meeting] = {}
        for year in range(first.year, last.year + 1):
            for payload in self._get_meeting_list(self.archived_meetings_url(year), {"year": year}):
                _remember(found, payload)
        for payload in self._get_meeting_list(self.upcoming_meetings_url(), None):
            _remember(found, payload)

        in_range = (m for m in found.values() if first <= m.start.date() <= last)
        return sorted(in_range, key=lambda meeting: meeting.start)

    def _get_meeting_list(self, url: str, params: dict[str, Any] | None) -> list[Any]:
        response = self._get(url, params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterHttpError(f"{url} did not answer with JSON.") from exc
        if not isinstance(payload, list):
            raise AdapterHttpError(f"{url} did not answer with a list of meetings.")
        return payload

    def _get(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        try:
            response = self.http.get(url, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise AdapterHttpError(f"{url} could not be reached: {exc}") from exc
        if response.status_code != 200:
            raise AdapterHttpError(f"{url} answered with status {response.status_code}.")
        return response

    # -- Documents ------------------------------------------------------------

    def limit_for(self, document: Document) -> int:
        """Return the size limit that applies to a document (spec 9.6)."""
        if "packet" in (document.template_name or "").lower():
            return self.packet_limit_bytes
        return self.pdf_limit_bytes

    def get_document(
        self, document: Document, *, limit_bytes: int | None = None
    ) -> DocumentContent:
        """Fetch a compiled document (compileOutputType 1) and hash it.

        The download passes through a redirect to a signed storage link that
        expires in about two days. That link is used and dropped: it is never
        returned, stored or logged. The size limit is checked twice, on the
        declared length and again on every chunk while the body streams.
        """
        if document.compile_output_type != 1:
            raise AdapterError(
                f"Template {document.template_id} is compileOutputType "
                f"{document.compile_output_type}, not a compiled document."
            )
        if document.meeting_id is None:
            raise AdapterError(
                f"Document {document.id} does not carry its meeting id, "
                "so a citation origin cannot be built."
            )

        limit = self.limit_for(document) if limit_bytes is None else limit_bytes
        source_url = self.portal_document_url(document.template_id)
        content, content_type = self._download(source_url, limit)

        return DocumentContent(
            content=content,
            content_type=content_type,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            source_url=source_url,
            citation_url=self.citation_url(document.meeting_id, document.template_id),
        )

    def _download(self, source_url: str, limit: int) -> tuple[bytes, str | None]:
        """Follow the redirects by hand and read the body within the limit."""
        url = source_url
        for _hop in range(MAX_REDIRECTS + 1):
            with self.http.stream(
                "GET", url, headers=self._headers, follow_redirects=False
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise AdapterHttpError(f"{source_url} redirected without a location.")
                    # The signed storage link lives in this variable only for
                    # the next request. It is never stored, returned or logged.
                    url = str(httpx.URL(url).join(location))
                    continue
                if response.status_code != 200:
                    raise AdapterHttpError(
                        f"{source_url} answered with status {response.status_code}."
                    )
                return (
                    _read_within_limit(response, limit, source_url),
                    response.headers.get("content-type"),
                )
        raise RedirectLimitExceeded(f"{source_url} redirected more than {MAX_REDIRECTS} times.")

    # -- HTML agendas ---------------------------------------------------------

    def parse_html_agenda(
        self, html_bytes: bytes, video_duration_s: int | None = None
    ) -> list[AgendaItem]:
        """Return the items of an HTML agenda (compileOutputType 3).

        Numbers come out as the agenda prints them: a top level item keeps its
        period ("1."), and a lettered item under a numbered section is written
        the way people cite it ("9.B", "10.A.2").

        `video_duration_s` is the length of the meeting video. A value longer
        than it is outside the video (spec 10.2), which is how the clerk's
        placeholders (585015 and 585021 seconds on September 8, 2026) come out
        on a 14,407 second video. Without a length, a value that is present is
        reported usable: the length check could not be made.
        """
        root = _parse_html(decode_html(html_bytes))
        items: list[AgendaItem] = []
        for section in root.find_all(attr="data-sectionid"):
            section_number = _clean(_text_of(section, tag="td", class_name="number-cell-section"))
            if section_number:
                items.append(
                    _agenda_item(
                        _number_stem(section_number) + ".",
                        _clean(_text_of(section, tag="td", class_name="section-heading")),
                        _seconds(section.attrs.get("data-videolocation")),
                        video_duration_s,
                    )
                )
            for element in section.find_all(class_name="meeting-item"):
                raw_number = _clean(_text_of(element, tag="td", class_name="number-cell"))
                if not raw_number:
                    continue
                number = _number_stem(raw_number)
                if section_number:
                    number = f"{_number_stem(section_number)}.{number}"
                items.append(
                    _agenda_item(
                        number,
                        _clean(_text_of(element, class_name="agenda-item")),
                        _seconds(element.attrs.get("data-videolocation")),
                        video_duration_s,
                    )
                )
        return items

    # -- Health ---------------------------------------------------------------

    def health(self, *, window_days: int = 7, today: date | None = None) -> HealthResult:
        """Run list_meetings over one recent window and report plainly."""
        last = today or date.today()
        first = last - timedelta(days=window_days)
        checked = tuple(
            [self.archived_meetings_url(year) for year in range(first.year, last.year + 1)]
            + [self.upcoming_meetings_url()]
        )
        try:
            meetings = self.list_meetings(first, last)
        except AdapterError as exc:
            return HealthResult(False, self.base_url, first, last, 0, checked, str(exc))
        if not meetings:
            return HealthResult(
                False,
                self.base_url,
                first,
                last,
                0,
                checked,
                f"No meeting was listed between {first.isoformat()} and {last.isoformat()}.",
            )
        return HealthResult(True, self.base_url, first, last, len(meetings), checked, None)


def _agenda_item(
    number: str, title: str, seconds: int | None, video_duration_s: int | None
) -> AgendaItem:
    return AgendaItem(number, title, seconds, _video_status(seconds, video_duration_s))


def _text_of(node: _Node, **keys: Any) -> str:
    found = node.find(**keys)
    return found.text() if found is not None else ""


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _remember(found: dict[int, Meeting], payload: Any) -> None:
    meeting = _meeting_from(payload)
    if meeting is not None and meeting.id not in found:
        found[meeting.id] = meeting


def _meeting_from(payload: Any) -> Meeting | None:
    """Build a Meeting from one list entry. Entries without an id or a time
    are skipped: nothing can be recorded about them."""
    if not isinstance(payload, dict):
        return None
    raw_id = payload.get("id")
    raw_start = payload.get("dateTime")
    if raw_id is None or not isinstance(raw_start, str):
        return None
    try:
        start = datetime.fromisoformat(raw_start)
        meeting_id = int(raw_id)
    except ValueError:
        return None
    documents = tuple(
        document
        for document in (
            _document_from(entry, meeting_id) for entry in payload.get("documentList") or []
        )
        if document is not None
    )
    return Meeting(
        id=meeting_id,
        title=_clean(str(payload.get("title") or "")),
        start=start,
        video_url=payload.get("videoUrl") or None,
        documents=documents,
    )


def _document_from(payload: Any, meeting_id: int) -> Document | None:
    if not isinstance(payload, dict):
        return None
    template_id = payload.get("templateId")
    if template_id is None:
        return None
    try:
        return Document(
            id=int(payload.get("id") or 0),
            template_id=int(template_id),
            compile_output_type=int(payload.get("compileOutputType") or 0),
            template_name=str(payload.get("templateName") or ""),
            publish_date=payload.get("publishDate"),
            meeting_id=meeting_id,
        )
    except (TypeError, ValueError):
        return None


def _read_within_limit(response: httpx.Response, limit: int, url: str) -> bytes:
    """Read a streaming body, refusing it once it passes the limit twice over."""
    declared = response.headers.get("content-length")
    if declared and declared.strip().isdigit() and int(declared) > limit:
        raise DocumentTooLarge(
            f"{url} declares {int(declared)} bytes, over the limit of {limit} bytes."
        )
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise DocumentTooLarge(f"{url} passed the limit of {limit} bytes while streaming.")
        chunks.append(chunk)
    return b"".join(chunks)
