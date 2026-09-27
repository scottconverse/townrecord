"""get_document: the redirect, the size limits, and what is never stored."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from townrecord.adapters.base import (
    PACKET_LIMIT_BYTES,
    PDF_LIMIT_BYTES,
    AdapterError,
    Document,
    DocumentTooLarge,
    RedirectLimitExceeded,
)
from townrecord.adapters.primegov import COMPILED_DOCUMENT_PATH

from .conftest import SIGNED_HOST, FakePortal

AGENDA = Document(
    id=18613, template_id=16805, compile_output_type=1, template_name="Agenda", meeting_id=3709
)
PACKET = Document(
    id=18658, template_id=16807, compile_output_type=1, template_name="Packet", meeting_id=3709
)


def test_the_document_is_returned_with_its_hash(portal: FakePortal) -> None:
    result = portal.adapter().get_document(AGENDA)

    assert result.content == portal.document_bytes
    assert result.size == len(portal.document_bytes)
    assert result.sha256 == hashlib.sha256(portal.document_bytes).hexdigest()
    assert result.content_type == "application/pdf"


def test_the_redirect_is_followed_and_the_signed_link_is_not_stored(portal: FakePortal) -> None:
    result = portal.adapter().get_document(AGENDA)

    assert portal.paths() == [COMPILED_DOCUMENT_PATH, "/compiled/18613_16805.pdf"]
    assert SIGNED_HOST in portal.signed_url(), "the fake portal does use a signed link"
    assert result.source_url == (
        f"{portal.base_url}{COMPILED_DOCUMENT_PATH}?meetingTemplateId=16805&compileOutputType=1"
    )
    assert result.citation_url == (
        f"{portal.base_url}/Portal/Meeting?meetingId=3709&meetingTemplateId=16805"
    )
    for value in (result.citation_url, result.source_url, repr(result)):
        assert "pgwest" not in value


def test_the_adapter_keeps_no_signed_link(portal: FakePortal) -> None:
    adapter = portal.adapter()
    adapter.get_document(AGENDA)

    assert not any("pgwest" in str(value) for value in vars(adapter).values())


def test_four_redirects_are_allowed(portal: FakePortal) -> None:
    portal.redirect_hops = 4

    result = portal.adapter().get_document(AGENDA)

    assert result.size == len(portal.document_bytes)
    assert len(portal.requests) == 5


def test_a_fifth_redirect_is_refused(portal: FakePortal) -> None:
    portal.redirect_hops = 5

    with pytest.raises(RedirectLimitExceeded):
        portal.adapter().get_document(AGENDA)


def test_a_declared_length_over_the_limit_is_refused_before_the_body(portal: FakePortal) -> None:
    portal.declared_length = str(4096)

    with pytest.raises(DocumentTooLarge) as caught:
        portal.adapter(pdf_limit_bytes=1024).get_document(AGENDA)

    assert "4096" in str(caught.value) and "1024" in str(caught.value)


def test_a_body_over_the_limit_is_refused_while_streaming(portal: FakePortal) -> None:
    """The declared length lies. The streaming check must still catch it."""
    portal.declared_length = "100"
    portal.document_chunks = [b"x" * 2048, b"y" * 2048]

    with pytest.raises(DocumentTooLarge) as caught:
        portal.adapter(pdf_limit_bytes=1024).get_document(AGENDA)

    assert "while streaming" in str(caught.value)


def test_a_packet_gets_its_own_higher_limit(portal: FakePortal) -> None:
    adapter = portal.adapter()

    assert adapter.limit_for(AGENDA) == PDF_LIMIT_BYTES
    assert adapter.limit_for(PACKET) == PACKET_LIMIT_BYTES
    assert PACKET_LIMIT_BYTES > 170 * 1024 * 1024, "spec 9.6 packets run to 170 MB"


def test_a_packet_body_over_its_own_limit_is_refused(portal: FakePortal) -> None:
    portal.document_chunks = [b"x" * 2048, b"y" * 2048]

    with pytest.raises(DocumentTooLarge):
        portal.adapter(packet_limit_bytes=1024).get_document(PACKET)


def test_only_compiled_documents_are_fetched(portal: FakePortal) -> None:
    html_agenda = Document(
        id=18657,
        template_id=16805,
        compile_output_type=3,
        template_name="HTML Agenda",
        meeting_id=3709,
    )

    with pytest.raises(AdapterError) as caught:
        portal.adapter().get_document(html_agenda)

    assert "compileOutputType 3" in str(caught.value)


def test_a_document_without_its_meeting_id_cannot_be_cited(portal: FakePortal) -> None:
    orphan = Document(id=1, template_id=16805, compile_output_type=1, template_name="Agenda")

    with pytest.raises(AdapterError) as caught:
        portal.adapter().get_document(orphan)

    assert "meeting id" in str(caught.value)


def test_a_redirect_without_a_location_is_an_error(portal: FakePortal) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    adapter = portal.adapter()
    adapter.http = httpx.Client(transport=httpx.MockTransport(handle))

    with pytest.raises(AdapterError) as caught:
        adapter.get_document(AGENDA)

    assert "without a location" in str(caught.value)
