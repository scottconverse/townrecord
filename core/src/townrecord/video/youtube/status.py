"""Asking whether a video is finished, when the row does not already say (spec 8.2).

Spec 8.2 gives the order: "Use the API's liveBroadcastContent and duration if
available; else the player endpoint status (LIVE_STREAM_OFFLINE means
upcoming); else yt-dlp --dump-single-json and its live_status field."

A listing writes a readiness word on every row it makes, and a public feed
carries no status at all, so every video a feed listed reads ``unknown``. A
capture job that only read that word back would wait for metadata nothing is
ever going to write, which is every video of every channel for a user with no
API key. So the word ``unknown`` is the one that means "ask", and this module is
the asking: the three steps of the spec, in the spec's order, each recorded with
what it answered, and the first definite answer is the one that is used.

What comes out is one of the four readiness words of
:mod:`townrecord.video.youtube.readiness`, which is the module that turns a
status into a word, plus a plain sentence naming what was asked and what each
ask answered. Nothing here writes to the database: the caller writes the answer
on the row, so the next run reads it instead of asking again.

Two of the three steps are deliberately not built: no setting holds a Data API
key yet, and the player endpoint is a network call this reader does not make.
Both say so in their own sentence rather than passing over in silence, the way
the listing ladder of spec 8.1 names its own missing step.

yt-dlp is run as an argument list with an allow-listed environment and no shell
(spec 8.3, PROJECT-BRIEF rule E), under a time limit, and the runner is
injected, so no test starts a process. Spec 8.9 puts yt-dlp in TownRecord's own
runtime, so the interpreter defaults to the one running this code.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from townrecord import proc
from townrecord.runtime.javascript import FALLBACK_RUNTIME, resolve
from townrecord.video.youtube.listing import SOURCE_DATA_API, SOURCE_YTDLP
from townrecord.video.youtube.readiness import (
    READINESS_FINISHED,
    READINESS_UNKNOWN,
    READINESS_WAITING_REASON,
    readiness,
    readiness_reason,
)

#: Spec 8.2's middle step: the player endpoint, whose status the spec reads as
#: ``LIVE_STREAM_OFFLINE`` meaning upcoming.
SOURCE_PLAYER = "player"

#: Spec 8.2's three steps, in the words a sentence uses for them.
ASK_LABELS: dict[str, str] = {
    SOURCE_DATA_API: "the official API",
    SOURCE_PLAYER: "the player endpoint status",
    SOURCE_YTDLP: "yt-dlp --dump-single-json",
}

#: Why the first step said nothing: the same missing setting the listing ladder
#: names, in as few words as will do, because this reason is nested inside a
#: deferred job's one-line reason, which the queue caps (spec 8.1 step 1, 8.2).
API_NOT_CONFIGURED = "no API key was given"

#: Why the middle step said nothing. It is named rather than skipped, so the
#: answer never reads as though the spec's own middle step had been tried.
PLAYER_NOT_BUILT = "not built here"

#: The module, not the script: the private runtime is asked for it by name.
YTDLP_MODULE = "yt_dlp"

#: Seconds. One video's information is a small read, not a download.
STATUS_TIMEOUT_S = 60

#: The shape of an injected runner, matching :func:`townrecord.proc.run`.
Runner = Callable[..., proc.ProcessResult]


def info_argv(interpreter: str, url: str, *, js_runtime: str | None = None) -> list[str]:
    """The exact command of spec 8.2's third step, as an argument list.

    ``--dump-single-json`` prints one video's information and downloads
    nothing, so no ``--skip-download`` is needed. ``js_runtime`` is the value
    for ``--js-runtimes``: YouTube's pages need a JavaScript runtime, and the
    one the private runtime of spec 8.9 installs beside this interpreter is
    passed in. None means the bare fallback name, which yt-dlp looks for
    itself.
    """
    return [
        interpreter,
        "-m",
        YTDLP_MODULE,
        "--dump-single-json",
        "--js-runtimes",
        js_runtime or FALLBACK_RUNTIME,
        url,
    ]


def describe_status(info: Mapping[str, Any]) -> str:
    """Name the status an info dict carried, in as few words as will do.

    The readiness module's own reason for an undecided video is a sentence fit
    for a listing's note and runs to the width of a paragraph; this reason is
    nested inside a deferred job's one-line reason, which the queue caps at 300
    characters. So the field is named, and the one value that is not
    self-explanatory is spelled out: ``post_live`` means the stream has ended
    and the recording is still being written, so waiting is right and there is
    no download to make yet.
    """
    status = info.get("live_status")
    if status == "post_live":
        return (
            "live_status=post_live: the stream has ended and the recording is still being written"
        )
    if isinstance(status, str) and status:
        return f"live_status={status}"
    if status is None:
        return "the information carries no live_status"
    return f"live_status={status!r}"


@dataclass(frozen=True)
class StatusAsk:
    """One step of spec 8.2, and what it answered.

    ``readiness`` stays ``unknown`` when the step could not decide, which
    covers both a step that is not built and a status this reader does not
    know. ``reason`` always says why, so neither case is a silent nothing.
    """

    method: str
    label: str
    readiness: str = READINESS_UNKNOWN
    reason: str = ""

    @property
    def answered(self) -> bool:
        """True when this step produced one of the three definite words."""
        return self.readiness != READINESS_UNKNOWN

    def sentence(self) -> str:
        """What this step said, in one plain phrase.

        A step that decided names the word it decided and the reason for it. A
        step that decided nothing is not silent about it: its own label stands
        beside the reason, so "nothing was decided" never reads the same as
        "nothing was asked".

        The phrase is short on purpose. The reason of the whole answer is what a
        deferred job says, and the queue caps a reason at 300 characters
        (:data:`townrecord.jobs.queue.MAX_REASON`), so the steps are named and
        the essays about them are left to :mod:`townrecord.video.youtube.readiness`.
        """
        if self.answered:
            return f"{self.label} said {self.readiness} ({self.reason})"
        return f"{self.label}: {self.reason}"


@dataclass(frozen=True)
class ReadinessAnswer:
    """The answer to one video's read, and every step it took to get there."""

    readiness: str
    reason: str
    asks: tuple[StatusAsk, ...] = ()

    @property
    def sentence(self) -> str:
        """What was asked and what each ask answered, in plain words.

        A readiness that stays ``unknown`` after all three steps is still an
        answer, and this sentence is what a deferred job says: which steps were
        asked, and what each one said.
        """
        if not self.asks:
            return "Nothing was asked: no way of asking was configured."
        return "Asked: " + "; ".join(ask.sentence() for ask in self.asks) + "."


class StatusAsker:
    """Asks one video's status the way spec 8.2 orders, and stops at an answer.

    The steps after the one that answered are not run, because the spec says
    "else": a definite word from the API means nothing else has to be asked.
    Every step that did run is in the answer, in order.
    """

    def __init__(
        self,
        *,
        runner: Runner = proc.run_allowlisted,
        interpreter: str | None = None,
        timeout_s: float = STATUS_TIMEOUT_S,
        js_runtime: str | None = None,
        api: Any = None,
    ) -> None:
        self.runner = runner
        #: Spec 8.9: the Python that runs TownRecord is the Python that runs yt-dlp.
        self.interpreter = interpreter or sys.executable
        self.timeout_s = timeout_s
        #: The JavaScript runtime yt-dlp is told to use (spec 8.3, 8.9): the
        #: one the private runtime installed beside that interpreter, and the
        #: bare fallback name when there is none.
        self.js_runtime = js_runtime or resolve(self.interpreter).argument
        #: The reader for spec 8.2's first step, or None when no API key is
        #: configured. It is a duck: ``video_status(video_id)`` answers a
        #: listing, or None when the API holds no such video. Nothing wires one
        #: yet: the Data API client reads a channel, not one video
        #: (:meth:`townrecord.video.youtube.data_api.DataApiClient.list_channel_videos`),
        #: and no setting holds a key, so this step says so and the next one is
        #: asked instead.
        self.api = api

    def ask(self, *, video_id: str, url: str) -> ReadinessAnswer:
        """Ask, in the spec's order, and answer with the first definite word."""
        asks: list[StatusAsk] = []
        for step in (self._ask_api, self._ask_player, self._ask_ytdlp):
            ask = step(video_id, url)
            asks.append(ask)
            if ask.answered:
                return ReadinessAnswer(readiness=ask.readiness, reason=ask.reason, asks=tuple(asks))
        last = asks[-1]
        return ReadinessAnswer(
            readiness=READINESS_UNKNOWN,
            reason=last.reason or READINESS_WAITING_REASON,
            asks=tuple(asks),
        )

    # -- the three steps, each usable on its own -----------------------------

    def _ask_api(self, video_id: str, url: str) -> StatusAsk:
        """Spec 8.2 step 1: the official API, which needs a key."""
        method, label = SOURCE_DATA_API, ASK_LABELS[SOURCE_DATA_API]
        if self.api is None:
            return StatusAsk(method=method, label=label, reason=API_NOT_CONFIGURED)
        try:
            listing = self.api.video_status(video_id)
        except Exception as exc:  # a reader that raises is a step that answered nothing
            return StatusAsk(method=method, label=label, reason=f"the call failed: {exc}")
        if listing is None:
            return StatusAsk(method=method, label=label, reason=f"it holds no video {video_id}")
        word = readiness(listing)
        if word == READINESS_UNKNOWN:
            return StatusAsk(
                method=method, label=label, reason="it decided nothing about this video"
            )
        return StatusAsk(
            method=method, label=label, readiness=word, reason=readiness_reason(listing)
        )

    def _ask_player(self, video_id: str, url: str) -> StatusAsk:
        """Spec 8.2 step 2: the player endpoint, which is not built here."""
        # TODO(unit D): the player endpoint status, where LIVE_STREAM_OFFLINE
        # means upcoming, is not this unit's work and nothing here pretends to
        # read it.
        return StatusAsk(
            method=SOURCE_PLAYER, label=ASK_LABELS[SOURCE_PLAYER], reason=PLAYER_NOT_BUILT
        )

    def _ask_ytdlp(self, video_id: str, url: str) -> StatusAsk:
        """Spec 8.2 step 3: yt-dlp's own info dict and its ``live_status``."""
        method, label = SOURCE_YTDLP, ASK_LABELS[SOURCE_YTDLP]
        argv = info_argv(self.interpreter, url, js_runtime=self.js_runtime)
        try:
            result = self.runner(argv, timeout_s=self.timeout_s, env=proc.allowed_environment())
        except proc.ProcessTimedOut as exc:
            return StatusAsk(method=method, label=label, reason=f"it was stopped, {exc}")
        except proc.ProcessFailed as exc:
            return StatusAsk(method=method, label=label, reason=f"{exc}")
        except OSError as exc:
            return StatusAsk(method=method, label=label, reason=f"it could not be started: {exc}")

        if result.returncode != 0:
            detail = result.last_stderr_line() or "no reason given"
            return StatusAsk(
                method=method,
                label=label,
                reason=f"it exited {result.returncode}: {detail}",
            )
        try:
            info = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            return StatusAsk(method=method, label=label, reason=f"its output was not JSON: {exc}")
        if not isinstance(info, Mapping):
            return StatusAsk(method=method, label=label, reason="its output was not a JSON object")
        word = readiness(info=info)
        if word == READINESS_UNKNOWN:
            return StatusAsk(method=method, label=label, reason=describe_status(info))
        return StatusAsk(
            method=method, label=label, readiness=word, reason=readiness_reason(info=info)
        )


def is_finished(answer: ReadinessAnswer) -> bool:
    """True when the ask decided the video may be captured (spec 8.2)."""
    return answer.readiness == READINESS_FINISHED
