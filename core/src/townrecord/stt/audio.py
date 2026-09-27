"""Downloading one meeting's audio, and why (spec 8.3, 8.5, 8.10).

Audio is the last fallback (spec 8.4): it is downloaded only when captions
are missing or unusable, or when the user turned on "Local transcription
only" (spec 8.10), and the reason is recorded every time.

The reason is a closed set, and this module is where the set lives. A caller
cannot ask for audio without one of its members, and the two things that are
not reasons cannot be spelled into one:

* HTTP 429. Spec 8.3 is explicit: a rate limit is a paced retry on a later
  pass, not a reason to download anything.
* Anything else a caller might invent. The values are the three below, and a
  word that is not one of them is refused with a message that says so.

The command is an argument list, never a string for a shell (spec 8.3,
PROJECT-BRIEF rule E). The interpreter is the one running TownRecord, because
spec 8.9 has the application manage its own Python runtime rather than use
whatever ``yt-dlp`` is on the user's PATH.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from townrecord.video.youtube.ytdlp import JS_RUNTIME, YTDLP_MODULE

__all__ = [
    "AUDIO_OUTPUT_TEMPLATE",
    "AUDIO_TRIGGER_REASONS",
    "AudioRequest",
    "AudioRequestError",
    "AudioTrigger",
    "NotAnAudioTrigger",
    "audio_download_argv",
]

#: The output template, relative to the work folder. yt-dlp fills in the
#: video id, so the audio of one meeting sits beside the info JSON of the
#: same id and a resumed download finds it.
AUDIO_OUTPUT_TEMPLATE = "%(id)s.%(ext)s"


class AudioRequestError(ValueError):
    """An audio request could not be built. The message says why."""


class NotAnAudioTrigger(AudioRequestError):
    """The value offered as a reason to download audio is not one of the set."""


class AudioTrigger(StrEnum):
    """Why this meeting's audio is being downloaded. Spec 8.5's closed set.

    ``LOCAL_TRANSCRIPTION_ONLY`` is the setting of spec 8.10: captions are
    turned off, so every meeting is transcribed on the user's own machine.
    """

    NO_CAPTIONS = "no_captions"
    CAPTIONS_UNUSABLE = "captions_unusable"
    LOCAL_TRANSCRIPTION_ONLY = "local_transcription_only"

    @classmethod
    def from_value(cls, value: AudioTrigger | str) -> AudioTrigger:
        """Read a reason, refusing anything that is not one of the set.

        Args:
            value: A member, or the exact text of a member as it arrives in a
                stored job payload.

        Returns:
            The member.

        Raises:
            NotAnAudioTrigger: The value is not one of the set.
        """
        if isinstance(value, cls):
            return value
        text = value.strip() if isinstance(value, str) else ""
        try:
            return cls(text)
        except ValueError:
            allowed = ", ".join(member.value for member in cls)
            raise NotAnAudioTrigger(
                f"{value!r} is not a reason to download audio. The reasons are: {allowed}."
            ) from None


#: The reasons, for a caller that wants to list them.
AUDIO_TRIGGER_REASONS: tuple[AudioTrigger, ...] = tuple(AudioTrigger)


@dataclass(frozen=True)
class AudioRequest:
    """One meeting's audio, and the reason it is being downloaded.

    The trigger is not optional and has no default. A caller that cannot say
    why cannot build this record, so the reason is always there to be stored
    with the capture (spec 8.5).
    """

    watch_url: str
    trigger: AudioTrigger
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.watch_url, str) or not self.watch_url.strip():
            raise AudioRequestError("an audio request needs the watch URL of the meeting")
        trigger = AudioTrigger.from_value(self.trigger)
        if trigger is not self.trigger:
            object.__setattr__(self, "trigger", trigger)

    def argv(self, work_dir: str | Path, archive_file: str | Path) -> list[str]:
        """The download command for this request, as an argument list."""
        return audio_download_argv(self.watch_url, work_dir, archive_file)


def audio_download_argv(
    watch_url: str, work_dir: str | Path, archive_file: str | Path
) -> list[str]:
    """The audio-only download command of spec 8.5, as an argument list.

    Args:
        watch_url: The watch page of the meeting's video.
        work_dir: The folder the audio and the info JSON are written to. The
            download runs with this as its working directory, so a stopped
            capture resumes in the same folder (spec 8.3).
        archive_file: The yt-dlp download archive, regenerated from the
            database before each capture (spec 8.7).

    Returns:
        The command, ready for :func:`townrecord.proc.run_allowlisted`. It is
        never a string for a shell: no part of it is quoted or joined.
    """
    output = Path(work_dir) / AUDIO_OUTPUT_TEMPLATE
    return [
        sys.executable,
        "-m",
        YTDLP_MODULE,
        "-x",
        "--audio-format",
        "opus",
        "--audio-quality",
        "5",
        "--write-info-json",
        "--js-runtimes",
        JS_RUNTIME,
        "--sleep-requests",
        "1",
        "--download-archive",
        str(archive_file),
        "-o",
        str(output),
        watch_url,
    ]
