"""Watching one video channel: list it, classify what it posted, queue captures.

This is the ``watch_channel`` job (spec 8.1, 8.2, 7.2 steps 6 and 7, 16.1). The
payload is the id of an accepted ``video_channel`` source, and the channel is
read out of that row and from nowhere else: the address, the body it belongs to
and the skip seeds are the source's, so watching two areas' channels is the same
job twice rather than a job with a city baked into it (rule D).

One run does four things:

* it lists the channel with the ladder of spec 8.1, and records which step
  answered and what each earlier step said;
* it classifies each listed title as a meeting or not, with the classifier built
  from that source's own settings (the ``sources.settings`` column of migration
  0013), falling back to the built-in seeds when the source carries none;
* it keeps one ``videos`` row per platform video, whichever reader found it
  first. A row a portal sync already made is adopted, not duplicated, and links
  it to a meeting of the source's body when the published date and the title
  agree, writing a plain note when they do not (spec 7.2 step 7). A row that
  exists is never renamed: only the meeting, the primary flag, the URL and a
  reading that is more definite than the one it holds are added;
* it queues one ``capture_captions`` per meeting video, once.

**Never captured early.** A video the listing says is ``upcoming`` or ``live`` is
counted and left for a later watch (spec 8.2); only ``finished`` and ``unknown``
are queued. ``unknown`` is queued on purpose and is not a mistake: a public feed
carries no status at all, so every video a feed lists is ``unknown``, and the
capture job's own gate of spec 8.2 is what holds it back until the metadata
arrives. A watch that queued nothing for a feed would never capture a feed's
meetings at all.

**A zero is a failure, not a quiet day.** When every method comes back with
nothing the job is FAILED with the reason, and the checkpoint names each method
that was tried and what it answered, so "no videos" can never be read as "no
meetings" (spec 16.3).

**Idempotent.** A video already captured (``captions`` or ``audio``) is not
queued again, and neither is a video that already has a stored transcript, even
when its capture state says nothing -- the words are the capture. The queue
refuses a second job of the same kind with the same payload too, so watching the
same channel twice adds nothing.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from .. import repo
from ..jobs import JobContext
from ..records import portal
from ..repo import Source
from .youtube.classify import MeetingClassifier
from .youtube.ladder import YOUTUBE_CHANNEL_URL, ListingLadder
from .youtube.listing import SOURCE_LABELS, NoVideosFound, source_label
from .youtube.readiness import READINESS_LIVE, READINESS_UNKNOWN, READINESS_UPCOMING

#: The job kind this module registers, and the lane it runs in (spec 16.1).
JOB_KIND = "watch_channel"
LANE = "normal"

#: The hosts whose links name a channel. This module reads an address out of a
#: configured source; an address on another host is refused rather than guessed.
YOUTUBE_HOSTS: tuple[str, ...] = ("youtube.com", "youtu.be")

#: The path segment a channel id stands behind, and the two a handle or a name
#: stands behind. They are the shapes YouTube publishes, and only these.
CHANNEL_PATH = "channel"
USERNAME_PATHS: tuple[str, ...] = ("c", "user")

#: A bare channel id: 24 characters, opening with UC.
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")

#: How many days either side of a video's published date a meeting may sit and
#: still be the meeting the video recorded. The window is not a guess about the
#: area: a feed dates an upload in UTC, an evening meeting in Denver is published
#: after midnight UTC, so the video of the 23rd can carry the 24th. A day either
#: side absorbs that, and the title still has to agree.
NEARBY_DAYS = 1

#: The capture states that mean "this video is already captured". A video in one
#: of them is never queued again, which is what makes a second watch free.
CAPTURED_STATES: tuple[str, ...] = ("captions", "audio")

#: The readiness words a video is never captured for yet (spec 8.2).
NOT_READY_STATES: tuple[str, ...] = (READINESS_UPCOMING, READINESS_LIVE)

#: What the checkpoint says when the channel listed nothing. It names the
#: methods, because a zero from a reader that never ran is not a fact about the
#: channel.
ZERO_VIDEOS_NOTE = (
    "No method listed any videos for this channel. The methods tried and what each "
    "answered are in methods_tried; this is a failed check, not a channel with no "
    "meetings."
)


class WatchRefused(RuntimeError):
    """A channel cannot be watched, with a plain sentence saying why."""


@dataclass(frozen=True)
class ChannelRef:
    """A channel as a source's origin names it, in the ladder's own arguments."""

    channel_id: str | None = None
    handle: str | None = None
    username: str | None = None
    channel_url: str | None = None

    def as_ladder_args(self) -> dict[str, Any]:
        """The arguments :meth:`ListingLadder.list_videos` takes."""
        return {
            "channel_id": self.channel_id,
            "handle": self.handle,
            "username": self.username,
            "channel_url": self.channel_url,
        }

    def described(self) -> str:
        """The channel in one line, for the checkpoint and the report."""
        for label, value in (
            ("channel id", self.channel_id),
            ("handle", self.handle),
            ("name", self.username),
        ):
            if value:
                return f"{label} {value}"
        return self.channel_url or "a channel with no address this watch can name"


@dataclass
class _Tally:
    """What one watch produced, for the checkpoint and the report."""

    listed: int = 0
    meeting_videos: int = 0
    other_videos: int = 0
    videos_created: int = 0
    videos_updated: int = 0
    videos_linked: int = 0
    captures_queued: int = 0
    already_captured: int = 0
    not_ready: int = 0
    not_ready_titles: list[str] = field(default_factory=list)
    skipped_total: int = 0
    skipped: list[str] = field(default_factory=list)

    def skip(self, sentence: str) -> None:
        """Record one thing the watch did not do, with its reason.

        Every skip is counted and only the first
        :data:`townrecord.records.portal.MAX_REPORTED_SKIPS` are kept by name, so
        a channel that has posted for years cannot grow a checkpoint without
        bound.
        """
        self.skipped_total += 1
        if len(self.skipped) < portal.MAX_REPORTED_SKIPS:
            self.skipped.append(sentence)


def watch_request(payload: Any) -> int:
    """Return the source id a watch payload names.

    A payload that names no source is a bug and not a pause: there is no source
    to pause on, so this raises and the job is FAILED with the reason.
    """
    value = payload.get("source_id") if isinstance(payload, Mapping) else payload
    if isinstance(value, bool):
        raise WatchRefused("The job payload is not a source id.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WatchRefused(f"The job payload {value!r} is not a source id.") from exc


def watchable_source(conn: sqlite3.Connection, source_id: int) -> Source:
    """Return the channel source to watch, or say plainly what stops it.

    The three refusals are the three of
    :func:`townrecord.records.portal.syncable_source`, for the channel's own
    type: a missing row, a source of another type, and a source the user has not
    accepted.
    """
    source = repo.get_source(conn, source_id)
    if source is None:
        raise WatchRefused(f"There is no source {source_id}.")
    if source.type != portal.VIDEO_CHANNEL:
        raise WatchRefused(
            f"Source {source.id} is a {source.type} source, which is not a video channel."
        )
    if source.status not in portal.SYNCABLE_STATUSES:
        raise WatchRefused(
            f"Source {source.id} is {source.status}, so it is not watched. "
            f"A watch runs on a source that is accepted or broken."
        )
    return source


def channel_of(origin: str) -> ChannelRef:
    """Read the channel an origin names, or refuse with a plain sentence.

    Four shapes are read, because they are the four a source is configured
    with: a channel URL (``/channel/UC...``, ``/@handle``, ``/c/name``,
    ``/user/name``), a bare channel id, a bare handle, and nothing else. yt-dlp
    needs the page and the feed needs the id, so a handle-only origin is read as
    what it is rather than completed into an id nobody gave.
    """
    text = (origin or "").strip()
    if not text:
        raise WatchRefused(
            "This source has no origin, so there is no channel to watch. A watch reads the "
            "channel the source's own origin names (rule D)."
        )
    if text.startswith("@"):
        return ChannelRef(handle=text, channel_url=f"https://www.youtube.com/{text}")
    if _looks_like_channel_id(text):
        return ChannelRef(channel_id=text, channel_url=YOUTUBE_CHANNEL_URL.format(channel_id=text))

    parsed = urlsplit(text if "//" in text else f"https://{text}")
    host = parsed.netloc.casefold().split("@")[-1].split(":")[0]
    if not any(host == name or host.endswith(f".{name}") for name in YOUTUBE_HOSTS):
        raise WatchRefused(f"The origin {origin!r} is not a YouTube channel this watch can read.")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise WatchRefused(f"The origin {origin!r} names no channel.")
    head = parts[0]
    if head == CHANNEL_PATH and len(parts) > 1:
        channel_id = parts[1]
        return ChannelRef(channel_id=channel_id, channel_url=text)
    if head.startswith("@"):
        return ChannelRef(handle=head, channel_url=text)
    if head in USERNAME_PATHS and len(parts) > 1:
        return ChannelRef(username=parts[1], channel_url=text)
    raise WatchRefused(
        f"The origin {origin!r} is a YouTube address but not a channel: this watch reads "
        "/channel/<id>, /@handle, /c/<name> and /user/<name>."
    )


def _looks_like_channel_id(text: str) -> bool:
    """Whether a bare origin is a channel id rather than a name.

    A channel id is 24 characters and opens with ``UC``, which is what every
    YouTube channel id does and what a handle or a name never does. The shape
    is checked rather than assumed, so a bare word is refused with a sentence
    instead of being sent to the feed as an id nobody has.
    """
    return bool(_CHANNEL_ID.fullmatch(text))


def refusal_for(conn: sqlite3.Connection, source: Source) -> str | None:
    """Why one channel source cannot be watched today, or None when it can.

    The sentences are the ones the job itself would refuse with, so the reason
    the user reads before the run and the reason recorded after it are the same
    one.
    """
    try:
        watchable_source(conn, source.id)
        channel_of(source.origin)
    except WatchRefused as exc:
        return str(exc)
    return None


def classifier_for(source: Source) -> tuple[MeetingClassifier, str | None]:
    """Build the classifier from the source's own settings (rule D).

    Returns the classifier and, when the settings could not be read, a plain
    sentence saying so. A source with no settings of its own, or one whose
    settings are not a JSON object, is classified with the built-in seeds rather
    than failing the watch: a listing that cannot be classified is still a
    listing, and the sentence is recorded with it.
    """
    text = (source.settings or "").strip()
    if not text or text == "{}":
        return MeetingClassifier.from_settings({}), None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        return (
            MeetingClassifier.from_settings({}),
            f"Source {source.id} carries settings that are not JSON ({exc}), so the built-in "
            "seeds are used instead.",
        )
    if not isinstance(loaded, Mapping):
        return (
            MeetingClassifier.from_settings({}),
            f"Source {source.id} carries settings that are not an object, so the built-in "
            "seeds are used instead.",
        )
    return MeetingClassifier.from_settings(loaded), None


def service_ladder(
    *, client: Any = None, interpreter: str | None = None, ytdlp: Any = None
) -> ListingLadder:
    """The ladder a real service watches with: the feed, then yt-dlp.

    The official API of spec 8.1 step 1 is not wired here and the ladder says
    why: it needs an API key, and no setting holds one yet, so the step reports
    "the official API is not configured: no API key was given" and the ladder
    moves on. The channel tab HTML of step 3 is a later unit's step
    (``TODO(unit D)`` in the ladder). Both missing steps are named in the
    checkpoint's ``methods_tried`` rather than passed over in silence.
    """
    from .youtube.rss import RssLister
    from .youtube.ytdlp import YtdlpFlatLister

    return ListingLadder(
        rss=None if client is None else RssLister(client),
        ytdlp=ytdlp if ytdlp is not None else YtdlpFlatLister(interpreter=interpreter),
    )


@dataclass
class WatchChannel:
    """The handler for the ``watch_channel`` job kind."""

    ladder: ListingLadder = field(default_factory=ListingLadder)

    def __call__(self, ctx: JobContext) -> None:
        watch_channel(ctx, self.ladder)


def register(*, registry: Any = None, ladder: ListingLadder | None = None) -> WatchChannel:
    """Register the watch job and return the handler.

    The ladder is injected rather than built here, because what a watch reads
    with is a deployment's business: a real service passes
    :func:`service_ladder`, a test passes the three recorded fixtures.
    """
    from ..jobs.registry import Registry, default_registry

    handler = WatchChannel(ladder=ladder if ladder is not None else ListingLadder())
    target: Registry = default_registry if registry is None else registry
    target.register(JOB_KIND, handler, lane=LANE)
    return handler


def watch_channel(ctx: JobContext, ladder: ListingLadder) -> None:
    """List one channel source, store what it posted, and queue its captures."""
    source_id = watch_request(ctx.payload)
    try:
        source = watchable_source(ctx.conn, source_id)
        channel = channel_of(source.origin)
    except WatchRefused as exc:
        ctx.pause(str(exc))
        return

    try:
        result = ladder.list_videos(**channel.as_ladder_args())
    except NoVideosFound as exc:
        reason = str(exc)
        ctx.save_checkpoint(_zero_checkpoint(source, channel, exc.attempts))
        failures = repo.record_source_failure(ctx.conn, source.id, reason)
        raise WatchRefused(_failure_sentence(source, reason, failures)) from exc
    except Exception as exc:  # a reader that raises anything else is a failure too
        reason = portal.adapter_problem(exc) if hasattr(exc, "message") else repr(exc)
        ctx.save_checkpoint(_failed_checkpoint(source, channel, reason))
        failures = repo.record_source_failure(ctx.conn, source.id, reason)
        raise WatchRefused(_failure_sentence(source, reason, failures)) from exc

    repo.record_source_success(ctx.conn, source.id)

    classifier, note = classifier_for(source)
    tally = _Tally()
    if note is not None:
        tally.skip(note)
    for listing in result.videos:
        ctx.heartbeat()
        _watch_video(ctx, source, listing, classifier, tally)
    ctx.save_checkpoint(_checkpoint(tally, source, channel, result))


def _watch_video(
    ctx: JobContext, source: Source, listing: Any, classifier: MeetingClassifier, tally: _Tally
) -> None:
    """Store one listed video, link it when it names a meeting, queue its capture."""
    tally.listed += 1
    verdict = classifier.verdict(listing.title)
    readiness = listing.readiness()
    # A video that does not read as a meeting is stored and not linked: it was
    # posted by the channel and a row records that it was seen and passed over,
    # but a link would claim the channel's weekly round-up was a sitting.
    meeting_id: int | None = None
    link_note = ""
    if verdict.is_meeting:
        meeting_id, link_note = _meeting_for(ctx.conn, source, listing)
    video, created = _upsert_video(ctx, source, listing, verdict, readiness, meeting_id=meeting_id)
    if created:
        tally.videos_created += 1
    else:
        tally.videos_updated += 1
    if link_note:
        tally.skip(link_note)
    elif meeting_id is not None:
        tally.videos_linked += 1

    if not verdict.is_meeting:
        tally.other_videos += 1
        return
    tally.meeting_videos += 1

    if readiness in NOT_READY_STATES:
        tally.not_ready += 1
        if len(tally.not_ready_titles) < portal.MAX_REPORTED_SKIPS:
            tally.not_ready_titles.append(
                f"{listing.title} ({listing.video_id}) is {readiness}, so its capture waits "
                "for a later watch (spec 8.2)."
            )
        return
    if _already_captured(ctx, video):
        tally.already_captured += 1
        return

    queued = portal.enqueue_once(ctx, capture_job_kind(), {"video_id": video.id})
    if queued is None:
        tally.already_captured += 1
        tally.skip(
            f"Video {video.id} ({listing.video_id}) is a meeting video and its capture is "
            "already queued, so no second job was made."
        )
    else:
        tally.captures_queued += 1


def _already_captured(ctx: JobContext, video: repo.Video) -> bool:
    """True when this video already has a capture, whoever made it.

    The capture state is the plain answer: it says a capture of this row
    finished. A stored transcript says the same thing even when the state does
    not, which is exactly what a row a portal sync made looks like -- the words
    are on the row, and the state column was never written by the job that
    stored them. Counting that as captured is what keeps a watch from queueing
    a second capture and a second transcript of a video the system already has
    (spec 7.2 step 7, 8.7).
    """
    if video.capture_state in CAPTURED_STATES:
        return True
    return repo.latest_transcript(ctx.conn, video.id) is not None


def _upsert_video(
    ctx: JobContext,
    source: Source,
    listing: Any,
    verdict: Any,
    readiness: str,
    *,
    meeting_id: int | None,
) -> tuple[repo.Video, bool]:
    """Store the one row for one platform video, or update the row it has.

    One YouTube video is one row, whichever reader found it first (spec 7.2
    step 7). So the row is found by platform id and not by this source: a row a
    portal sync made for the same video is the row this watch adopts, and the
    watch keeps its title, its source and its capture state exactly as they are.
    This is the same adoption the portal sync does in the other direction
    (:func:`townrecord.records.sync._record_video`), so two readers that found
    one video cannot leave two rows behind. A row that exists is never renamed.

    What a watch adds is only what the row lacks: the meeting, the primary flag,
    the URL, and a readiness that is more definite than the one the row holds --
    a reading of ``finished`` replaces a row that was waiting for status
    metadata, and a reading of ``unknown`` never replaces an answer.
    """
    known = repo.videos_of_platform(ctx.conn, listing.video_id)
    mine = next((video for video in known if video.source_id == source.id), None)
    # This channel's own row when it has one; otherwise the oldest row for the
    # video, which is the one whoever read the video first left behind.
    row = mine if mine is not None else (known[0] if known else None)
    if row is not None:
        if meeting_id is not None:
            repo.attach_video(
                ctx.conn,
                row.id,
                meeting_id=meeting_id,
                is_primary=repo.primary_video(ctx.conn, meeting_id) is None,
                url=listing_url(listing.video_id),
            )
        if readiness != READINESS_UNKNOWN and row.readiness == READINESS_UNKNOWN:
            repo.set_readiness(ctx.conn, row.id, readiness)
        return repo.get_video(ctx.conn, row.id) or row, False

    video_id = repo.insert_video(
        ctx.conn,
        source_id=source.id,
        platform_video_id=listing.video_id,
        meeting_id=meeting_id,
        title=listing.title,
        url=listing_url(listing.video_id),
        published_at=listing.published,
        duration_s=listing.duration_s,
        is_primary=meeting_id is not None and repo.primary_video(ctx.conn, meeting_id) is None,
        readiness=readiness,
        source_note=_source_note(source, listing, verdict, meeting_id),
    )
    stored = repo.get_video(ctx.conn, video_id)
    if stored is None:  # pragma: no cover - the row was just written
        raise WatchRefused(f"Video {video_id} was written and could not be read back.")
    return stored, True


def capture_job_kind() -> str:
    """The kind of job a watch asks for, read from the capture package.

    It is read here rather than spelled again, so that a watch and the handler
    it queues cannot drift apart. The import is inside the function because the
    capture package is the layer above this one: it reads videos, and this
    module must be importable without it.
    """
    from ..capture.command import JOB_KIND as kind

    return kind


def listing_url(platform_video_id: str) -> str:
    """The watch address of one platform video id."""
    from ..capture.command import watch_url

    return watch_url(None, platform_video_id)


def _source_note(source: Source, listing: Any, verdict: Any, meeting_id: int | None) -> str:
    """What the row says about why it is here, in plain words."""
    if meeting_id is not None:
        return (
            f"Listed by watched channel source {source.id} and linked to meeting "
            f"{meeting_id}. {verdict.reason}"
        )
    return f"Listed by watched channel source {source.id}, linked to no meeting. {verdict.reason}"


def _meeting_for(conn: sqlite3.Connection, source: Source, listing: Any) -> tuple[int | None, str]:
    """The meeting a listed video records, or a plain sentence saying why none.

    The body comes from the source and the portal's own reader, which is what a
    meeting portal's listing is resolved with, so a channel and a portal agree
    about which body a title names. The date and the type come from the video's
    own title and the listing's published date. A day either side is tried only
    when the published day itself matches nothing, because a feed dates an
    upload in UTC and an evening meeting in Denver is published after midnight.

    Nothing here guesses: one meeting has to be left, and two leave a sentence
    saying so rather than a link to whichever came first.
    """
    body = portal.resolve_body(conn, source, listing.title)
    if body is None:
        return None, (
            f"Video {listing.video_id} ({listing.title!r}) is not linked to a meeting: it "
            f"names no body of jurisdiction {source.jurisdiction_id}, and source {source.id} "
            "names none of its own."
        )
    day = published_day(listing.published)
    if day is None:
        return None, (
            f"Video {listing.video_id} ({listing.title!r}) is not linked to a meeting: its "
            f"published date {listing.published!r} is not a date this watch can read."
        )

    where = f"body {body.id} ({body.name!r})"
    on_the_day = _title_matches(
        repo.meetings_of_body(conn, body.id, day.isoformat(), day.isoformat()), listing.title
    )
    if len(on_the_day) == 1:
        return on_the_day[0].id, ""
    if len(on_the_day) > 1:
        return None, (
            f"Video {listing.video_id} ({listing.title!r}) is not linked to a meeting: "
            f"{len(on_the_day)} meetings of {where} on {day.isoformat()} read as the same kind "
            "as its title, and this watch will not guess which one it recorded."
        )

    nearby = _nearby_window(day)
    around = _title_matches(
        repo.meetings_of_body(conn, body.id, nearby[0], nearby[1]), listing.title
    )
    if len(around) == 1:
        return around[0].id, ""
    if not around:
        return None, (
            f"Video {listing.video_id} ({listing.title!r}) is not linked to a meeting: no "
            f"meeting of {where} between {nearby[0]} and {nearby[1]} is the kind of sitting its "
            "title names."
        )
    return None, (
        f"Video {listing.video_id} ({listing.title!r}) is not linked to a meeting: "
        f"{len(around)} meetings of {where} between {nearby[0]} and {nearby[1]} read as the "
        "same kind as its title, and this watch will not guess which one it recorded."
    )


def _nearby_window(day: date) -> tuple[str, str]:
    """The days either side of a published day, as inclusive YYYY-MM-DD strings."""
    slack = timedelta(days=NEARBY_DAYS)
    return (day - slack).isoformat(), (day + slack).isoformat()


def _title_matches(meetings: Sequence[Any], title: str) -> list[Any]:
    """The meetings of one body a video's title could be about.

    Two readings, and the body is already settled by the caller, so what is
    compared is the name the video's title carries -- when it carries one -- and
    the kind of sitting both titles name. "City Council Regular Session" and
    "City Council - Regular Session 9/22/26" are that one meeting, while the
    study session of the same body on the next day is not, which is why the type
    is part of the reading and not only the name.
    """
    wanted = portal.body_name(title).casefold().strip()
    wanted_type = portal.meeting_type(title)
    matches: list[Any] = []
    for meeting in meetings:
        meeting_title = meeting.title or ""
        named = portal.body_name(meeting_title).casefold().strip()
        if wanted and named and named != wanted:
            continue
        if portal.meeting_type(meeting_title) != wanted_type:
            continue
        matches.append(meeting)
    return matches


def published_day(published: str | None) -> date | None:
    """The day a listing's published value falls on, or None.

    The value is read exactly as the source gave it and is never rewritten, so
    only the date part of a timestamp this module can parse is used (spec 8.1:
    "the time exactly as the source gave it").
    """
    text = (published or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _failure_sentence(source: Source, reason: str, failures: int) -> str:
    """One sentence for a failed listing, naming the count and the status.

    It is the sentence a failed portal sync is recorded with, so the two checks
    of spec 7.3 read the same way in ``townrecord status``.
    """
    return portal.failure_sentence(source, reason, failures)


def _checkpoint(tally: _Tally, source: Source, channel: ChannelRef, result: Any) -> dict[str, Any]:
    """The summary a reader finds on the job when it is done."""
    summary: dict[str, Any] = {
        "source_id": source.id,
        "channel": channel.described(),
        "origin": source.origin,
        "method": result.method,
        "method_label": result.label,
        "methods_tried": _attempts(result.attempts),
        "videos_listed": tally.listed,
        "meeting_videos": tally.meeting_videos,
        "other_videos": tally.other_videos,
        "videos_created": tally.videos_created,
        "videos_updated": tally.videos_updated,
        "videos_linked": tally.videos_linked,
        "captures_queued": tally.captures_queued,
        "already_captured": tally.already_captured,
        "not_ready": tally.not_ready,
        "skipped_total": tally.skipped_total,
        "skipped": list(tally.skipped),
    }
    if tally.not_ready_titles:
        summary["not_ready_videos"] = list(tally.not_ready_titles)
    if tally.skipped_total > portal.MAX_REPORTED_SKIPS:
        summary["skipped_note"] = (
            f"{tally.skipped_total} things were skipped; the first "
            f"{portal.MAX_REPORTED_SKIPS} are listed."
        )
    return summary


def _zero_checkpoint(
    source: Source, channel: ChannelRef, attempts: Sequence[Any]
) -> dict[str, Any]:
    """What the job records when no method listed any videos.

    The methods and their reasons are the whole point: a zero from a reader that
    was never tried, or one that was refused for want of a key, is not a fact
    about the channel (spec 16.3).
    """
    return {
        "source_id": source.id,
        "channel": channel.described(),
        "origin": source.origin,
        "method": None,
        "videos_listed": 0,
        "zero_videos": ZERO_VIDEOS_NOTE,
        "methods_tried": _attempts(attempts),
    }


def _failed_checkpoint(source: Source, channel: ChannelRef, reason: str) -> dict[str, Any]:
    """What the job records when the ladder itself raised."""
    return {
        "source_id": source.id,
        "channel": channel.described(),
        "origin": source.origin,
        "method": None,
        "videos_listed": 0,
        "failed": reason,
        "methods_tried": [],
    }


def _attempts(attempts: Sequence[Any]) -> list[dict[str, Any]]:
    """Each ladder step as a plain row: which method, and what it answered."""
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        method = str(getattr(attempt, "method", ""))
        rows.append(
            {
                "method": method,
                "label": str(getattr(attempt, "label", "")) or source_label(method),
                "ok": bool(getattr(attempt, "ok", False)),
                "count": int(getattr(attempt, "count", 0)),
                "reason": str(getattr(attempt, "reason", "")),
            }
        )
    return rows


__all__ = [
    "CAPTURED_STATES",
    "CHANNEL_PATH",
    "ChannelRef",
    "JOB_KIND",
    "LANE",
    "NEARBY_DAYS",
    "NOT_READY_STATES",
    "SOURCE_LABELS",
    "USERNAME_PATHS",
    "YOUTUBE_HOSTS",
    "ZERO_VIDEOS_NOTE",
    "WatchChannel",
    "WatchRefused",
    "channel_of",
    "classifier_for",
    "listing_url",
    "published_day",
    "refusal_for",
    "register",
    "service_ladder",
    "watch_channel",
    "watch_request",
    "watchable_source",
]
