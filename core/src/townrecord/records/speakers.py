"""Who is speaking, read out of the chair's own words (spec 10.6).

A caption file names nobody. An srv3 file says only that somebody changed
speaker, with ``>>``, and what the person said. Spec 10.6 asks for the names
anyway, and the names are in the room: the chair says who she is turning to
before the person speaks. This module reads those words.

The reading has four steps, and each one is a function here.

* :func:`runs_of` splits a transcript where the captioner marked a change of
  speaker. A run is the lines from one change through the line before the next.
* :func:`recognitions_of` finds the places a run names somebody by their title,
  ``Council member Marcen``, and :func:`recognition_of` picks the one that
  matters: the chair's recognition sits at the end of her own run, so the last
  one that is not a part of something else is the one she said to hand over.
* :func:`match_name` reads a spoken name as one of the body's seated officials.
  The names in a machine transcript of a live room are wrong in ways a
  spell-checker does not see: "Marcen" for Jake Marsing, "Poppin" for Popkin,
  "Chris" for Crist. The method, the cutoff and the margin are stated as
  constants below, with the reason each was chosen.
* :func:`read_labels` puts the two together: a recognition in one run names the
  speaker of the *next* run.

Nothing below the cutoff is written as a name. Spec 10.6 shows such a run as
"an unidentified speaker", and the words the chair said and the closest official
to them are kept on the label row so that a person can confirm it later
(migration ``0014_speakers.sql``).

:func:`cross_check` is the second record. The minutes of a meeting name the
person who moved each motion and the person who seconded it (spec 10.4), and
the chair says the same two names when she puts the motion. When the two
records agree, the label on the lines where that person speaks is marked as
confirmed by both. When they disagree, both names are kept and the label
carries the sentence saying what each record holds.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from ..jobs import JobContext
from ..repo import (
    Person,
    Segment,
    agenda_items,
    clear_speaker_labels,
    get_body,
    get_meeting,
    insert_speaker_label,
    latest_transcript,
    mark_confirmed,
    mark_conflict,
    meeting_date,
    motion_items_of,
    motions_of_meeting,
    officials_of,
    primary_video,
    segments_of,
    set_segment_speaker,
)

#: What the srv3 format puts in front of a line that starts a new speaker.
CHANGE_MARKER = ">>"

#: The name of the way a spoken name is scored. A score with no method is not a
#: fact a later reader can check, so the method travels with it into the row.
METHOD = "jaro_winkler_phonetic"

#: How close a spoken name has to be to an official to be written as that
#: person's name. Measured over the spellings of the real Longmont captions
#: (the September 8, 2026 regular session): the lowest score any correct
#: reading of that transcript reached is 0.7222 ("Brietto" for Prieto), so the
#: cutoff sits just under it and refuses nothing that was right there. A wrong
#: reading above it is the margin's business and not the cutoff's: "Marcy"
#: scores 0.800 for McCoy and 0.9067 for the Marsing the room meant, and no
#: single cutoff separates those two.
CUTOFF = 0.72

#: How far ahead of the runner-up the best score has to be. A cutoff alone
#: cannot tell a wrong reading from a right one when two officials of one body
#: sound alike, which is the normal case on a council: on that transcript a
#: score of 0.76 for McCoy sat 0.01 ahead of Crist and is not a reading. Over
#: the same spellings, the reading the margin refuses is ahead by 0.0447 at
#: most ("Calcoffer" for Kalkhofer, with Prieto at 0.7775 behind it), and the
#: reading it comes closest to refusing is ahead by 0.0889 ("Coloffer" for the
#: same official, with 0.6444 behind it). Five hundredths sits between those
#: two gaps: above every reading the margin refuses, below the narrowest one
#: it keeps.
MARGIN = 0.05

#: Which title the chair used, as the row records it. The mayor pro tem is not
#: the mayor, and a label that did not say which one was said would be a claim
#: about the office that the chair did not make.
_TITLE_KINDS = {
    "mayor": "mayor",
    "mayor pro tem": "mayor_pro_tem",
}

#: The words a chair uses to hand the floor over, and the words she uses to say
#: who moved and who seconded a motion. Both name a person by their title, and
#: only the first is a recognition of the next speaker: the second is a summary
#: of a motion that the minutes also hold, which is why it is read separately
#: (part D of the skill, and :func:`cross_check`).
_TITLE = r"(?:council\s*member|councilman|councilwoman|mayor\s+pro\s+tem|mayor)"

#: A name as a captioner writes one: one to three words, either case.
_NAME = r"[A-Za-z][\w'’.\-]*(?:\s+[A-Za-z][\w'’.\-]*){0,2}"

_NAMED = re.compile(rf"(?P<title>{_TITLE})\s+(?P<name>{_NAME})", re.IGNORECASE)

#: How many words before the title are read to tell a recognition from a
#: summary. The window is for the thanks test only: "Thank you, Council Member
#: McCoy" puts two words in front of it and a chair thanks somebody in a whole
#: sentence, so the words are read a little further back than the phrase.
_BEFORE_WORDS = 6

#: How many words before the title are read for the role of a naming. The role
#: is said in the phrase that names the person and not anywhere else: "the
#: motion has been made by council member Popkin" puts "made by" in front of
#: the title and two words are enough to see it. Reading a wider window is what
#: makes a recognition look like a summary: "Um, can I have a motion to go
#: ahead, Council Member Popkin" is the chair asking Popkin for a motion and
#: handing him the floor, and a chair who has just said the word "motion" is
#: still recognizing the next speaker.
_ROLE_WORDS = 3

#: The words that can sit between the verb and the person it names and mean
#: nothing: the articles of "seconded by the council member", and the hesitation
#: a live room puts between the two ("and it was, uh, made by council member
#: Popkin"). A word of one letter is one of those as well, because a captioner
#: writes "made by U. Council Member Coloffer" for a name she could not hear.
_FILLER_WORDS = frozenset({"a", "an", "the", "our", "own", "my", "her", "his", "your", "uh", "um"})

#: The words a motion is moved with, and the words it is seconded with. "motion"
#: is deliberately not among them: a chair says "can I have a motion" and "were
#: you going to make a motion" while she is handing the floor over, and the word
#: is about the motion she is asking for rather than one that was made.
_MOVER_WORDS = ("made", "moved", "move", "moves")
_SECONDER_WORDS = ("seconded", "second", "seconds")
_THANKS_WORDS = ("thank", "thanks")

#: The words a person uses to second a motion themselves. The chair's summary
#: and the person's own words are two ways to the same fact, and the second of
#: them needs no chair: whoever says it is the seconder, and the run it is in is
#: their run.
_SELF_SECOND = re.compile(r"\bi(?:'ll| will| would)\s+(?:gladly\s+)?second\b", re.IGNORECASE)

_SENTENCE_END = re.compile(r"[.!?]")


@dataclass(frozen=True)
class Run:
    """The lines from one speaker change through the line before the next."""

    index: int
    segments: tuple[Segment, ...]

    @property
    def start_segment_id(self) -> int:
        """The first line of the run, which is the line the change is on."""
        return self.segments[0].id

    @property
    def end_segment_id(self) -> int:
        """The last line of the run."""
        return self.segments[-1].id

    @property
    def start_ms(self) -> int:
        """When the run's first line was said."""
        return self.segments[0].start_ms

    @property
    def text(self) -> str:
        """The run's lines as one text, which is how its words are read.

        A title and the name after it can straddle two lines, because the
        captioner breaks lines where the words fell and not where a sentence
        ends ("Council member Marcen," then "could you give us an update"). The
        joined text is what makes those two lines one naming.
        """
        return " ".join(segment.text for segment in self.segments)


@dataclass(frozen=True)
class Recognition:
    """One place a run named somebody by their title."""

    run: int
    title_kind: str
    spoken: str
    evidence: str


@dataclass(frozen=True)
class Summary:
    """One place a run said who moved or who seconded a motion.

    ``role`` is 'mover' or 'seconder', which is what the minutes of the meeting
    hold for the same motion (spec 10.4).
    """

    run: int
    role: str
    spoken: str
    evidence: str


@dataclass(frozen=True)
class Match:
    """One spoken name read against the officials of a body (spec 10.6).

    ``candidate`` is the closest official whether or not the reading passed the
    cutoff, because a reader is shown the guess and its score for a line that
    stayed unidentified. :attr:`person` is the official the name is written as,
    which is None for every reading below the cutoff.
    """

    spoken: str
    candidate: Person | None
    score: float
    runner_up: float
    method: str = METHOD

    @property
    def passed(self) -> bool:
        """Whether the reading is close enough to write as a name.

        Both tests have to hold: the score has to reach the cutoff, and it has
        to be clear of the runner-up by the margin. A name that is close to two
        officials of one body is not a reading of either of them.
        """
        return self.score >= CUTOFF and self.score - self.runner_up >= MARGIN

    @property
    def person(self) -> Person | None:
        """The official this name was read as, or None below the cutoff."""
        return self.candidate if self.passed else None


@dataclass(frozen=True)
class ReadLabel:
    """One run of lines that the chair's words gave a speaker to."""

    run: Run
    recognition: Recognition
    match: Match

    @property
    def person(self) -> Person | None:
        """The official the run is labelled with, or None when unidentified."""
        return self.match.person

    @property
    def identified(self) -> bool:
        """Whether this run was named, or shown as an unidentified speaker."""
        return self.match.person is not None


@dataclass(frozen=True)
class CrossCheck:
    """What the minutes agreed and disagreed with about one transcript.

    ``checked`` counts the motions whose item has a time range and that the
    transcript was therefore read for. A motion of an item with no time range is
    not counted: nothing in the transcript could be found near it, and a motion
    nobody looked for is not a motion that agreed.
    """

    checked: int = 0
    confirmed: int = 0
    conflicts: int = 0
    sentences: tuple[str, ...] = ()


def runs_of(segments: Sequence[Segment]) -> list[Run]:
    """Split a transcript where the captioner marked a change of speaker.

    The first run starts at the first line, and every line whose text begins
    with the marker starts a run of its own, so a change is the first line of
    the run it begins rather than the last line of the one it ends. Nothing is
    taken out of the text: spec 10.1 reads a caption as captioned, so the marker
    stays where it was written.
    """
    runs: list[Run] = []
    current: list[Segment] = []
    for segment in segments:
        if current and _starts_change(segment.text):
            runs.append(Run(index=len(runs), segments=tuple(current)))
            current = []
        current.append(segment)
    if current:
        runs.append(Run(index=len(runs), segments=tuple(current)))
    return runs


def recognitions_of(run: Run) -> list[Recognition]:
    """The places in a run where somebody was named by their title.

    A naming that is part of a motion summary ("the motion has been made by
    council member Popkin") and a naming that is an aside ("Thank you, Council
    Member McCoy") are both left out: neither hands the floor over, and reading
    one as a recognition would put the chair's own words on somebody else's
    lines.
    """
    return [
        Recognition(
            run=run.index,
            title_kind=naming.title_kind,
            spoken=naming.spoken,
            evidence=naming.evidence,
        )
        for naming in _namings_of(run)
        if naming.role is None
    ]


def summaries_of(run: Run) -> list[Summary]:
    """The places in a run that name the mover or the seconder of a motion.

    These are the namings :func:`recognitions_of` leaves out, kept because the
    minutes name the same two people (spec 10.4) and two records saying one
    thing is a fact one record cannot carry alone.
    """
    return [
        Summary(run=run.index, role=naming.role, spoken=naming.spoken, evidence=naming.evidence)
        for naming in _namings_of(run)
        if naming.role is not None
    ]


def recognition_of(run: Run) -> Recognition | None:
    """The recognition a run hands the floor over with, or None.

    The last one is the one that counts. The chair talks, then names the person
    she is turning to, and then that person's line starts the next run, so a
    recognition with words after it is a recognition she went past. A run that
    names nobody hands nothing over, and None is that answer rather than a
    guess at who was speaking.
    """
    found = recognitions_of(run)
    return found[-1] if found else None


def match_name(spoken: str, officials: Sequence[Person]) -> Match:
    """Read one spoken name as one of the officials of a body (spec 10.6).

    The score is the best Jaro-Winkler similarity over a phonetic key of the
    name. Two things about that choice were measured rather than assumed.

    The phonetic key keeps the first letter, drops the vowels and the glide
    consonants, and reduces each other letter to its Soundex class without ever
    truncating the result. It is what makes "Chris" and "Crist" the same key
    (C623) while plain Jaro-Winkler puts them at 0.833, below every wrong
    reading of a two-syllable name. It is also what keeps "Marcy" apart from
    "McCoy" (0.800) and close to "Marsing" (0.907), which is the reading the
    room actually meant.

    A spoken name is compared whole and word by word, against an official's
    full name, their surname, and each part of a hyphenated surname, because a
    captioner writes "Marcen", "Marcy" and "Jake Marsing" for one person and a
    chamber says "Mayor Hidalgo-Fahring" and "Council member Fahring" for
    another.

    Two names are compared only when they start with the same sound, which is
    the one part of a name a listener almost never loses. The gate is there
    because a phonetic key is short for a short name and two unrelated short
    keys can still share most of their symbols: "Zabrowski" and "Marsing" share
    no sound at all, and their keys Z1622 and M6252 alone put them at 0.733,
    above the cutoff. Comparing the class of the first letter rather than the
    letter itself is what keeps "Coloffer" and "Kalkhofer", which start with
    different letters but the same sound.

    The best score wins, and a reading is written as a name only when it reaches
    :data:`CUTOFF` and clears the runner-up by :data:`MARGIN`. Below that the
    match still names its closest official and the score it reached, so the row
    of an unidentified speaker says what was nearly read.
    """
    scored: list[tuple[float, int, Person]] = []
    for official in officials:
        best = 0.0
        for said in _spoken_variants(spoken):
            for known in _known_variants(official.name):
                if _initial_class(said) != _initial_class(known):
                    continue
                best = max(best, jaro_winkler(phonetic_key(said), phonetic_key(known)))
        scored.append((best, official.id, official))
    if not scored:
        return Match(spoken=spoken, candidate=None, score=0.0, runner_up=0.0)
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    best_score, _, candidate = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    return Match(
        spoken=spoken,
        candidate=candidate,
        score=round(best_score, 4),
        runner_up=round(runner_up, 4),
    )


def read_labels(segments: Sequence[Segment], officials: Sequence[Person]) -> list[ReadLabel]:
    """Read who is speaking, run by run (spec 10.6).

    The words that name the next speaker are the last words of the run before
    theirs, because the chair says them after the person who was speaking has
    stopped and before the next one starts. So a recognition in one run labels
    the run after it, and the last run labels nothing: there is nothing after it
    for the words to hand the floor to.

    A run whose own lines name nobody is not labelled and keeps a NULL
    ``speaker_label``, which is what spec 10.6 shows as an unidentified
    speaker. A chair does not name everybody, and a label invented for those
    lines would be a claim the transcript does not support.
    """
    labels: list[ReadLabel] = []
    runs = runs_of(segments)
    for run in runs[:-1]:
        recognition = recognition_of(run)
        if recognition is None:
            continue
        labels.append(
            ReadLabel(
                run=runs[run.index + 1],
                recognition=recognition,
                match=match_name(recognition.spoken, officials),
            )
        )
    return labels


def read_speakers(ctx: JobContext) -> None:
    """Label one meeting's transcript with who is speaking (spec 10.6).

    The job runs for a meeting that has both a transcript and seats: a meeting
    with no transcript has no lines to label, and one whose body has no seat on
    the date of the meeting has nobody a line could be labelled as. Either of
    those pauses with the sentence saying which one it is, rather than writing a
    reading nothing stands behind.

    Reading is idempotent. Every label of the meeting is cleared first, and
    clearing takes the names back off the lines as well as deleting the rows, so
    a second run writes one set of rows and leaves no name behind from the
    first.
    """
    from . import portal

    request = portal.speakers_request(ctx.payload)
    meeting = get_meeting(ctx.conn, request.meeting_id)
    if meeting is None:
        ctx.pause(f"There is no meeting {request.meeting_id} to read the speakers of.")
        return

    video = primary_video(ctx.conn, meeting.id)
    if video is None:
        ctx.pause(f"Meeting {meeting.id} has no primary video, so it has no lines to read.")
        return
    transcript = latest_transcript(ctx.conn, video.id)
    if transcript is None:
        ctx.pause(
            f"Video {video.id} of meeting {meeting.id} has no transcript yet, so nobody's "
            "lines can be labelled."
        )
        return

    body = get_body(ctx.conn, meeting.body_id)
    on = meeting_date(meeting)
    officials = officials_of(ctx.conn, body_id=meeting.body_id, on=on)
    if not officials:
        said = "" if body is None else f" of {body.name}"
        ctx.pause(
            f"No seat on {on.isoformat()} names anybody on the body{said} of meeting "
            f"{meeting.id}, so there is nobody a line could be labelled as."
        )
        return

    segments = segments_of(ctx.conn, transcript.id)
    if not segments:
        ctx.pause(f"Transcript {transcript.id} of meeting {meeting.id} has no lines to label.")
        return

    clear_speaker_labels(ctx.conn, meeting.id)
    labels = read_labels(segments, officials)
    stored = _store(ctx, meeting_id=meeting.id, transcript_id=transcript.id, labels=labels)
    checked = cross_check(
        ctx,
        meeting_id=meeting.id,
        officials=officials,
        segments=segments,
        stored=stored,
    )
    ctx.save_checkpoint(
        {
            "meeting_id": meeting.id,
            "transcript_id": transcript.id,
            "segments": len(segments),
            "labels": len(labels),
            "identified": sum(1 for label in labels if label.identified),
            "unidentified": sum(1 for label in labels if not label.identified),
            "motions_checked": checked.checked,
            "confirmed": checked.confirmed,
            "conflicts": checked.conflicts,
        }
    )


def cross_check(
    ctx: JobContext,
    *,
    meeting_id: int,
    officials: Sequence[Person],
    segments: Sequence[Segment],
    stored: Sequence[tuple[ReadLabel, int]],
) -> CrossCheck:
    """Put the labels of a meeting against the motions its minutes record.

    Part D of the skill: the chair names the mover and the seconder when she
    puts a motion, and the minutes of the same meeting name the same two people
    for the same motion (spec 10.4). Two primary records naming one person is
    the strongest fact either of them can carry alone, and the label on the
    lines where that person speaks is marked as confirmed by the minutes.

    What is checked is the person, not the words: the chair's summary is read
    for the name it says, and the label that gets marked is the one on the lines
    of *that person* in the item's stretch of the transcript. Reading the run
    after the summary instead would mark the wrong person, because a chair who
    says "the motion has been made by council member Popkin, seconded by council
    member Crist" names two people in one breath and only one of them speaks
    next.

    A conflict is kept rather than resolved. When the captions and the minutes
    name different people for one motion, the label of the person the chair
    named carries a sentence saying what the minutes say instead, and both names
    are kept. Spec 10.4 shows both sources when they disagree rather than
    picking one, and the same rule holds here: the reader is told what each
    record says.

    Only a motion linked to an item with a time range is checked. Without a
    range there is no moment of the transcript to look near, and a motion nobody
    looked for is not a motion that agreed. A summary whose name did not pass
    the cutoff claims nothing: nothing was read, so nothing is being said
    against the minutes.
    """
    ranges = _item_ranges(ctx.conn, meeting_id)
    runs = runs_of(segments)
    labelled = {label.run.index: (label, label_id) for label, label_id in stored}
    by_person: dict[int, list[tuple[ReadLabel, int]]] = {}
    for label, label_id in stored:
        if label.person is not None:
            by_person.setdefault(label.person.id, []).append((label, label_id))
    claims: list[_Claim] = []
    checked = 0
    for motion in motions_of_meeting(ctx.conn, meeting_id):
        windows = [ranges[item.agenda_item_id] for item in motion_items_of(ctx.conn, motion.id)]
        found_windows = [window for window in windows if window is not None]
        if not found_windows:
            continue
        checked += 1
        for run in runs:
            if not _in_windows(run, found_windows):
                continue
            for said in summaries_of(run):
                expected = motion.mover if said.role == "mover" else motion.seconder
                if not expected:
                    continue
                person = match_name(said.spoken, officials).person
                holder = None if person is None else _nearest_label(by_person.get(person.id), run)
                if person is None or holder is None:
                    continue
                claims.append(
                    _Claim(
                        label_id=holder,
                        role=said.role,
                        spoken=said.spoken,
                        person=person,
                        expected=expected,
                    )
                )
            holder = labelled.get(run.index)
            if holder is not None and motion.seconder and _SELF_SECOND.search(run.text):
                claims.append(
                    _Claim(
                        label_id=holder[1],
                        role="seconder",
                        spoken=holder[0].recognition.spoken,
                        person=holder[0].person,
                        expected=motion.seconder,
                    )
                )

    confirmed = 0
    conflicts = 0
    sentences: list[str] = []
    seen: set[tuple[int, str]] = set()
    for claim in claims:
        if (claim.label_id, claim.role) in seen or claim.person is None:
            continue
        seen.add((claim.label_id, claim.role))
        if _is_the_minutes_name(claim.person, claim.expected):
            mark_confirmed(ctx.conn, claim.label_id, by="minutes")
            confirmed += 1
            continue
        sentence = (
            f"The minutes name {claim.expected} as the {claim.role} of a motion of meeting "
            f"{meeting_id}, and the captions name {claim.spoken} there ({claim.person.name})."
        )
        mark_conflict(ctx.conn, claim.label_id, sentence)
        sentences.append(sentence)
        conflicts += 1
    return CrossCheck(
        checked=checked,
        confirmed=confirmed,
        conflicts=conflicts,
        sentences=tuple(sentences),
    )


def phonetic_key(text: str) -> str:
    """The sound of a name as a key, for comparing names that are misspelled.

    The first letter is kept, because a captioner who hears a name still hears
    the letter it starts with, and the letter is what tells "Crist" from
    "Krist" only when it is the same one. The vowels and the glide consonants
    (h, w, y) are dropped, because they are what a listener moves around. Every
    other letter becomes its Soundex class, and the codes are kept whole: a
    Soundex code is four characters and the end of a long name carries as much
    of it as the start ("Marsing" and "Marzang" differ in the middle, not at the
    end).
    """
    letters = [letter for letter in text.casefold() if letter.isalpha()]
    if not letters:
        return ""
    key = [letters[0]]
    for letter in letters[1:]:
        code = _SOUNDEX_CLASSES.get(letter)
        if code is not None:
            key.append(code)
    return "".join(key)


def _initial_class(text: str) -> str:
    """The sound a name starts with, as the class of its first letter.

    A name is compared against another only when the two begin with the same
    class, because the start of a name is what a listener keeps and the rest of
    a short name's key is too little to tell two unrelated names apart. A letter
    the Soundex classes leave out, which is every vowel and h, w and y, is its
    own class: two names that both begin with a vowel are compared, and two that
    begin with different vowels are not.
    """
    letters = [letter for letter in text.casefold() if letter.isalpha()]
    if not letters:
        return ""
    return _SOUNDEX_CLASSES.get(letters[0], letters[0])


def jaro_winkler(first: str, second: str, *, prefix_weight: float = 0.1) -> float:
    """How close two keys are, from 0 to 1.

    The Jaro similarity is how many characters of the one occur in the other
    within half their length, and the Winkler part gives a longer prefix a
    bonus, up to four characters, which is what makes two names that sound the
    same at the start score near each other. The bonus is applied only above
    the usual 0.7, so a pair of keys with nothing in common does not get lifted
    by a shared first letter.
    """
    if first == second:
        return 1.0
    if not first or not second:
        return 0.0
    jaro = _jaro(first, second)
    if jaro <= 0.7:
        return jaro
    prefix = 0
    # Two keys of different lengths share a prefix for as long as the shorter
    # one has letters, so the shorter one ends the scan and is not an error.
    for left, right in zip(first, second, strict=False):
        if left != right or prefix == 4:
            break
        prefix += 1
    return jaro + prefix * prefix_weight * (1 - jaro)


def _jaro(first: str, second: str) -> float:
    """The Jaro similarity of two keys, from 0 to 1.

    A character of the one counts when the other has the same character within
    half of the longer key's length, and each character of the other counts
    once. What is left is how many of those matches are out of order, which is
    what a captioner produces when she hears two letters the wrong way round.
    """
    window = max(0, max(len(first), len(second)) // 2 - 1)
    ours = [False] * len(first)
    theirs = [False] * len(second)
    same = 0
    for index, letter in enumerate(first):
        low = max(0, index - window)
        high = min(index + window + 1, len(second))
        for position in range(low, high):
            if theirs[position] or second[position] != letter:
                continue
            ours[index] = True
            theirs[position] = True
            same += 1
            break
    if not same:
        return 0.0
    # The match flags were made one per character of the keys, and one match
    # flips one flag in each, so the two matched lists are the same length.
    matched_first = [letter for letter, was in zip(first, ours, strict=True) if was]
    matched_second = [letter for letter, was in zip(second, theirs, strict=True) if was]
    transposed = sum(
        1 for left, right in zip(matched_first, matched_second, strict=True) if left != right
    )
    return (same / len(first) + same / len(second) + (same - transposed / 2) / same) / 3


#: Soundex classes, with the letters Soundex drops left out entirely.
_SOUNDEX_CLASSES = {
    "b": "1",
    "f": "1",
    "p": "1",
    "v": "1",
    "c": "2",
    "g": "2",
    "j": "2",
    "k": "2",
    "q": "2",
    "s": "2",
    "x": "2",
    "z": "2",
    "d": "3",
    "t": "3",
    "l": "4",
    "m": "5",
    "n": "5",
    "r": "6",
}


@dataclass(frozen=True)
class _Naming:
    """One place a run named somebody by their title, before it is classified.

    ``role`` is None for a recognition and 'mover' or 'seconder' for the two
    kinds of motion summary. It is one scan with two readings rather than two
    scans, because the words that tell them apart are the same words.
    """

    title_kind: str
    spoken: str
    role: str | None
    evidence: str


def _namings_of(run: Run) -> list[_Naming]:
    """Every naming in a run, with the motion summary it is part of if any."""
    text = run.text
    found: list[_Naming] = []
    for match in _NAMED.finditer(text):
        if _said_thanks(_words_before(text, match.start(), _BEFORE_WORDS)):
            continue
        spoken = _name_words(match.group("name"))
        if not _written_as_a_name(text, spoken):
            continue
        found.append(
            _Naming(
                title_kind=_title_kind(match.group("title")),
                spoken=spoken,
                role=_summary_role(_words_before(text, match.start(), _ROLE_WORDS)),
                evidence=_sentence_at(text, match.start()),
            )
        )
    return found


def _starts_change(text: str) -> bool:
    """Whether a line is the first line of a new speaker."""
    return text.lstrip().startswith(CHANGE_MARKER)


def _title_kind(title: str) -> str:
    """Which title was used, as the row records it."""
    said = " ".join(title.casefold().split())
    for printed, kind in _TITLE_KINDS.items():
        if printed in said:
            return kind
    return "member"


def _written_as_a_name(text: str, spoken: str) -> bool:
    """Whether the captioner wrote the words after a title as somebody's name.

    A captioner capitalizes a name, even one she has never heard before: the
    September 8 captions of the real fixture write "Council member Marcen",
    "council member Koffer" and "Council Member Poppin". She does not capitalize
    the ordinary words that follow a title ("the mayor, for this board"), and a
    title followed by ordinary words is a chair talking about somebody rather
    than to them.

    The test is made against the transcript's own writing, not against English:
    a caption file that has no capital letter anywhere is one where the
    convention says nothing, and no naming is dropped from it for its case.
    """
    first = spoken.split()[0] if spoken else ""
    if not first or first[:1].isupper():
        return True
    return not any(letter.isupper() for letter in text)


def _name_words(name: str) -> str:
    """One spoken name, without the punctuation around it."""
    words = [word.strip(".,;:!?\"'") for word in name.split()]
    return " ".join(word for word in words if word)


def _words_before(text: str, position: int, count: int) -> list[str]:
    """The words just before a position, nearest first.

    The window is small on purpose, and how small is the caller's decision: the
    phrase that names a person is one or two words long, and a wider window
    would read a motion summary in one sentence as the reason for a name in the
    next.
    """
    before = text[:position].casefold()
    words = re.findall(r"[a-z']+", before)
    return list(reversed(words[-count:]))


def _said_thanks(before: Sequence[str]) -> bool:
    """Whether the words before a title are the chair thanking somebody."""
    return any(word in _THANKS_WORDS for word in before)


def _summary_role(before: Sequence[str]) -> str | None:
    """Whether the words before a title are a motion's mover or seconder.

    The role is the word the title hangs off: "made by council member Popkin"
    puts "by" and then "made" in front of the title, and "so moved council
    member Koffer" puts the verb there itself. A word that is not one of those
    is the ordinary case, which is a chair saying a name to hand the floor over.

    Reading it this way is what tells the two apart, and the difference is not
    academic. "Um, can I have a motion to go ahead, Council Member Popkin" is a
    recognition, and reading the "motion" further back in the sentence as its
    role would leave Popkin's own lines unlabelled.
    """
    words = [word for word in before if len(word) > 1 and word not in _FILLER_WORDS]
    if not words:
        return None
    if words[0] == "by":
        return _role_of(words[1]) if len(words) > 1 else None
    return _role_of(words[0])


def _role_of(word: str) -> str | None:
    """Which role a verb in front of a name says, or None when it says neither."""
    if word in _SECONDER_WORDS:
        return "seconder"
    if word in _MOVER_WORDS:
        return "mover"
    return None


def _sentence_at(text: str, position: int) -> str:
    """The sentence a position falls in, for the evidence of a label.

    The sentence is enough to find the place again and enough to show why the
    reading was made, which is what a citation of a transcript is for
    (spec 10.5). It is cut at a full stop, a question mark or an exclamation
    mark on either side of the position.
    """
    start = 0
    for found in _SENTENCE_END.finditer(text, 0, position):
        start = found.end()
    found = _SENTENCE_END.search(text, position)
    end = len(text) if found is None else found.end()
    return " ".join(text[start:end].split())


def _spoken_variants(spoken: str) -> list[str]:
    """The ways one spoken name is compared, longest part first.

    A captioner writes the name she heard and keeps writing after it: "Council
    member Christ was absent" puts words after the name that are not part of it.
    So every prefix of the name is one of its readings, from the whole phrase
    down to the first word alone, and the first word is the one that carries the
    name in almost every case.
    """
    words = [word for word in spoken.split() if word]
    variants: list[str] = []
    for length in range(len(words), 0, -1):
        variants.append(" ".join(words[:length]))
        variants.append(words[length - 1])
    return variants


def _known_variants(name: str) -> list[str]:
    """The ways one official's name is compared: whole, and by surname part."""
    words = [word for word in name.split() if word]
    variants = [" ".join(words)]
    if words:
        surname = words[-1]
        variants.append(surname)
        variants.extend(part for part in surname.split("-") if part)
    return variants


def _is_the_minutes_name(named: Person, expected: str) -> bool:
    """Whether an official is the person a minutes document names.

    The minutes print a bare name, "Sean McCoy", and the officials are held as
    the records name them, so the comparison is on the words of the name and
    not on a score: a minute's name is not a misheard one. A surname alone
    counts, because a minutes document that names one person by surname and
    another by full name is still naming the same two people, and a name that
    matches no official is compared as printed.
    """
    wanted = [word for word in expected.casefold().split() if word]
    known = [word for word in named.name.casefold().split() if word]
    if not wanted:
        return False
    return wanted[-1] in known


def _item_ranges(conn: sqlite3.Connection, meeting_id: int) -> dict[int, tuple[int, int] | None]:
    """The time range of every item of a meeting, or None for a timed-out item.

    An item with no range is None rather than absent, so a motion linked to one
    is a motion that was looked for and could not be found in the transcript,
    which is a different fact from a motion of an item nobody looked at.
    """
    ranges: dict[int, tuple[int, int] | None] = {}
    for item in agenda_items(conn, meeting_id):
        if item.start_ms is None or item.end_ms is None:
            ranges[item.id] = None
        else:
            ranges[item.id] = (item.start_ms, item.end_ms)
    return ranges


def _in_windows(run: Run, windows: Sequence[tuple[int, int]]) -> bool:
    """Whether a run starts inside any of the time ranges of a motion."""
    return any(low <= run.start_ms < high for low, high in windows)


def _nearest_label(candidates: Sequence[tuple[ReadLabel, int]] | None, run: Run) -> int | None:
    """The label of a person nearest to a run, or None when they have none.

    A person's lines are their own wherever they are in the meeting, so the
    reading of a motion belongs to their nearest set of them. A run at or after
    the summary wins a tie with one before it, because the chair says the name
    and the person's own words are what follow.
    """
    if not candidates:
        return None
    nearest = min(
        candidates,
        key=lambda entry: (
            0 if entry[0].run.index >= run.index else 1,
            abs(entry[0].run.index - run.index),
        ),
    )
    return nearest[1]


@dataclass(frozen=True)
class _Claim:
    """One thing the captions say about one role of one motion.

    ``person`` is who the captions name, which is None when the reading of the
    name did not pass the cutoff, and ``expected`` is the name the minutes hold
    for the same role. A claim whose caption side is unnamed is not a
    disagreement: nothing was read, so nothing is being said against the
    minutes.
    """

    label_id: int
    role: str
    spoken: str
    person: Person | None
    expected: str


def _store(
    ctx: JobContext,
    *,
    meeting_id: int,
    transcript_id: int,
    labels: Sequence[ReadLabel],
) -> list[tuple[ReadLabel, int]]:
    """Write one label per run the chair named, and name the run's lines.

    A run that stayed unidentified writes no name on its lines: they keep a NULL
    ``speaker_label``, which is spec 10.6's "an unidentified speaker", and the
    row keeps the words and the closest official so that the reading can be
    confirmed by a person later.
    """
    stored: list[tuple[ReadLabel, int]] = []
    for label in labels:
        person = label.person
        candidate = label.match.candidate
        label_id = insert_speaker_label(
            ctx.conn,
            meeting_id=meeting_id,
            transcript_id=transcript_id,
            start_segment_id=label.run.start_segment_id,
            end_segment_id=label.run.end_segment_id,
            spoken_name=label.recognition.spoken,
            title_kind=label.recognition.title_kind,
            person_id=None if person is None else person.id,
            candidate_person_id=None if candidate is None else candidate.id,
            score=label.match.score,
            method=label.match.method,
            evidence=label.recognition.evidence,
        )
        name = None if person is None else person.name
        for segment in label.run.segments:
            set_segment_speaker(ctx.conn, segment.id, name)
        stored.append((label, label_id))
    return stored


__all__ = [
    "CHANGE_MARKER",
    "CUTOFF",
    "MARGIN",
    "METHOD",
    "CrossCheck",
    "Match",
    "ReadLabel",
    "Recognition",
    "Run",
    "Summary",
    "cross_check",
    "jaro_winkler",
    "match_name",
    "phonetic_key",
    "read_labels",
    "read_speakers",
    "recognition_of",
    "recognitions_of",
    "runs_of",
    "summaries_of",
]
