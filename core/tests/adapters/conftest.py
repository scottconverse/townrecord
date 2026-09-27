"""Shared pieces for the PrimeGov adapter tests.

Every response is a recorded fixture from `evidence/fixtures/primegov/` or is
built by hand here. No test reaches the network: the adapter's HTTP client is
an `httpx.Client` over `httpx.MockTransport`, which is why the client is
injected in the first place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from townrecord.adapters.primegov import (
    ARCHIVED_MEETINGS_PATH,
    COMPILED_DOCUMENT_PATH,
    UPCOMING_MEETINGS_PATH,
    PrimeGovAdapter,
)

#: A test host, so nothing here suggests a real city.
BASE_URL = "https://portal.test.invalid"

#: The signed storage host the portal redirects to (spec 9.2, measured 2026-09-27).
SIGNED_HOST = "https://pgwest.blob.core.windows.net"

#: The signed link of the measured capture: st and se two days apart.
SIGNED_QUERY = "sv=2023-11-03&st=2026-09-26T20%3A06Z&se=2026-09-28T20%3A06Z&sig=notarealsignature"

FIXTURE_FOLDER = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    """Return the bytes of one recorded fixture."""
    return (FIXTURE_FOLDER / name).read_bytes()


def fixture_json(name: str) -> Any:
    """Return the parsed JSON of one recorded fixture."""
    return json.loads(fixture_bytes(name))


def meeting_payload(meeting_id: int, date_time: str, *extra: dict[str, Any]) -> dict[str, Any]:
    """Build one meeting list entry with documents, for hand-written cases."""
    return {
        "id": meeting_id,
        "dateTime": date_time,
        "title": f"Test meeting {meeting_id}",
        "videoUrl": None,
        "documentList": list(extra),
    }


class FakePortal:
    """A fake PrimeGov portal that records every request it answers."""

    def __init__(self) -> None:
        self.base_url = BASE_URL
        self.requests: list[httpx.Request] = []
        self.archived_by_year: dict[int, Any] = {2026: fixture_json("archived-meetings-2026.json")}
        self.upcoming: Any = fixture_json("upcoming-meetings.json")
        self.archived_status = 200
        self.document_bytes = fixture_bytes("fake-agenda.pdf")
        self.document_content_type = "application/pdf"
        #: How many redirects the compiled document download makes first.
        self.redirect_hops = 1
        #: Set to lie about the body length, to prove the streaming check.
        self.declared_length: str | None = None
        #: Set to serve a body larger than the limit.
        self.document_chunks: list[bytes] | None = None

    # -- the pieces a test asks for ------------------------------------------

    def adapter(self, **kwargs: Any) -> PrimeGovAdapter:
        """A PrimeGovAdapter whose client is the fake portal."""
        return PrimeGovAdapter(self.base_url, self.client(), **kwargs)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def signed_url(self, hop: int = 1) -> str:
        return f"{SIGNED_HOST}/compiled/18613_16805.pdf?{SIGNED_QUERY}&hop={hop}"

    def paths(self) -> list[str]:
        """The paths the adapter asked for, in order."""
        return [request.url.path for request in self.requests]

    # -- the transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "pgwest.blob.core.windows.net":
            return self._signed_hop(int(url.params.get("hop") or "1"))
        if url.path == ARCHIVED_MEETINGS_PATH:
            if self.archived_status != 200:
                return httpx.Response(self.archived_status, text="no")
            year = int(url.params.get("year") or "0")
            return httpx.Response(200, json=self.archived_by_year.get(year, []))
        if url.path == UPCOMING_MEETINGS_PATH:
            return httpx.Response(200, json=self.upcoming)
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
        if self.declared_length is not None:
            headers["content-length"] = self.declared_length
        if self.document_chunks is None:
            return httpx.Response(200, content=self.document_bytes, headers=headers)
        return httpx.Response(200, content=iter(self.document_chunks), headers=headers)


@pytest.fixture
def portal() -> FakePortal:
    """A fake PrimeGov portal on a test host."""
    return FakePortal()


@pytest.fixture
def agenda_16805() -> bytes:
    """The September 8, 2026 City Council regular session HTML agenda."""
    return fixture_bytes("agenda-16805.html")


@pytest.fixture
def agenda_16095() -> bytes:
    """The June 2, 2026 City Council study session HTML agenda."""
    return fixture_bytes("agenda-16095.html")
