"""Comparing two yt-dlp versions.

The two spellings of the same release are why this file exists. Asked live on
2026-09-27, PyPI's JSON API said ``2026.8.19`` and the same release's own
``python -m yt_dlp --version`` said ``2026.08.19``. Comparing those as text
would say a fresh install is out of date on every single daily check, so a
version is compared as its numeric parts, and only text that carries no number
falls back to text.

yt-dlp releases are dated (``2026.8.19``), and a build from the master branch
carries a fourth part (``2026.8.19.232914``). Anything else, a word or a
string with a suffix, is compared as text, which is the honest answer when the
shape is not known.
"""

from __future__ import annotations

import re

#: A dotted run of digits at the start of a version: ``2026.8.19``.
_NUMERIC = re.compile(r"^(\d+(?:\.\d+)*)")


def version_key(text: str) -> tuple[int, ...] | None:
    """Return the numeric parts of a version, or None when it has none.

    Every numeric part is compared, so ``2026.8.19.232914`` is later than
    ``2026.8.19``. Leading zeros are dropped, so ``2026.8.19`` and
    ``2026.08.19`` give the same key.
    """
    match = _NUMERIC.match(str(text).strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def same_version(left: str, right: str) -> bool:
    """Return True when two spellings name the same release."""
    left_key = version_key(left)
    right_key = version_key(right)
    if left_key is not None and right_key is not None:
        return left_key == right_key
    return str(left).strip() == str(right).strip()


def is_newer(candidate: str, current: str) -> bool:
    """Return True when `candidate` is a later release than `current`.

    Text without numbers has no order, so it counts as newer only when it is
    not the same text. The probe of spec 8.9 is what decides whether a
    candidate is trusted, so a guess here cannot put a bad version in place.
    """
    if same_version(candidate, current):
        return False
    candidate_key = version_key(candidate)
    current_key = version_key(current)
    if candidate_key is None or current_key is None:
        return True
    return candidate_key > current_key
