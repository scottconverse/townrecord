"""The reading job: one stored PDF's text, page by page (spec 9.4, 9.6).

A packet runs 60 to 170 MB and a hundred pages, so reading one is slow work of
its own and runs on the heavy lane, after the download that stored the bytes.
What it writes is one ``record_pages`` row per page, the text of that page, the
number the page prints in its own footer when it prints one, and the record's
``page_count``.

The footer is kept apart from the page number because they are not the same
number everywhere (spec 9.4): on a Longmont agenda they happen to be equal, and
the checkpoint says how many pages they agreed on, so a document where they do
not agree is visible rather than silent.

A page with no text layer is a scan. OCR is a later unit, so the page is
stored with the plain reason it has no text (spec 9.6) and its row is what a
later unit reads to find the work.

Reading is idempotent: the pages of the record are written fresh rather than
edited, so a second reading of the same bytes writes the same rows.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from ..artifacts import get as get_artifact
from ..jobs import JobContext
from ..repo import (
    Record,
    delete_record_pages,
    get_record,
    insert_record_page,
    record_pages,
    set_record_page_count,
)
from . import portal

#: The plain reason a page that carries no text is stored with (spec 9.6).
#: Reading a scan is OCR, which is a later unit, and this is the sentence that
#: names the page for it.
NEEDS_OCR = "The page has no text layer, so it needs OCR."

#: The footer a document prints at the foot of a page ("Page 3"). Spec 9.4:
#: on a Longmont agenda this number is the page's number in the file, and the
#: checkpoint reports how often that held.
_PAGE_FOOTER = re.compile(r"^Page\s+(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


class PagesRefused(Exception):
    """The stored bytes cannot be read, with a plain reason a person can act on."""


def extract_pages(ctx: JobContext) -> None:
    """Read one stored record page by page and store what each page says.

    A record whose file is not a PDF, or is not there, pauses: nothing is
    wrong with the bytes, there is simply nothing to read. Bytes that are
    there and cannot be opened as a PDF fail the job with the reason, because a
    stored file that will not open is a fact about the file and not a wait.
    """
    job = portal.page_job(ctx.payload)
    record = get_record(ctx.conn, job.record_id)
    if record is None:
        ctx.pause(f"There is no record {job.record_id} to read.")
        return

    artifact = get_artifact(ctx.conn, record.artifact_id, portal.settings().storage_root)
    if artifact is None:
        ctx.pause(
            f"The file of record {record.id} is not in the artifact store, so its pages "
            "cannot be read."
        )
        return
    suffix = artifact.path.suffix.casefold().lstrip(".")
    if suffix != portal.PDF_EXTENSION:
        ctx.pause(
            f"Record {record.id} is {artifact.rel_path}, which is not a PDF, so it has no "
            "pages to read."
        )
        return
    try:
        content = artifact.path.read_bytes()
    except OSError as exc:
        ctx.pause(f"The file of record {record.id} could not be opened: {exc.strerror or exc}.")
        return

    pages = _read(content, record)
    replaced = delete_record_pages(ctx.conn, record.id)
    for number, text in pages:
        insert_record_page(
            ctx.conn,
            record_id=record.id,
            page_number=number,
            text=text,
            footer_page_number=_footer_number(text),
            ocr_reason=None if text else NEEDS_OCR,
        )
    set_record_page_count(ctx.conn, record.id, len(pages))
    written = record_pages(ctx.conn, record.id)
    ctx.save_checkpoint(
        {
            "record_id": record.id,
            "artifact_id": artifact.id,
            "sha256": artifact.sha256,
            "pages": len(written),
            "replaced_pages": replaced,
            "pages_with_text": sum(1 for page in written if page.text),
            "footers_found": sum(1 for page in written if page.footer_page_number is not None),
            "footers_matching": sum(
                1
                for page in written
                if page.footer_page_number is not None
                and page.footer_page_number == page.page_number
            ),
            "needing_ocr": sum(1 for page in written if page.ocr_reason is not None),
        }
    )


def _read(content: bytes, record: Record) -> list[tuple[int, str]]:
    """Read every page of the bytes, with its number and its text.

    The number is the page's place in the file, counted from one. A page whose
    text cannot be read at all fails the whole reading with the page named: a
    record half read would be a page count nobody can trust.
    """
    try:
        reader = PdfReader(io.BytesIO(content))
        loaded = list(reader.pages)
    except Exception as exc:  # pypdf raises its own types and the reader's
        raise PagesRefused(
            f"The bytes of record {record.id} could not be read as a PDF: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    pages: list[tuple[int, str]] = []
    for index, page in enumerate(loaded, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise PagesRefused(
                f"Page {index} of record {record.id} could not be read: {type(exc).__name__}: {exc}"
            ) from exc
        pages.append((index, text))
    return pages


def _footer_number(text: str) -> int | None:
    """The number the page prints in its footer, or None when it prints none.

    The last line that is nothing but "Page N" is the footer: a page of a
    record can quote the phrase in its own body, and the footer is what comes
    after that.
    """
    found = _PAGE_FOOTER.findall(text)
    return int(found[-1]) if found else None


__all__ = ["NEEDS_OCR", "PagesRefused", "extract_pages"]
