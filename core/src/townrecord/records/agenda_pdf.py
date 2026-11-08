"""The items of an agenda that is published only as a PDF (spec 10.2).

Some meetings have no HTML agenda: on November 3, 2026 the Longmont City
Council's Regular Session published an agenda PDF and a packet and nothing
else. The aligner still needs the items, so they are read from the text of the
agenda's pages, which the page reader already stored.

The layout read here is the one the portal compiles. Each number is a line of
its own ("9.", "A." or "A.1."), and the title follows it:

- a numbered item's title is in capitals and can run over more than one line;
  the paragraph that follows it is in ordinary case and is not part of it;
- a lettered item's title runs until the next number, a blank line or the end;
- each page ends with the meeting's name and date and then "Page 3";
- the "Appendix of Informational Items" numbers its items again from 1.

Numbers come out the way the HTML agenda's do, so the two can be compared: a
numbered item keeps its period ("9."), a lettered one is written under its
number ("9.A", "10.A.1"), and an appendix item is its number alone ("1"). A PDF carries
no clerk video times, so every item's time is missing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..adapters.base import VIDEO_TIME_MISSING, AgendaItem

#: A numbered item's number on a line of its own.
_NUMBER = re.compile(r"^(\d{1,3})\.$")

#: A lettered item's letter on a line of its own.
_LETTER = re.compile(r"^([A-Za-z])\.$")

#: An item under a lettered item ("I.1."), on a line of its own.
_SUB_LETTER = re.compile(r"^([A-Za-z])\.(\d{1,2})\.$")

#: The ending of an ordinal the PDF sets apart as a superscript ("1" then "st").
_ORDINAL_ENDING = re.compile(r"^(?:st|nd|rd|th)$")

#: The page number at the foot of a page.
_PAGE_FOOTER = re.compile(r"^Page\s+\d+$", re.IGNORECASE)

#: The heading of the appendix, after which the items are numbered again.
_APPENDIX = re.compile(r"^Appendix\b", re.IGNORECASE)


def parse_pdf_agenda(pages: Sequence[str]) -> list[AgendaItem]:
    """Return the items of an agenda from the text of its pages, in order."""
    lines = _body_lines(pages)
    items: list[AgendaItem] = []
    section: str | None = None
    in_appendix = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _APPENDIX.match(line):
            in_appendix = True
            section = None
            index += 1
            continue
        number = _NUMBER.match(line)
        letter = _LETTER.match(line)
        sub_letter = _SUB_LETTER.match(line)
        if number and not in_appendix:
            section = number.group(1)
            title, index = _capital_title(lines, index + 1)
            items.append(_item(f"{section}.", title))
            continue
        label = None
        if number:
            label = number.group(1)
        elif letter and section is not None:
            label = f"{section}.{letter.group(1)}"
        elif sub_letter and section is not None:
            label = f"{section}.{sub_letter.group(1)}.{sub_letter.group(2)}"
        if label is not None:
            title, index = _running_title(lines, index + 1)
            items.append(_item(label, title))
            continue
        index += 1
    return [item for item in items if item.title]


def _body_lines(pages: Sequence[str]) -> list[str]:
    """Every line of every page with the page footers taken off.

    A blank line is kept as an empty string, because it ends a title. A line
    holding only a no-break space is blank too: that is how the portal leaves a
    gap between two items.
    """
    lines: list[str] = []
    for page in pages:
        page_lines = [_clean(line) for line in page.replace("\r", "").split("\n")]
        while page_lines and not page_lines[-1]:
            page_lines.pop()
        if page_lines and _PAGE_FOOTER.match(page_lines[-1]):
            page_lines.pop()
            # The line above the page number is the meeting's name and date.
            if page_lines:
                page_lines.pop()
        lines.extend(page_lines)
    return lines


def _capital_title(lines: list[str], index: int) -> tuple[str, int]:
    """A numbered item's title: its first line and the capital lines after it."""
    if index >= len(lines) or _is_marker(lines[index]):
        return "", index
    parts = [lines[index]]
    index += 1
    if _in_capitals(parts[0]):
        while index < len(lines) and _in_capitals(lines[index]) and not _is_marker(lines[index]):
            parts.append(lines[index])
            index += 1
    return _join(parts), index


def _running_title(lines: list[str], index: int) -> tuple[str, int]:
    """A lettered item's title: every line until the next number or a blank line."""
    parts: list[str] = []
    while index < len(lines) and lines[index] and not _is_marker(lines[index]):
        if _APPENDIX.match(lines[index]):
            break
        parts.append(lines[index])
        index += 1
    return _join(parts), index


def _is_marker(line: str) -> bool:
    return bool(_NUMBER.match(line) or _LETTER.match(line) or _SUB_LETTER.match(line))


def _in_capitals(line: str) -> bool:
    """True for a line with letters in it and none of them in lower case."""
    return any(ch.isalpha() for ch in line) and line == line.upper()


def _clean(line: str) -> str:
    # str.split takes the no-break space for a space, as it should here.
    return " ".join(line.split())


def _join(parts: list[str]) -> str:
    """One title from its lines. A word the PDF broke over two lines in capitals
    ("BOAR" and "D") is joined back, and a comma that starts a line ("O-2026-74"
    then ", A Bill") is put back against the word before it, and so is the
    ending of an ordinal set as a superscript ("1" then "st")."""
    title = ""
    for part in parts:
        broken_word = _in_capitals(part) and len(part) <= 4 and part.isalpha()
        ordinal = bool(title) and title[-1].isdigit() and bool(_ORDINAL_ENDING.match(part))
        if title and (broken_word or ordinal):
            title += part
        elif title:
            title += " " + part
        else:
            title = part
    return re.sub(r"\s+,", ",", title).strip()


def _item(number: str, title: str) -> AgendaItem:
    return AgendaItem(
        number=number, title=title, video_seconds=None, video_time_status=VIDEO_TIME_MISSING
    )


__all__ = ["parse_pdf_agenda"]
