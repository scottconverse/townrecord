"""parse_html_agenda: item numbers, the clerk's video times, and decoding."""

from __future__ import annotations

import pytest

from townrecord.adapters.base import VIDEO_TIME_MISSING, VIDEO_TIME_OUTSIDE_VIDEO, VIDEO_TIME_USABLE
from townrecord.adapters.primegov import decode_html, detect_encoding

from .conftest import FakePortal

#: The measured length of video 3qfQAkAAC9U, from its info.json (2026-09-27).
VIDEO_DURATION_S = 14407

#: How many data-videolocation values on the September 8, 2026 agenda are
#: longer than that video: 585015 once, 585021 twelve times, 658102 and 658993
#: once each, plus the two items that genuinely run past the end of the video.
PLACEHOLDER_COUNT = 16


def parse(portal: FakePortal, agenda: bytes, duration: int | None = VIDEO_DURATION_S):
    return portal.adapter().parse_html_agenda(agenda, duration)


def test_the_encoding_of_the_agenda_is_utf8(agenda_16805: bytes) -> None:
    """Byte evidence: the file is valid UTF-8 and declares UTF-8 in a meta tag."""
    assert detect_encoding(agenda_16805) == "utf-8"
    text = decode_html(agenda_16805)

    assert "City Clerk’s Office" in text, "the clerk's apostrophe survives"
    assert "�" not in text, "nothing is replaced or dropped"
    assert agenda_16805.count(b"\xef\xbf\xbd") == 0


def test_an_undeclared_body_that_is_not_utf8_falls_back_to_cp1252() -> None:
    assert detect_encoding(b"<p>caf\x92s</p>") == "cp1252"
    assert decode_html(b"<p>caf\x92s</p>") == "<p>caf’s</p>"


def test_a_declared_charset_wins(agenda_16095: bytes) -> None:
    assert detect_encoding(agenda_16095, "text/html; charset=UTF-8") == "utf-8"
    assert detect_encoding(b"\xef\xbb\xbf<p>x</p>") == "utf-8-sig"


def test_items_carry_their_numbers_and_sections(portal: FakePortal, agenda_16805: bytes) -> None:
    items = parse(portal, agenda_16805)
    by_number = {item.number: item for item in items}

    assert by_number["1."].title == "MEETING CALLED TO ORDER"
    assert by_number["1."].video_seconds == 466
    assert by_number["9."].title.startswith("CONSENT AGENDA")
    assert by_number["4.A"].title == "August 25, 2026 – Regular Session", "entities decode"
    assert by_number["9.B"].title.startswith("O-2026-58")
    assert by_number["10.A.2"].title.startswith("O-2026-55")
    assert by_number["12.B"].title == "2027 Proposed Budget Presentation"


def test_placeholders_are_outside_the_video(portal: FakePortal, agenda_16805: bytes) -> None:
    """Spec 10.2: a value longer than the video is not usable."""
    items = parse(portal, agenda_16805)
    by_number = {item.number: item for item in items}

    assert by_number["9."].video_seconds == 585015
    assert by_number["9."].video_time_status == VIDEO_TIME_OUTSIDE_VIDEO
    assert by_number["9.A"].video_seconds == 585021
    assert by_number["9.A"].video_time_status == VIDEO_TIME_OUTSIDE_VIDEO
    assert by_number["14."].video_seconds == 14619, "the video ends at 14,407 seconds"
    assert by_number["14."].video_time_status == VIDEO_TIME_OUTSIDE_VIDEO
    assert by_number["13."].video_seconds == 14105
    assert by_number["13."].video_time_status == VIDEO_TIME_USABLE
    assert [
        item.number for item in items if item.video_time_status == VIDEO_TIME_OUTSIDE_VIDEO
    ] != []
    assert sum(1 for i in items if i.video_time_status == VIDEO_TIME_OUTSIDE_VIDEO) == (
        PLACEHOLDER_COUNT
    )


def test_an_item_without_a_time_says_so(portal: FakePortal, agenda_16805: bytes) -> None:
    items = parse(portal, agenda_16805)
    by_number = {item.number: item for item in items}

    assert by_number["2."].video_seconds is None
    assert by_number["2."].video_time_status == VIDEO_TIME_MISSING
    assert by_number["4."].video_time_status == VIDEO_TIME_MISSING, "the section has no value"
    assert by_number["4.A"].video_time_status == VIDEO_TIME_USABLE


def test_without_a_video_length_a_present_time_is_reported_usable(
    portal: FakePortal, agenda_16805: bytes
) -> None:
    """The length check needs the video. Without it, nothing is claimed."""
    items = parse(portal, agenda_16805, None)
    by_number = {item.number: item for item in items}

    assert by_number["9."].video_seconds == 585015
    assert by_number["9."].video_time_status == VIDEO_TIME_USABLE
    assert by_number["2."].video_time_status == VIDEO_TIME_MISSING


def test_the_study_session_agenda_has_no_item_times(
    portal: FakePortal, agenda_16095: bytes
) -> None:
    """Appendix A: the June 2, 2026 agenda has no item times."""
    items = parse(portal, agenda_16095)

    assert items, "the agenda still has items"
    assert [item for item in items if item.video_seconds is not None] == []
    assert {item.video_time_status for item in items} == {VIDEO_TIME_MISSING}
    assert b'data-videolocation="' not in agenda_16095, (
        "the two occurrences in this file are JavaScript, not item attributes"
    )


def test_titles_are_plain_text(portal: FakePortal, agenda_16805: bytes) -> None:
    items = parse(portal, agenda_16805)

    assert all(item.title == item.title.strip() for item in items)
    assert all("\n" not in item.title and "\xa0" not in item.title for item in items)
    assert all("<" not in item.title for item in items)
    assert all(item.number for item in items)


@pytest.mark.parametrize(
    ("number", "status"),
    [("9.B", VIDEO_TIME_OUTSIDE_VIDEO), ("11.A", VIDEO_TIME_USABLE), ("7.", VIDEO_TIME_MISSING)],
)
def test_a_sample_of_statuses(
    portal: FakePortal, agenda_16805: bytes, number: str, status: str
) -> None:
    items = {item.number: item for item in parse(portal, agenda_16805)}

    assert items[number].video_time_status == status
