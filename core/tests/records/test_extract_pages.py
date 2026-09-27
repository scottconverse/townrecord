"""Reading a stored PDF page by page (spec 9.4, 9.6).

Fetching a document and reading it are two jobs, both on the heavy lane: a
packet runs 60 to 170 MB, and opening one is slow work of its own. What the
reading job writes is one row per page, the text of that page, and the number
the page prints in its own footer when it prints one. Spec 9.4 says that on a
Longmont agenda the printed number is the page's number in the file, which is
what the last test here checks against the recorded agenda.

A page with no text at all is a scan, and a scan needs OCR, which is a later
unit: the page is recorded with the plain reason rather than dropped.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from pypdf import PdfWriter

from townrecord.jobs import DONE, FAILED, PAUSED, QUEUED, read_checkpoint
from townrecord.repo import (
    Record,
    get_record,
    record_pages,
    records_of_meeting,
    search,
)

from .conftest import (
    AGENDA_16805_PDF,
    Area,
    FakePortal,
    Sync,
    document,
    meeting,
    needs,
)

#: The document the tests fetch: the September 8, 2026 agenda of the fixture
#: portal (compileOutputType 1, template 16805).
AGENDA_ID = 18613
AGENDA_TEMPLATE_ID = 16805

#: A meeting that lists it.
A_MEETING = 4100

#: The pages of the recorded agenda: unit K2 measured five, every one of them
#: with text, every one with a "Page N" footer that equals its page number.
PAGES = 5


def an_agenda_meeting(sync: Sync, wired: FakePortal, area: Area) -> int:
    """Sync one meeting that lists the agenda, and return the meeting's row id."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(AGENDA_ID, AGENDA_TEMPLATE_ID, 1, "Agenda"),),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    row = sync.conn.execute("SELECT id FROM meetings").fetchone()
    assert row is not None
    return int(row["id"])


def its_record(sync: Sync, meeting_id: int) -> Record:
    """The one record the fixture listing stores."""
    records = records_of_meeting(sync.conn, meeting_id)
    assert len(records) == 1
    return records[0]


def the_reading_job(sync: Sync, record_id: int) -> int:
    """Queue the reading of one record and run the lane it belongs to."""
    job_id = sync.queue("extract_pages", {"record_id": record_id})
    sync.lane("heavy")
    return job_id


def a_pdf_with_a_blank_page() -> bytes:
    """A one page PDF with a page and nothing written on it."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def page_rows(sync: Sync, record_id: int) -> list[tuple[int, int | None, str]]:
    """Each page's number, footer number and text, for a comparison of runs."""
    return [
        (page.page_number, page.footer_page_number, page.text)
        for page in record_pages(sync.conn, record_id)
    ]


# -- Storing a PDF queues the reading of it ------------------------------------


def test_storing_a_pdf_queues_one_reading_job_on_the_heavy_lane(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The download stores bytes; the reading is the next job, not this one."""
    meeting_id = an_agenda_meeting(sync, wired, area)
    download = int(sync.jobs_of_kind("download_record")[0]["id"])
    assert sync.jobs_of_kind("extract_pages") == [], "nothing is queued before the download runs"

    assert sync.runner().run_once("heavy") == download

    queued = sync.jobs_of_kind("extract_pages")
    assert len(queued) == 1, "storing one PDF queues one reading of it"
    record = its_record(sync, meeting_id)
    assert json.loads(queued[0]["payload"]) == {"record_id": record.id}
    assert (queued[0]["kind"], queued[0]["lane"], queued[0]["state"]) == (
        "extract_pages",
        "heavy",
        QUEUED,
    )


def test_a_document_that_is_not_a_pdf_is_not_queued_for_reading(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """An HTML agenda has no pages, so nothing is queued to read it."""
    wired.document_content_type = "text/html"
    wired.document_bytes = b"<html><body>an agenda</body></html>"
    meeting_id = an_agenda_meeting(sync, wired, area)

    sync.lane("heavy")

    assert sync.jobs_of_kind("extract_pages") == []
    record = its_record(sync, meeting_id)
    assert record.page_count is None
    assert record_pages(sync.conn, record.id) == []


# -- The recorded agenda, page by page -----------------------------------------


@needs(AGENDA_16805_PDF)
def test_the_recorded_agenda_is_read_page_by_page(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.6: the pages, their text and the count, on the recorded bytes.

    The agenda of September 8, 2026 is five pages and every one of them has a
    text layer. Spec 9.4 says the "Page N" footer of a Longmont agenda is the
    page's number in the file, so the two are held equal here, on every page.
    """
    wired.document_bytes = AGENDA_16805_PDF.read_bytes()
    meeting_id = an_agenda_meeting(sync, wired, area)

    sync.lane("heavy")

    record = its_record(sync, meeting_id)
    job_id = int(sync.jobs_of_kind("extract_pages")[0]["id"])
    assert sync.job(job_id)["state"] == DONE

    pages = record_pages(sync.conn, record.id)
    assert len(pages) == PAGES
    assert [page.page_number for page in pages] == [1, 2, 3, 4, 5]
    assert all(page.text.strip() for page in pages), "every page has a text layer"
    assert [page.footer_page_number for page in pages] == [1, 2, 3, 4, 5]
    assert all(page.ocr_reason is None for page in pages)
    assert pages[0].text.startswith("AGENDA")
    assert "Kimbark" in pages[0].text, "the text is the page's own, not a placeholder"

    stored = get_record(sync.conn, record.id)
    assert stored is not None
    assert stored.page_count == PAGES

    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["record_id"] == record.id
    assert checkpoint["pages"] == PAGES
    assert checkpoint["pages_with_text"] == PAGES
    assert checkpoint["footers_found"] == PAGES
    assert checkpoint["footers_matching"] == PAGES
    assert checkpoint["needing_ocr"] == 0


@needs(AGENDA_16805_PDF)
def test_the_stored_pages_are_what_the_search_reads(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 12.3: a phrase of a page is found through the full text search."""
    wired.document_bytes = AGENDA_16805_PDF.read_bytes()
    meeting_id = an_agenda_meeting(sync, wired, area)
    sync.lane("heavy")
    record = its_record(sync, meeting_id)

    hits = [hit for hit in search(sync.conn, "Kimbark") if hit.scope == "record_page"]

    assert hits, "the page that names the address is found by it"
    assert hits[0].page_number == 1
    assert hits[0].footer_page_number == 1
    assert hits[0].citation.record_id == record.id


@needs(AGENDA_16805_PDF)
def test_reading_the_same_record_again_writes_the_same_rows(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A re-run of the reading writes one set of pages, not a second set."""
    wired.document_bytes = AGENDA_16805_PDF.read_bytes()
    meeting_id = an_agenda_meeting(sync, wired, area)
    sync.lane("heavy")
    record = its_record(sync, meeting_id)
    first = page_rows(sync, record.id)
    assert len(first) == PAGES

    job_id = the_reading_job(sync, record.id)

    assert sync.job(job_id)["state"] == DONE
    assert page_rows(sync, record.id) == first
    assert sync.conn.execute("SELECT COUNT(*) FROM record_pages").fetchone()[0] == PAGES
    stored = get_record(sync.conn, record.id)
    assert stored is not None
    assert stored.page_count == PAGES


# -- Pages that cannot be read, and pages with no text -------------------------


def test_a_page_with_no_text_is_recorded_as_needing_ocr(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A scan is a page with no text layer, and it says so rather than vanish."""
    wired.document_bytes = a_pdf_with_a_blank_page()
    meeting_id = an_agenda_meeting(sync, wired, area)
    sync.lane("heavy")

    record = its_record(sync, meeting_id)
    job_id = int(sync.jobs_of_kind("extract_pages")[0]["id"])
    assert sync.job(job_id)["state"] == DONE

    pages = record_pages(sync.conn, record.id)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].text.strip() == ""
    assert pages[0].footer_page_number is None
    assert pages[0].ocr_reason, "a page with no text says why it has none"
    assert "OCR" in pages[0].ocr_reason
    stored = get_record(sync.conn, record.id)
    assert stored is not None
    assert stored.page_count == 1, "a scan is a page, and it is counted"
    assert read_checkpoint(sync.conn, job_id)["needing_ocr"] == 1


def test_bytes_that_cannot_be_read_as_a_pdf_fail_with_their_reason(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The bytes are stored and then cannot be opened, and that is a failure.

    The stub the fake portal serves is 193 bytes with no cross reference table,
    which is what a truncated packet looks like: the download stored exactly
    what it was given, so what failed is the reading, and it says so.
    """
    meeting_id = an_agenda_meeting(sync, wired, area)
    sync.lane("heavy")

    record = its_record(sync, meeting_id)
    job_id = int(sync.jobs_of_kind("extract_pages")[0]["id"])
    row = sync.job(job_id)
    assert row["state"] == FAILED
    assert "could not be read as a PDF" in row["last_error"]

    assert record_pages(sync.conn, record.id) == []
    stored = get_record(sync.conn, record.id)
    assert stored is not None
    assert stored.page_count is None, "a file that did not open has no page count"


def test_reading_a_record_that_is_not_a_pdf_pauses_with_its_reason(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """A job asked to read something that is not a PDF says so, and stops."""
    wired.document_content_type = "text/html"
    wired.document_bytes = b"<html><body>an agenda</body></html>"
    meeting_id = an_agenda_meeting(sync, wired, area)
    sync.lane("heavy")

    job_id = the_reading_job(sync, its_record(sync, meeting_id).id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "not a PDF" in row["last_error"]


def test_reading_a_record_that_is_not_there_pauses(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A job whose record is gone says so rather than reading a missing row."""
    job_id = the_reading_job(sync, 999999)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == "There is no record 999999 to read."
