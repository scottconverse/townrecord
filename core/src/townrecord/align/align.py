"""Aligning a transcript with an agenda (spec 10.2).

Three ways an item gets a start time, and every boundary says which one
produced it:

* ``spoken_transition``. The chair says the item and the transcript records
  it. This is measured evidence and it wins whenever it exists.
* ``html_agenda_time``. The clerk published a video time for the item, the
  transcript gives a constant offset for the whole video, and the shifted
  time falls inside the video where the transcript mentions the item.
* ``none``. Nothing above held. The item keeps no boundary and carries a
  plain reason. A boundary is never invented.

The offset is the interesting part. The clerk's ``data-videolocation`` times
on the September 8, 2026 Longmont agenda (16805, video 3qfQAkAAC9U) run about
466 s ahead of the video clock, so the published times alone would put every
item minutes after it happened. One constant is measured per video instead of
trusting the published values: each item that has both a published time and a
spoken transition is an anchor, the difference between the two is a candidate
offset, and an offset is used only when at least ``offset_min_anchors``
anchors land within ``offset_tolerance_s`` of each other. One or two
agreements are not enough, and with no agreement the method gives nothing and
says why.

The result keeps the item of a moment in one place. :meth:`AlignmentResult.
item_for` answers what was on the table at a transcript time; the item is not
copied onto every segment (spec 10.2, last paragraph, and the TownReporter
lesson that a second item column came back empty on all 47,592 rows).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from townrecord.adapters.base import AgendaItem
from townrecord.align.identifiers import (
    Identifier,
    ItemReference,
    find_identifiers,
    find_item_references,
    significant_words,
    words_of,
)
from townrecord.captions.parse import Segment

__all__ = [
    "HTML_AGENDA_TIME",
    "NO_ALIGNMENT",
    "SPOKEN_TRANSITION",
    "AlignedItem",
    "AlignmentResult",
    "AlignmentSettings",
    "OffsetAnchor",
    "OffsetEstimate",
    "align_agenda_with_transcript",
]

#: The item's start came from the agenda's video time, shifted by the offset.
HTML_AGENDA_TIME = "html_agenda_time"

#: The item's start came from the transcript saying the item.
SPOKEN_TRANSITION = "spoken_transition"

#: No boundary was established. The item carries the reason.
NO_ALIGNMENT = "none"

#: How a spoken candidate was found, strongest first. Two candidates in one
#: segment are taken in this order for the same item, and the agenda order
#: settles which items are tried first.
_RULE_SPOKEN_IDENTIFIER = 0
_RULE_ITEM_NUMBER = 1
_RULE_WRITTEN_IDENTIFIER = 2
_RULE_TITLE = 3

#: One or more dots or spaces separate the parts of an agenda item number.
_NUMBER_SEPARATOR = re.compile(r"[.\s]+")


def _item_parts(number: str) -> tuple[str, ...]:
    """``10.A.1`` becomes ``("10", "A", "1")`` and ``1.`` becomes ``("1",)``."""
    parts = []
    for piece in _NUMBER_SEPARATOR.split(number.strip()):
        if not piece:
            continue
        parts.append(str(int(piece)) if piece.isdigit() else piece.upper())
    return tuple(parts)


@dataclass(frozen=True)
class AlignmentSettings:
    """The numbers the aligner uses, all of them changeable by a caller.

    ``offset_tolerance_s`` is how far apart two anchors may land and still
    count as agreeing, and ``offset_min_anchors`` is how many must agree
    before an offset is used at all. Both start at the values the September 8,
    2026 video was measured with: eight anchors spread over 456 s to 487 s, so
    30 s holds them together with room to spare, and three is the smallest
    number that is more than a coincidence.
    """

    offset_tolerance_s: float = 30.0
    offset_min_anchors: int = 3
    mention_window_s: float = 20.0
    mention_min_words: int = 1
    title_min_word_length: int = 4
    title_min_words: int = 2
    identifier_kind_window_words: int = 4


@dataclass(frozen=True)
class OffsetAnchor:
    """One item that has both a published time and a spoken transition."""

    item_number: str
    agenda_seconds: int
    spoken_ms: int
    difference_s: float
    matched_text: str


@dataclass(frozen=True)
class OffsetEstimate:
    """What the anchors said about the offset, whether or not one was taken."""

    offset_s: int | None
    accepted: bool
    tolerance_s: float
    min_anchors: int
    anchors: tuple[OffsetAnchor, ...]
    agreeing: tuple[OffsetAnchor, ...]
    reason: str


@dataclass(frozen=True)
class AlignedItem:
    """One agenda item and the boundary it got, with what the boundary rests on."""

    number: str
    title: str
    method: str
    start_ms: int | None = None
    end_ms: int | None = None
    evidence: str | None = None
    segment_start_ms: int | None = None
    offset_s: int | None = None
    agenda_seconds: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AlignmentResult:
    """The boundaries of one meeting, and how each of them was reached."""

    items: tuple[AlignedItem, ...]
    offset: OffsetEstimate
    video_duration_ms: int | None
    settings: AlignmentSettings
    spoken_reason: str | None = None
    html_reason: str | None = None
    dropped: tuple[str, ...] = ()

    def item_for(self, ms: int) -> str | None:
        """The agenda item number that was on the table at this transcript time.

        This is the only place the item of a moment is kept (spec 10.2). A
        caller reads it instead of storing an item on every segment.
        """
        for item in self.items:
            if item.start_ms is None or item.end_ms is None:
                continue
            if item.start_ms <= ms < item.end_ms:
                return item.number
        return None

    def counts_by_method(self) -> dict[str, int]:
        """How many items each method accounted for, ``none`` included."""
        counts = {SPOKEN_TRANSITION: 0, HTML_AGENDA_TIME: 0, NO_ALIGNMENT: 0}
        for item in self.items:
            counts[item.method] = counts.get(item.method, 0) + 1
        return counts

    def aligned_items(self) -> tuple[AlignedItem, ...]:
        """The items that got a boundary, in agenda order."""
        return tuple(item for item in self.items if item.start_ms is not None)

    def unaligned_items(self) -> tuple[AlignedItem, ...]:
        """The items that got no boundary, in agenda order."""
        return tuple(item for item in self.items if item.start_ms is None)


@dataclass
class _Item:
    """One agenda item with everything the matchers need, worked out once."""

    index: int
    number: str
    title: str
    parts: tuple[str, ...]
    title_words: tuple[str, ...]
    identifiers: dict[tuple[int, int], str | None]
    agenda_seconds: int | None
    video_time_status: str

    def matches_identifier(self, identifier: Identifier) -> bool:
        """Say whether a number read out of the transcript belongs to this item.

        The item's own title gives the kind when the transcript did not, so a
        bare ``2026-56`` matches ``O-2026-56``. When both give a kind they must
        agree, so a resolution number never stands in for an ordinance.
        """
        if identifier.key() not in self.identifiers:
            return False
        if identifier.kind is None:
            return True
        expected = self.identifiers[identifier.key()]
        return expected is None or expected == identifier.kind

    def title_is_spoken_in(self, segment_words: set[str], settings: AlignmentSettings) -> bool:
        """Spec 10.2: the title has two words over four letters and all appear."""
        if len(self.title_words) < settings.title_min_words:
            return False
        return all(word in segment_words for word in self.title_words)

    def mentioned_words(self, words: set[str], settings: AlignmentSettings) -> tuple[str, ...]:
        """The item's title words the transcript says near a published time.

        Looser than the verbatim title check on purpose. This one guards a
        time the clerk already published for this item, so its job is to catch
        a time that has drifted away from the item, not to identify the item:
        one distinctive title word in the window is enough, and a title of one
        long word such as ``Budget`` needs that one word anyway. Spec 10.2's
        stricter all-words check belongs to the spoken title rule, and stays
        strict there.
        """
        if not self.title_words:
            return ()
        hits = tuple(word for word in self.title_words if word in words)
        needed = min(settings.mention_min_words, len(self.title_words))
        return hits if len(hits) >= needed else ()


@dataclass(frozen=True)
class _Boundary:
    """A start found in the transcript, with the words that produced it."""

    item_index: int
    start_ms: int
    matched_text: str
    rule: int


def _build_item(index: int, item: AgendaItem) -> _Item:
    """Work out an agenda item's number parts, title words and identifiers once."""
    identifiers: dict[tuple[int, int], str | None] = {}
    for identifier in find_identifiers(item.title):
        identifiers.setdefault(identifier.key(), identifier.kind)
    return _Item(
        index=index,
        number=item.number,
        title=item.title,
        parts=_item_parts(item.number),
        title_words=significant_words(item.title),
        identifiers=identifiers,
        agenda_seconds=item.video_seconds,
        video_time_status=item.video_time_status,
    )


def _matches_reference(item: _Item, reference: ItemReference) -> bool:
    """Say whether a spoken item number names this item.

    ``("11",)`` names item ``11``. ``("A", "1")`` names ``10.A.1``, which is
    how the chair cites a sub-item without repeating the parent. A lone spoken
    number is tried against the item's whole number, so "item 9" names ``9.``
    and not ``9.A``. A lone spoken letter is different: the chair says "Item A"
    inside item 12 and means ``12.A``, so it is offered to every sub-item whose
    last part is that letter, and the chain of items in agenda order settles
    which one it was.
    """
    if len(reference.parts) == 1:
        part = reference.parts[0]
        if part.isdigit():
            return len(item.parts) == 1 and item.parts[0] == part
        return item.parts[-1] == part
    return reference.matches(item.parts)


def _candidates_in_segment(
    items: Sequence[_Item], segment: Segment, settings: AlignmentSettings
) -> list[_Boundary]:
    """Every item this segment names, taken in agenda order.

    Within one item the strongest evidence is kept, so the boundary reports
    the best reason it has.
    """
    text = segment.text
    segment_words = set(words_of(text))
    identifiers = find_identifiers(text, kind_window_words=settings.identifier_kind_window_words)
    references = find_item_references(text)
    found: list[_Boundary] = []
    for item in items:
        best: tuple[int, str] | None = None

        def take(rule: int, matched_text: str) -> None:
            nonlocal best
            if best is None or rule < best[0]:
                best = (rule, matched_text)

        for identifier in identifiers:
            if not item.matches_identifier(identifier):
                continue
            if identifier.is_written():
                # A written number alone is how a document gets cited, so spec
                # 10.2 asks for a word of the item's title beside it.
                if any(word in segment_words for word in item.title_words):
                    take(_RULE_WRITTEN_IDENTIFIER, identifier.text)
            else:
                take(_RULE_SPOKEN_IDENTIFIER, identifier.text)
        for reference in references:
            if _matches_reference(item, reference):
                take(_RULE_ITEM_NUMBER, reference.text)
        if best is None and item.title_is_spoken_in(segment_words, settings):
            best = (_RULE_TITLE, text)
        if best is not None:
            found.append(
                _Boundary(
                    item_index=item.index,
                    start_ms=segment.start_ms,
                    matched_text=best[1].strip(),
                    rule=best[0],
                )
            )
    return found


@dataclass(frozen=True)
class _ChainNode:
    """One candidate boundary as a link in a chain of items in order."""

    boundary: _Boundary
    length: int
    total_ms: int
    parent: int | None


def _longest_orderly_chain(candidates: dict[int, list[_Boundary]]) -> tuple[_Boundary, ...]:
    """The most items that can be placed in agenda order without going back in time.

    The chair works down the agenda, so the items that were really announced
    form a chain that moves forward in the agenda *and* forward in the video.
    A public speaker naming an ordinance during the first call, or a captioner
    writing "item 9" where the chair said "item 9D", breaks that chain. Walking
    the transcript and taking what comes first cannot tell those apart, so the
    longest chain is taken instead: it keeps the most items in order, and a
    stray mention has to outnumber the real ones before it wins.

    Length is the objective; among chains of the same length the one whose
    items start earliest is kept, which is the earliest reading of the agenda.
    """
    nodes: list[_ChainNode] = []
    for item_index in sorted(candidates):
        group_start = len(nodes)
        for boundary in sorted(candidates[item_index], key=lambda found: found.start_ms):
            best = _ChainNode(boundary=boundary, length=1, total_ms=boundary.start_ms, parent=None)
            for position in range(group_start):
                previous = nodes[position]
                if previous.boundary.start_ms > boundary.start_ms:
                    continue
                length = previous.length + 1
                total = previous.total_ms + boundary.start_ms
                if length > best.length or (length == best.length and total < best.total_ms):
                    best = _ChainNode(
                        boundary=boundary, length=length, total_ms=total, parent=position
                    )
            nodes.append(best)
    if not nodes:
        return ()
    end = max(range(len(nodes)), key=lambda node: (nodes[node].length, -nodes[node].total_ms))
    chain: list[_Boundary] = []
    position: int | None = end
    while position is not None:
        chain.append(nodes[position].boundary)
        position = nodes[position].parent
    return tuple(reversed(chain))


def _spoken_boundaries(
    items: Sequence[_Item],
    segments: Sequence[Segment],
    settings: AlignmentSettings,
) -> tuple[dict[int, _Boundary], tuple[str, ...]]:
    """Take the longest chain of spoken transitions that follows the agenda.

    Every candidate the transcript raises is either kept in that chain or
    reported as dropped, so the reason an item has no boundary can be traced
    back to the words that were passed over.
    """
    candidates: dict[int, list[_Boundary]] = {}
    for segment in segments:
        for candidate in _candidates_in_segment(items, segment, settings):
            candidates.setdefault(candidate.item_index, []).append(candidate)
    chain = _longest_orderly_chain(candidates)
    taken = {boundary.item_index: boundary for boundary in chain}

    dropped: list[str] = []
    for item_index in sorted(candidates):
        for candidate in sorted(candidates[item_index], key=lambda found: found.start_ms):
            if taken.get(item_index) == candidate:
                continue
            kept = taken.get(item_index)
            if kept is not None:
                dropped.append(
                    f"{_seconds(candidate.start_ms)}: '{candidate.matched_text}' names "
                    f"{items[item_index].number}, whose reading was kept at "
                    f"{_seconds(kept.start_ms)}"
                )
            else:
                dropped.append(
                    f"{_seconds(candidate.start_ms)}: '{candidate.matched_text}' names "
                    f"{items[item_index].number}, which could not be kept in order with "
                    f"the items around it"
                )
    return taken, tuple(dropped)


def _estimate_offset(
    items: Sequence[_Item], taken: dict[int, _Boundary], settings: AlignmentSettings
) -> OffsetEstimate:
    """Measure one constant offset from the items that have both kinds of time."""
    anchors = tuple(
        OffsetAnchor(
            item_number=items[index].number,
            agenda_seconds=items[index].agenda_seconds or 0,
            spoken_ms=boundary.start_ms,
            difference_s=(items[index].agenda_seconds or 0) - boundary.start_ms / 1000.0,
            matched_text=boundary.matched_text,
        )
        for index, boundary in sorted(taken.items())
        if items[index].agenda_seconds is not None
    )
    tolerance = settings.offset_tolerance_s
    if not anchors:
        return OffsetEstimate(
            offset_s=None,
            accepted=False,
            tolerance_s=tolerance,
            min_anchors=settings.offset_min_anchors,
            anchors=(),
            agreeing=(),
            reason=(
                "no spoken transition named an item that also carries an agenda time, "
                "so no offset could be measured"
            ),
        )

    differences = sorted(anchor.difference_s for anchor in anchors)
    best: tuple[float, ...] = ()
    for center in differences:
        cluster = tuple(value for value in differences if abs(value - center) <= tolerance)
        if len(cluster) > len(best):
            best = cluster
    agreeing = tuple(anchor for anchor in anchors if anchor.difference_s in best)
    if len(agreeing) < settings.offset_min_anchors:
        spread = ", ".join(f"{value:g}" for value in differences)
        return OffsetEstimate(
            offset_s=None,
            accepted=False,
            tolerance_s=tolerance,
            min_anchors=settings.offset_min_anchors,
            anchors=anchors,
            agreeing=agreeing,
            reason=(
                f"the {len(anchors)} measured difference(s) were {spread} s and only "
                f"{len(agreeing)} of them land within {tolerance:g} s of each other, "
                f"where {settings.offset_min_anchors} are needed"
            ),
        )
    # The middle of the agreeing differences, not the middle of the widest
    # pair: one anchor that sits at the edge of the tolerance must not drag
    # the whole video's offset with it.
    offset = round(statistics.median(best))
    return OffsetEstimate(
        offset_s=offset,
        accepted=True,
        tolerance_s=tolerance,
        min_anchors=settings.offset_min_anchors,
        anchors=anchors,
        agreeing=agreeing,
        reason=(
            f"{len(agreeing)} of {len(anchors)} anchors agree within {tolerance:g} s, "
            f"so the agenda's times run {offset} s ahead of the video "
            f"(the middle of the agreeing differences is {statistics.median(best):g} s)"
        ),
    )


def _window_text(segments: Sequence[Segment], center_ms: int, window_ms: float) -> str:
    """The transcript around one moment, within the window, as one string."""
    low = center_ms - window_ms
    high = center_ms + window_ms
    return " ".join(s.text for s in segments if s.end_ms >= low and s.start_ms <= high)


def _seconds(ms: int) -> str:
    """A transcript time as ``1234.5 s``, for a reason a person reads."""
    return f"{ms / 1000:.1f} s"


def _contradicts_transcript(
    item: _Item,
    start_ms: int,
    offset_s: int,
    spoken_starts: dict[int, int],
    items: Sequence[_Item],
) -> str:
    """Say whether a published time argues with the transcript's own transitions.

    A published time is a weaker witness than words the chair actually said, so
    when the two disagree the published time is refused and the item keeps no
    boundary. The September 8, 2026 video has one case: item 10's published
    time shifts to 3974.0 s, but the chair had already reached item 10.A at
    3972.4 s, and the agenda places 10.A after item 10. Placing item 10 after
    its own sub-item would be a boundary the transcript contradicts.
    """
    earlier = [index for index in spoken_starts if index < item.index]
    later = [index for index in spoken_starts if index > item.index]
    if earlier:
        previous = max(earlier)
        if start_ms < spoken_starts[previous]:
            return (
                f"its agenda time of {item.agenda_seconds} s, shifted by {offset_s} s to "
                f"{_seconds(start_ms)}, would start it before {items[previous].number}'s "
                f"spoken transition at {_seconds(spoken_starts[previous])}, which the "
                f"transcript reaches first"
            )
    if later:
        following = min(later)
        if start_ms > spoken_starts[following]:
            return (
                f"its agenda time of {item.agenda_seconds} s, shifted by {offset_s} s to "
                f"{_seconds(start_ms)}, would start it after {items[following].number}'s "
                f"spoken transition at {_seconds(spoken_starts[following])}, even though "
                f"the agenda places {items[following].number} after it"
            )
    return ""


def _method_one_boundary(
    item: _Item,
    offset: OffsetEstimate,
    segments: Sequence[Segment],
    video_end_ms: int,
    settings: AlignmentSettings,
) -> tuple[int | None, str, str]:
    """The item's start from its agenda time, or nothing, why not, and the evidence.

    The agenda time is used only when it is inside the video and the
    transcript near the shifted time mentions the item (spec 10.2). A
    placeholder time such as 585021 s on a 14407 s video fails the first test
    and is never shifted into an answer.
    """
    if item.agenda_seconds is None:
        return None, "the agenda carries no video time for it", ""
    if item.agenda_seconds < 0 or item.agenda_seconds * 1000 >= video_end_ms:
        return (
            None,
            f"its agenda time of {item.agenda_seconds} s falls outside the "
            f"{video_end_ms / 1000:g} s video",
            "",
        )
    if not offset.accepted or offset.offset_s is None:
        return (
            None,
            f"its agenda time of {item.agenda_seconds} s could not be shifted, "
            f"because {offset.reason}",
            "",
        )
    shifted_ms = (item.agenda_seconds - offset.offset_s) * 1000
    if shifted_ms < 0 or shifted_ms >= video_end_ms:
        return (
            None,
            f"its agenda time of {item.agenda_seconds} s, shifted by {offset.offset_s} s, "
            f"falls outside the {video_end_ms / 1000:g} s video",
            "",
        )
    text = _window_text(segments, shifted_ms, settings.mention_window_s * 1000)
    mentioned = item.mentioned_words(set(words_of(text)), settings)
    if mentioned:
        return shifted_ms, "", f"the transcript near it mentions {' and '.join(mentioned)}"
    return (
        None,
        f"its agenda time of {item.agenda_seconds} s, shifted by {offset.offset_s} s to "
        f"{_seconds(shifted_ms)}, lands where the transcript does not mention it",
        "",
    )


def align_agenda_with_transcript(
    agenda_items: Iterable[AgendaItem],
    segments: Iterable[Segment],
    *,
    video_duration_ms: int | None,
    settings: AlignmentSettings | None = None,
) -> AlignmentResult:
    """Give each agenda item a start in the transcript, and say how (spec 10.2).

    Args:
        agenda_items: The items of the meeting's HTML agenda, in agenda order.
        segments: The meeting's transcript, in time order.
        video_duration_ms: The video's length. Without it no agenda time can
            be checked against the video, so method 1 gives nothing.
        settings: The numbers the aligner uses. The defaults are the measured
            ones.

    Returns:
        One boundary per item that could be placed, the offset that was
        measured or the reason none was, and the method that accounted for
        each item. Nothing is invented: an item that no method placed keeps no
        start and carries a plain reason.
    """
    active = settings or AlignmentSettings()
    items = [_build_item(index, item) for index, item in enumerate(agenda_items)]
    transcript = list(segments)

    taken, dropped = _spoken_boundaries(items, transcript, active)
    offset = _estimate_offset(items, taken, active)

    if not transcript:
        spoken_reason = "the meeting has no transcript to align with"
    elif not taken:
        spoken_reason = "no spoken transcript transition matched a packet item"
    else:
        spoken_reason = None

    if video_duration_ms is None:
        html_reason = "the video's length is not known, so no agenda time could be placed"
    elif not offset.accepted:
        html_reason = offset.reason
    else:
        html_reason = None

    if video_duration_ms is not None:
        video_end_ms = video_duration_ms
    elif transcript:
        video_end_ms = transcript[-1].end_ms
    else:
        video_end_ms = 0

    starts: dict[int, int] = {}
    methods: dict[int, str] = {}
    evidence: dict[int, tuple[str | None, int | None, int | None]] = {}
    reasons: dict[int, str] = {}

    for index, boundary in taken.items():
        starts[index] = boundary.start_ms
        methods[index] = SPOKEN_TRANSITION
        evidence[index] = (boundary.matched_text, boundary.start_ms, None)

    spoken_starts = {index: boundary.start_ms for index, boundary in taken.items()}
    for item in items:
        if item.index in starts:
            continue
        placed, why, mention = _method_one_boundary(item, offset, transcript, video_end_ms, active)
        if placed is not None and offset.offset_s is not None:
            why = _contradicts_transcript(item, placed, offset.offset_s, spoken_starts, items)
            if why:
                placed = None
        if placed is None:
            reasons[item.index] = why
            continue
        starts[item.index] = placed
        methods[item.index] = HTML_AGENDA_TIME
        evidence[item.index] = (mention, None, offset.offset_s)

    placed_indices = sorted(starts, key=lambda index: starts[index])
    ends: dict[int, int] = {}
    for position, index in enumerate(placed_indices):
        if position + 1 < len(placed_indices):
            ends[index] = starts[placed_indices[position + 1]]
        else:
            ends[index] = max(video_end_ms, starts[index])

    aligned: list[AlignedItem] = []
    for item in items:
        if item.index not in starts:
            aligned.append(
                AlignedItem(
                    number=item.number,
                    title=item.title,
                    method=NO_ALIGNMENT,
                    agenda_seconds=item.agenda_seconds,
                    reason=_no_boundary_reason(reasons.get(item.index, ""), active),
                )
            )
            continue
        matched_text, segment_start_ms, offset_used = evidence[item.index]
        aligned.append(
            AlignedItem(
                number=item.number,
                title=item.title,
                method=methods[item.index],
                start_ms=starts[item.index],
                end_ms=ends[item.index],
                evidence=matched_text,
                segment_start_ms=segment_start_ms,
                offset_s=offset_used,
                agenda_seconds=item.agenda_seconds,
            )
        )

    return AlignmentResult(
        items=tuple(aligned),
        offset=offset,
        video_duration_ms=video_duration_ms,
        settings=active,
        spoken_reason=spoken_reason,
        html_reason=html_reason,
        dropped=dropped,
    )


def _no_boundary_reason(html_reason: str, settings: AlignmentSettings) -> str:
    """One plain sentence saying what was missing for an item with no boundary.

    Both methods are named, because an item can fail one and pass the other,
    and a reader needs to know which door was closed.
    """
    spoken = (
        "no spoken transition matched its number, its ordinance or resolution number, or its title"
    )
    if html_reason:
        # Only the first letter: str.capitalize would also lowercase "10.A".
        return f"Not placed. {html_reason[0].upper()}{html_reason[1:]}. Spoken: {spoken}."
    return f"Not placed. Spoken: {spoken}."
