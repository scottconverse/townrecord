"""Reading an agenda's items from its PDF (spec 10.2).

A meeting with no HTML agenda is aligned against the items of its agenda PDF.
The strongest check is a meeting that has both: on September 8, 2026 the
portal published the HTML agenda and the PDF compiled from it, and the PDF
must give the same items, numbers and titles as the HTML. The November 3,
2026 Regular Session is the meeting that needs it: its agenda is a PDF and
nothing else.
"""

from __future__ import annotations

import io

import httpx
from pypdf import PdfReader

from townrecord.adapters.base import VIDEO_TIME_MISSING
from townrecord.adapters.primegov import PrimeGovAdapter
from townrecord.records.agenda_pdf import parse_pdf_agenda

from .conftest import AGENDA_16805, AGENDA_16805_PDF, AGENDA_16858_PDF, needs


def pages_of(pdf: bytes) -> list[str]:
    """Each page's text, read the way the page reader stores it."""
    return [(page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(pdf)).pages]


# -- The recorded agendas -----------------------------------------------------


@needs(AGENDA_16805, AGENDA_16805_PDF)
def test_the_pdf_gives_the_same_items_as_the_html_agenda() -> None:
    """September 8, 2026: forty items, in the same order, with the same titles."""
    with httpx.Client() as client:
        html = PrimeGovAdapter("https://portal.invalid", client).parse_html_agenda(
            AGENDA_16805.read_bytes()
        )
    pdf = parse_pdf_agenda(pages_of(AGENDA_16805_PDF.read_bytes()))

    assert len(html) == 40
    assert [item.number for item in pdf] == [item.number for item in html]
    assert [item.title for item in pdf] == [" ".join(item.title.split()) for item in html]


@needs(AGENDA_16858_PDF)
def test_the_november_3_agenda_is_read_from_its_pdf() -> None:
    """The Regular Session that published no HTML agenda: 34 items."""
    items = parse_pdf_agenda(pages_of(AGENDA_16858_PDF.read_bytes()))
    titles = {item.number: item.title for item in items}

    assert len(items) == 34
    assert [item.number for item in items][:5] == ["1.", "2.", "3.", "4.", "4.A"]
    assert titles["1."] == "MEETING CALLED TO ORDER", "the paragraph under it is not the title"
    assert titles["9."] == (
        "CONSENT AGENDA AND INTRODUCTION AND READING BY TITLE OF FIRST READING ORDINANCES"
    )
    assert titles["9.A"] == (
        "O-2026-77, A Bill For An Ordinance Making Additional Appropriations For Expenses "
        "And Liabilities Of The City Of Longmont For The Fiscal Year Beginning January 1, 2026"
    )
    assert titles["10.B"].startswith("O-2026-74, A Bill For An Ordinance Conditionally")
    # 10.D runs over a page break, past the page's footer, and stops at the gap.
    assert titles["10.D"].endswith(
        "Section 22, Township 3 North, Range 68 West) Comprehensive Land Use Plan Amendment"
    )
    assert titles["17."] == "ADJOURN", "the contingency fund line under it is not the title"
    assert [item.number for item in items][-2:] == ["1", "2"], "the appendix starts again"
    assert titles["1"] == "City Council Calendar Of Upcoming Meetings And Events"
    assert not any("Page" in title or "November 03" in title for title in titles.values())
    assert all(item.video_seconds is None for item in items)
    assert all(item.video_time_status == VIDEO_TIME_MISSING for item in items)


# -- The layout, one rule at a time -------------------------------------------


def test_the_footer_of_each_page_is_left_out() -> None:
    """The meeting's name and date and the page number end every page."""
    pages = [
        "AGENDA\n1.\nROLL CALL\n2.\nGENERAL BUSINESS\nA.\nA Long Title That Runs\n"
        "Water Board, October 19, 2026\nPage 1",
        "Onto The Next Page\n3.\nADJOURN\nWater Board, October 19, 2026\nPage 2",
    ]

    items = parse_pdf_agenda(pages)

    assert [(item.number, item.title) for item in items] == [
        ("1.", "ROLL CALL"),
        ("2.", "GENERAL BUSINESS"),
        ("2.A", "A Long Title That Runs Onto The Next Page"),
        ("3.", "ADJOURN"),
    ]


def test_a_numbered_title_in_capitals_ends_where_ordinary_case_starts() -> None:
    pages = [
        "1.\nLAND ACKNOWLEDGMENT\nSTATEMENT\nWe acknowledge that Longmont sits on\n"
        "the traditional territory.\n2.\nADJOURN"
    ]

    items = parse_pdf_agenda(pages)

    assert [item.title for item in items] == ["LAND ACKNOWLEDGMENT STATEMENT", "ADJOURN"]


def test_a_numbered_title_in_ordinary_case_is_its_first_line() -> None:
    """The Executive Session agenda writes its items in ordinary case."""
    pages = [
        "1.\nRoll Call\n2.\nAdjourn\nTo view upcoming meeting dates and agendas, please visit:\n"
        "City Council Executive Session, November 03, 2026\nPage 1"
    ]

    assert [item.title for item in parse_pdf_agenda(pages)] == ["Roll Call", "Adjourn"]


def test_a_blank_line_ends_a_lettered_title() -> None:
    """The portal leaves a line holding only a no-break space between items."""
    pages = ["1.\nGENERAL BUSINESS\nA.\nFirst Line\nSecond Line\n \nNot part of it"]

    assert parse_pdf_agenda(pages)[-1].title == "First Line Second Line"


def test_a_word_broken_over_two_lines_is_joined_back() -> None:
    """Measured: "ITEMS FROM BOAR" then "D", and "ADJOURN" then "MENT"."""
    pages = ["1.\nITEMS FROM BOAR\nD\n2.\nADJOURN\nMENT"]

    assert [item.title for item in parse_pdf_agenda(pages)] == ["ITEMS FROM BOARD", "ADJOURNMENT"]


def test_a_comma_that_starts_a_line_goes_back_against_the_number() -> None:
    pages = ["10.\nORDINANCES\nB.\nO-2026-74\n, A Bill For An Ordinance"]

    assert parse_pdf_agenda(pages)[-1].title == "O-2026-74, A Bill For An Ordinance"


def test_an_ordinal_set_as_a_superscript_is_joined_back() -> None:
    pages = ["10.\nORDINANCES\nA.\n1\nst\n And Main Transit Project"]

    assert parse_pdf_agenda(pages)[-1].title == "1st And Main Transit Project"


def test_an_item_under_a_lettered_item_is_numbered_under_both() -> None:
    pages = ["9.\nCONSENT AGENDA\nI.\nFour Resolutions\nI.1.\nR-2026-60\n, A Resolution"]

    items = parse_pdf_agenda(pages)

    assert [(item.number, item.title) for item in items] == [
        ("9.", "CONSENT AGENDA"),
        ("9.I", "Four Resolutions"),
        ("9.I.1", "R-2026-60, A Resolution"),
    ]


def test_text_before_the_first_number_is_not_an_item() -> None:
    """The heading, the date and the special assistance note come first."""
    pages = ["AGENDA\nCity Council Regular Session\nA.\nnot an item\n1.\nROLL CALL"]

    assert [item.number for item in parse_pdf_agenda(pages)] == ["1."]


def test_a_document_with_no_numbered_items_gives_none() -> None:
    """The special meeting notice of October 6, 2026 is a letter, not a list."""
    pages = ["NOTICE OF SPECIAL PLANNING & ZONING COMMISSION MEETING\nCivic Center\nPage 1"]

    assert parse_pdf_agenda(pages) == []
