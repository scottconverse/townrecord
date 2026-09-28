"""Shared pieces for the PrimeGov job tests (spec 7.2, 7.3, 9.2, 9.6, 10.2).

Every portal answer comes from a fake portal on a test host, or from the
recorded fixtures of the oversight repository read in place. No test reaches
the network: the jobs ask ``townrecord.records.portal.open_client`` for their
client, and the tests replace that one function with a client built on an
``httpx.MockTransport`` (rule 7).

The fake portal is its own, rather than the adapter tests' one, because the
jobs read a page the adapter tests never ask for: the HTML agenda at
``/Portal/Meeting`` (spec 9.2).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from townrecord.adapters.primegov import (
    ARCHIVED_MEETINGS_PATH,
    COMPILED_DOCUMENT_PATH,
    PORTAL_MEETING_PATH,
    UPCOMING_MEETINGS_PATH,
)
from townrecord.jobs import ORIGIN_MANUAL, Registry, Runner, enqueue, get
from townrecord.records import register_jobs
from townrecord.repo import insert_body, insert_jurisdiction, insert_source

#: The origins the tests configure. Neither is a real city (rule D).
BASE_URL = "https://portal.test.invalid"

#: A second portal, to prove a sync asks the source it was given and no other.
OTHER_URL = "https://second.test.invalid"

#: The signed storage host a compiled document redirects to (spec 9.2).
SIGNED_HOST = "https://pgwest.blob.core.windows.net"

#: A signed query, shortened. No real signature, and none is needed.
SIGNED_QUERY = "sv=2023-11-03&se=2026-09-28T20%3A06Z&sig=notarealsignature"

#: The word that appears in the signed link and nowhere else. A test that finds
#: it in the database has found the signed link being stored (spec 9.2).
SIGNED_MARKER = "notarealsignature"

#: The recorded fixtures of the adapter tests, for hand-built portal answers.
FIXTURE_FOLDER = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"

#: The recorded fixtures of the oversight repository. Override with
#: ``TOWNRECORD_EVIDENCE`` when they live somewhere else.
EVIDENCE_FOLDER = Path(
    os.environ.get(
        "TOWNRECORD_EVIDENCE",
        Path(__file__).resolve().parents[4] / "townrecord-oversight" / "evidence" / "fixtures",
    )
)

#: The September 8, 2026 City Council regular session HTML agenda (16805).
AGENDA_16805 = EVIDENCE_FOLDER / "primegov" / "longmont-html-agenda-16805.html"

#: The srv3 caption track of video 3qfQAkAAC9U, the September 8, 2026 meeting.
CAPTIONS_16805 = EVIDENCE_FOLDER / "youtube" / "captions" / "3qfQAkAAC9U.en.srv3"

#: The September 22, 2026 City Council regular session HTML agenda (16821).
#: Its minutes are not published yet, so its votes are read from the video.
AGENDA_16821 = EVIDENCE_FOLDER / "primegov" / "longmont-html-agenda-16821.html"

#: The srv3 caption track of video jhsFsEz0P5A, the September 22, 2026 meeting.
CAPTIONS_SEP22 = EVIDENCE_FOLDER / "youtube" / "captions-sep22" / "jhsFsEz0P5A.en.srv3"

#: The meeting list of 2026, recorded from the portal.
ARCHIVED_2026 = EVIDENCE_FOLDER / "primegov" / "longmont-ListArchivedMeetings-2026.json"

#: The compiled PDF agenda of that meeting, recorded from the portal.
AGENDA_16805_PDF = EVIDENCE_FOLDER / "primegov" / "longmont-agenda-16805.pdf"

#: The run of the September 22, 2026 packet that carries the draft minutes of
#: the September 8, 2026 session (spec 9.4). Its first page is packet page 17,
#: and each of its 22 pages prints the number the packet gave it and the number
#: the draft gave it.
PACKET_MINUTES_SEP08 = (
    EVIDENCE_FOLDER / "primegov" / "longmont-packet-16823-minutes-sep08-p17-38.pdf"
)

#: The meeting the end-to-end test syncs: the September 8, 2026 regular session.
MEETING_3709 = 3709

#: Its video, and the measured length of that video.
VIDEO_3709 = "3qfQAkAAC9U"
VIDEO_3709_DURATION_S = 14407

#: The counts unit F measured on that meeting, by method (spec 10.2).
MEASURED_COUNTS = {"spoken_transitions": 22, "html_video_times": 6, "none": 12}

#: The offset unit F measured on that meeting, in seconds.
MEASURED_OFFSET_S = 466


def fixture_bytes(name: str) -> bytes:
    """Return the bytes of one recorded adapter fixture."""
    return (FIXTURE_FOLDER / name).read_bytes()


def fixture_json(name: str) -> Any:
    """Return the parsed JSON of one recorded adapter fixture."""
    return json.loads(fixture_bytes(name))


def needs(*paths: Path) -> pytest.MarkDecorator:
    """Skip a test when a recorded fixture is not on this machine."""
    missing = ", ".join(str(path) for path in paths if not path.exists())
    return pytest.mark.skipif(bool(missing), reason=f"recorded fixture not present: {missing}")


def document(
    document_id: int,
    template_id: int,
    compile_output_type: int,
    template_name: str,
) -> dict[str, Any]:
    """One document of a meeting list entry, as the portal publishes it."""
    return {
        "id": document_id,
        "templateId": template_id,
        "compileOutputType": compile_output_type,
        "templateName": template_name,
    }


def meeting(
    meeting_id: int,
    date_time: str,
    title: str,
    *,
    video_url: str | None = None,
    documents: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """One meeting list entry, as the portal publishes it."""
    return {
        "id": meeting_id,
        "dateTime": date_time,
        "title": title,
        "videoUrl": video_url,
        "documentList": list(documents),
    }


def trimmed_2026(meeting_ids: tuple[int, ...] = (MEETING_3709,)) -> list[dict[str, Any]]:
    """The recorded 2026 list, cut down to the meetings a test asks for.

    The recording is read in place rather than copied into the repository, and
    the trim is what keeps a test's window small enough to read: the whole year
    is 241 meetings, of which 40 carry a video.
    """
    wanted = set(meeting_ids)
    return [
        entry
        for entry in json.loads(ARCHIVED_2026.read_text(encoding="utf-8"))
        if entry.get("id") in wanted
    ]


class FakePortal:
    """A fake PrimeGov portal that answers the paths the jobs read."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.meetings: list[dict[str, Any]] = []
        self.html_agenda: bytes = fixture_bytes("agenda-16805.html")
        self.archived_status = 200
        #: The status the portal answers the HTML agenda page with. A page that
        #: cannot be read is a 500 here, not an empty body: an empty page parses
        #: to no items, which is not the same thing (spec 10.2).
        self.portal_meeting_status = 200
        self.document_bytes: bytes = fixture_bytes("fake-agenda.pdf")
        self.document_content_type: str | None = "application/pdf"
        self.redirect_hops = 1
        #: When set, the body is served as these chunks instead of the bytes
        #: above, which is how a body over the limit is served.
        self.document_chunks: list[bytes] | None = None

    # -- what a test asks for ------------------------------------------------

    def client(self) -> httpx.Client:
        """A client whose transport is this fake portal and nothing else."""
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def hosts(self) -> list[str]:
        """The hosts the jobs asked, in order."""
        return [request.url.host for request in self.requests]

    def paths(self) -> list[str]:
        """The paths the jobs asked, in order."""
        return [request.url.path for request in self.requests]

    def compiled_requests(self) -> list[httpx.Request]:
        """The requests of one compiled download, redirect hops included.

        The portal path spells ``CompiledDocument`` and the signed storage link
        that follows it spells ``compiled``, so the match is on the lowercased
        URL: what this returns is every hop of the download, in order.
        """
        return [request for request in self.requests if "compiled" in str(request.url).lower()]

    def signed_url(self, hop: int = 1) -> str:
        """One hop of the signed storage link a download passes through."""
        return f"{SIGNED_HOST}/compiled/18613_16805.pdf?{SIGNED_QUERY}&hop={hop}"

    def reset_requests(self) -> None:
        """Forget what was asked, so one test can look at one job's requests."""
        self.requests.clear()

    # -- the transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "pgwest.blob.core.windows.net":
            return self._signed_hop(int(url.params.get("hop") or "1"))
        if url.path == ARCHIVED_MEETINGS_PATH:
            if self.archived_status != 200:
                return httpx.Response(self.archived_status, text="the portal is unwell")
            return httpx.Response(200, json=self.meetings)
        if url.path == UPCOMING_MEETINGS_PATH:
            return httpx.Response(200, json=[])
        if url.path == PORTAL_MEETING_PATH:
            if self.portal_meeting_status != 200:
                return httpx.Response(self.portal_meeting_status, text="the agenda page is unwell")
            return httpx.Response(
                200,
                content=self.html_agenda,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if url.path == COMPILED_DOCUMENT_PATH:
            if self.redirect_hops == 0:
                return self._document_response()
            return httpx.Response(302, headers={"location": self.signed_url(1)})
        return httpx.Response(404, text=f"the fake portal has no {url.path}")

    def _signed_hop(self, hop: int) -> httpx.Response:
        if hop < self.redirect_hops:
            return httpx.Response(302, headers={"location": self.signed_url(hop + 1)})
        return self._document_response()

    def _document_response(self) -> httpx.Response:
        headers = {"content-type": self.document_content_type}
        if self.document_chunks is None:
            return httpx.Response(200, content=self.document_bytes, headers=headers)
        return httpx.Response(200, content=iter(self.document_chunks), headers=headers)


@pytest.fixture
def portal() -> FakePortal:
    """A fake PrimeGov portal on a test host."""
    return FakePortal()


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """A storage root of this test's own, never the user's."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, portal: FakePortal, storage_root: Path) -> FakePortal:
    """Point the records jobs at the fake portal and at a temporary store."""
    monkeypatch.setattr("townrecord.records.portal.open_client", portal.client)
    monkeypatch.setenv("TOWNRECORD_STORAGE", str(storage_root))
    return portal


class Area:
    """One jurisdiction, its bodies and the sources the tests sync."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.jurisdiction_id = insert_jurisdiction(conn, type="city", name="Test City")
        self.body_id = insert_body(conn, jurisdiction_id=self.jurisdiction_id, name="City Council")
        self.other_body_id = insert_body(
            conn, jurisdiction_id=self.jurisdiction_id, name="Parks Board"
        )
        self.portal_id = self.source(type="meeting_portal", origin=BASE_URL)
        self.other_portal_id = self.source(type="meeting_portal", origin=OTHER_URL)
        self.channel_id = self.source(type="video_channel", origin="https://channel.test.invalid")

    def source(
        self,
        *,
        type: str,
        origin: str,
        status: str = "accepted",
        body_id: int | None = None,
    ) -> int:
        """Store one source of this jurisdiction and return its id."""
        return insert_source(
            self.conn,
            jurisdiction_id=self.jurisdiction_id,
            type=type,
            origin=origin,
            suggested_by="a person",
            reason="the tests need one",
            status=status,
            body_id=body_id,
        )


@pytest.fixture
def area(conn: sqlite3.Connection) -> Area:
    """A jurisdiction with a body and the sources the tests sync."""
    return Area(conn)


class Sync:
    """Queue jobs and run them, one lane at a time, over one database."""

    def __init__(self, db_path: Path, conn: sqlite3.Connection) -> None:
        self.db_path = db_path
        self.conn = conn
        self.registry = Registry()
        register_jobs(self.registry)

    def runner(self) -> Runner:
        """A runner over this test's database, with the records kinds on it."""
        return Runner(self.db_path, registry=self.registry)

    def queue(self, kind: str, payload: Any = None, *, origin: str = ORIGIN_MANUAL) -> int:
        """Queue one job, on the lane its kind belongs to.

        ``origin`` is who asked for it: a person here, the daily schedule in
        the service (spec 16.1), and the children a job queues take after it.
        """
        return enqueue(self.conn, kind, payload, origin=origin)

    def queue_sync(
        self,
        source_id: int,
        *,
        from_date: str = "2026-09-01",
        to_date: str = "2026-09-30",
        origin: str = ORIGIN_MANUAL,
    ) -> int:
        """Queue one sync of a window, and return its id."""
        return self.queue(
            "sync_primegov",
            {"source_id": source_id, "from_date": from_date, "to_date": to_date},
            origin=origin,
        )

    def lane(self, lane: str) -> list[int]:
        """Run one lane until it has nothing left, and return the job ids run.

        One job at a time, because every job here queues more work: a sync
        queues downloads, and a download runs on another lane.
        """
        runner = self.runner()
        ran: list[int] = []
        while True:
            job_id = runner.run_once(lane)
            if job_id is None:
                return ran
            ran.append(job_id)

    def all_lanes(self) -> list[int]:
        """Run both lanes until neither has anything left."""
        ran: list[int] = []
        while True:
            ran.extend(self.lane("normal"))
            ran.extend(self.lane("heavy"))
            waiting = self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'running')"
            ).fetchone()[0]
            if not waiting:
                return ran

    def job(self, job_id: int) -> sqlite3.Row:
        """Return one job's row, which is where its state and reason are."""
        row = get(self.conn, job_id)
        assert row is not None
        return row

    def jobs_of_kind(self, kind: str) -> list[sqlite3.Row]:
        """Return every job of one kind, oldest first."""
        return list(
            self.conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (kind,)).fetchall()
        )


@pytest.fixture
def sync(db_path: Path, conn: sqlite3.Connection) -> Sync:
    """A sync helper over this test's database and the records registry."""
    return Sync(db_path, conn)


__all__ = [
    "AGENDA_16805",
    "AGENDA_16805_PDF",
    "ARCHIVED_2026",
    "BASE_URL",
    "CAPTIONS_16805",
    "EVIDENCE_FOLDER",
    "FakePortal",
    "MEASURED_COUNTS",
    "MEASURED_OFFSET_S",
    "MEETING_3709",
    "OTHER_URL",
    "PACKET_MINUTES_SEP08",
    "SIGNED_MARKER",
    "VIDEO_3709",
    "VIDEO_3709_DURATION_S",
    "Area",
    "Sync",
    "document",
    "fixture_bytes",
    "fixture_json",
    "meeting",
    "needs",
    "trimmed_2026",
]
