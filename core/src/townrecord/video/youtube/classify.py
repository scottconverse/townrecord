"""Telling a meeting apart from everything else a channel posts (spec 7.2 step 6).

A channel posts meetings, but it also posts neighborhood notices, tours,
sports and a weekly round-up. The rule is deliberately plain and the two
lists are data, not code: a user can change what counts as a meeting without
a new release, and both recorded channels can be re-checked against the
lists at any time.

Matching is on word boundaries, so ``Commission`` does not match
``Commissioners`` and ``Council`` does not match ``Councilwoman``. That cuts
both ways and the misses are known: a title that renames or misspells a body
falls through, and the fix is a keyword in settings, not a special case here.
A phrase only matches if it begins and ends with a word character, which is
what a boundary needs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: The bodies and session words a Longmont meeting title usually carries.
DEFAULT_MEETING_KEYWORDS: tuple[str, ...] = (
    "City Council",
    "Planning and Zoning",
    "Advisory Board",
    "Commission",
    "Authority",
    "Study Session",
    "Regular Session",
)

#: Titles that carry a keyword but are not meetings anyone attends as one.
#:
#: A neighborhood meeting is a developer's open house, not a public body; the
#: weekly round-up, the sports tournament and the city news show are programs
#: about the city rather than the city at work.
DEFAULT_SKIP_TITLES: tuple[str, ...] = (
    "This Week in Council",
    "Neighborhood Meeting",
    "Esports",
    "This is Longmont",
)


@dataclass(frozen=True)
class MeetingSettings:
    """The two lists as settings a caller stores and edits."""

    meeting_keywords: tuple[str, ...] = DEFAULT_MEETING_KEYWORDS
    meeting_skip_titles: tuple[str, ...] = DEFAULT_SKIP_TITLES


@dataclass(frozen=True)
class MeetingVerdict:
    """What the classifier decided about one title, and why."""

    title: str
    is_meeting: bool
    reason: str
    matched_keywords: tuple[str, ...] = ()
    matched_skips: tuple[str, ...] = ()


def _quoted(phrases: tuple[str, ...]) -> str:
    """`'a'`, `'a' and 'b'`, `'a', 'b', and 'c'`: how a person lists things."""
    if not phrases:
        return "nothing"
    if len(phrases) == 1:
        return f"'{phrases[0]}'"
    if len(phrases) == 2:
        return f"'{phrases[0]}' and '{phrases[1]}'"
    listed = ", ".join("'" + phrase + "'" for phrase in phrases[:-1])
    return f"{listed}, and '{phrases[-1]}'"


def _compile(phrases: Iterable[str]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (phrase, re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE))
        for phrase in phrases
        if phrase
    )


class MeetingClassifier:
    """A keyword list and a skip list, matched on word boundaries, case aside."""

    def __init__(
        self,
        keywords: Iterable[str] | None = None,
        skip: Iterable[str] | None = None,
    ) -> None:
        self.keywords: tuple[str, ...] = tuple(
            DEFAULT_MEETING_KEYWORDS if keywords is None else keywords
        )
        self.skip: tuple[str, ...] = tuple(DEFAULT_SKIP_TITLES if skip is None else skip)
        self._keywords = _compile(self.keywords)
        self._skips = _compile(self.skip)

    @classmethod
    def from_settings(
        cls, settings: MeetingSettings | Mapping[str, Any] | None = None
    ) -> MeetingClassifier:
        """Build from stored settings, from a mapping, or from the seeds."""
        if settings is None:
            return cls()
        if isinstance(settings, Mapping):
            keywords = settings.get("meeting_keywords")
            skip = settings.get("meeting_skip_titles")
        else:
            keywords = getattr(settings, "meeting_keywords", None)
            skip = getattr(settings, "meeting_skip_titles", None)
        return cls(
            keywords=DEFAULT_MEETING_KEYWORDS if keywords is None else keywords,
            skip=DEFAULT_SKIP_TITLES if skip is None else skip,
        )

    @staticmethod
    def _matches(title: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> tuple[str, ...]:
        text = title or ""
        return tuple(phrase for phrase, pattern in patterns if pattern.search(text))

    def matches_keywords(self, title: str) -> tuple[str, ...]:
        """Every keyword that matches, in the order the list holds them."""
        return self._matches(title, self._keywords)

    def matches_skips(self, title: str) -> tuple[str, ...]:
        """Every skip phrase that matches, in the order the list holds them."""
        return self._matches(title, self._skips)

    def verdict(self, title: str) -> MeetingVerdict:
        """The decision for one title, with the words the interface shows."""
        text = title or ""
        skips = self.matches_skips(text)
        keywords = self.matches_keywords(text)
        if skips:
            reason = f"Not read as a meeting: the skip phrase {_quoted(skips)} applies"
            if keywords:
                reason += f", though the keyword {_quoted(keywords)} matched"
            return MeetingVerdict(text, False, reason + ".", keywords, skips)
        if keywords:
            return MeetingVerdict(
                text, True, f"Read as a meeting: matched {_quoted(keywords)}.", keywords, ()
            )
        return MeetingVerdict(text, False, "Not read as a meeting: no keyword matched.")

    def is_meeting(self, title: str) -> bool:
        """One word answer, for a caller that does not need the reason."""
        return self.verdict(title).is_meeting

    def filter_meetings(self, videos: Iterable[Any]) -> list[Any]:
        """The listings whose titles read as meetings, in the order given."""
        return [video for video in videos if self.is_meeting(video.title)]
