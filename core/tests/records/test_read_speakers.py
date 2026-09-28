"""Who is speaking: the seats out of the minutes, and the names on the lines.

Spec 10.6 has two halves, and both of them are read out of records rather than
written here. A body's seated officials come from the minutes of a meeting,
which record who voted on it: nobody is invented from a caption file, because a
captioner who writes "Marcen" has heard a name rather than established a person.
The speaker of a run of transcript lines comes from the chair, who names the
person she is handing the floor to just before that person talks, and the
captioner's ``>>`` mark is where one speaker's lines stop and the next start.

The five things the checks were asked for, in the order they were asked for:

* a caption's spelling of a name ("Marcen") is read as the official it sounds
  like (Jake Marsing);
* a name that matches nobody of the body stays "an unidentified speaker", with
  the raw words and the closest official and its score kept beside it;
* no person is created from a caption, whatever a caption says;
* a label runs from one ``>>`` to the line before the next;
* a motion whose mover or seconder the captions and the minutes agree on is
  marked as confirmed by two records, and a disagreement keeps both.

Two of the tests read the recorded September 8, 2026 caption track and packet
of the oversight repository in place and are skipped when the recordings are not
on this machine. Everything else is built here, so the five checks hold on a
machine with no recordings at all.

Nothing here reaches the network: the portal is never asked for anything, and
the records the tests read are built in the database (rule 7).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from townrecord.jobs import DONE, read_checkpoint
from townrecord.records.portal import READ_SPEAKERS, SpeakersRequest
from townrecord.repo import (
    Segment,
    agenda_items,
    get_person,
    insert_agenda_item,
    insert_motion,
    insert_motion_item,
    motions_of_meeting,
    segments_of,
    speaker_label_of_segment,
    speaker_labels_of_meeting,
    update_agenda_item_alignment,
    upsert_person,
    upsert_seat,
)

from .conftest import (
    AGENDA_16805,
    CAPTIONS_16805,
    PACKET_MINUTES_SEP08,
    VIDEO_3709_DURATION_S,
    Area,
    FakePortal,
    Sync,
    needs,
)
from .test_align_meeting import a_meeting as a_listed_meeting
from .test_align_meeting import a_video as its_video
from .test_align_meeting import align
from .test_read_minutes import (
    COUNCIL,
    HEAD,
    SEPT_8,
    SEPT_22,
    a_draft_run,
    a_meeting,
    a_motion_block,
    a_packet,
    a_spoken_transcript,
    a_transcript,
    a_video,
    its_lines,
    read,
    stored_pages,
)

#: The method the reading writes on every label it makes. The test pins it
#: rather than importing the module's own name for two reasons: a reader of this
#: file should see what is stored without opening another one, and a rename
#: inside the reading should fail here rather than agree with itself.
METHOD = "jaro_winkler_phonetic"

#: The score a spoken name has to reach to be written as an official, and how
#: far clear of the next closest official it has to be. Both are pinned for the
#: same reason as the method.
CUTOFF = 0.72
MARGIN = 0.05

#: The words the chair puts in front of a name when she hands the floor over.
CHAIR = "Council member"


def a_seated_body(
    sync: Sync,
    area: Area,
    *,
    names: tuple[str, ...] = COUNCIL,
    title: str = "Council Member",
    since: str = "2026-01-01",
) -> dict[str, int]:
    """Seat the named officials on the test's body from a date before the meeting.

    The rows are written the way the reading of the minutes writes them, so a
    test that does not care where the seats came from still reads them the way
    the reading does.
    """
    seated: dict[str, int] = {}
    for name in names:
        person_id = upsert_person(sync.conn, name=name)
        upsert_seat(sync.conn, person_id=person_id, body_id=area.body_id, title=title, on=since)
        seated[name] = person_id
    return seated


def a_transcript_of(
    sync: Sync,
    storage_root: Path,
    area: Area,
    lines: tuple[str, ...],
    *,
    starts_at: str = SEPT_8,
) -> tuple[int, int]:
    """Store a meeting, its primary video and a transcript of one line per second.

    The lines are written here rather than read out of a caption file, so a test
    can hold the reading to a line it knows: what the chair said, where the
    captioner marked the change of speaker, and nothing else.
    """
    meeting_id = a_meeting(sync, area, starts_at=starts_at)
    video_id = a_video(sync, area, meeting_id)
    transcript_id = a_spoken_transcript(sync, storage_root, video_id, lines)
    return meeting_id, transcript_id


def speak(sync: Sync, meeting_id: int) -> int:
    """Queue the speaker reading of one meeting and run the lane it is on."""
    job_id = sync.queue(READ_SPEAKERS, SpeakersRequest(meeting_id).as_payload())
    sync.lane("normal")
    return job_id


def line_of(sync: Sync, transcript_id: int, text: str) -> Segment:
    """The one stored line of a transcript whose text is this, or a failure."""
    found = [segment for segment in segments_of(sync.conn, transcript_id) if segment.text == text]
    assert len(found) == 1, f"the transcript should have exactly one line {text!r}"
    return found[0]


# -- Check 1: a caption's spelling of a name ----------------------------------


def test_a_caption_spelling_of_a_name_is_read_as_the_official_it_sounds_like(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """The chair names "Marcen", and the reading writes Jake Marsing.

    The captioner heard the name and wrote what she heard. The body's own
    records, the minutes, spell it "Jake Marsing", and the reading is between
    the two: the sounds of the name are what is compared, so a caption file that
    misspells a name is still a caption file that names somebody.
    """
    seated = a_seated_body(sync, area)
    meeting_id, transcript_id = a_transcript_of(
        sync,
        storage_root,
        area,
        (
            f"Good evening, everyone. {CHAIR} Marcen,",
            ">> could you tell us about the permit?",
            ">> The permit was issued on June third.",
        ),
    )

    job_id = speak(sync, meeting_id)

    assert sync.job(job_id)["state"] == DONE
    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    assert len(labels) == 1, "the chair named one person, and the last run labels nobody"
    label = labels[0]
    assert label.spoken_name == "Marcen", "the words the captioner wrote are kept"
    assert label.title_kind == "member"
    assert label.person_id == seated["Jake Marsing"]
    assert label.candidate_person_id == seated["Jake Marsing"]
    assert label.method == METHOD
    assert label.score >= CUTOFF, "the spelling was close enough to be written as a name"
    assert label.confirmed_by is None, "nothing else has been read against this label yet"
    assert label.conflict is None

    named = line_of(sync, transcript_id, ">> could you tell us about the permit?")
    assert named.speaker_label == "Jake Marsing", "the run's lines carry the official's name"
    chair = line_of(sync, transcript_id, f"Good evening, everyone. {CHAIR} Marcen,")
    assert chair.speaker_label is None, "the chair was not named, so her own lines have no name"


def test_a_name_near_two_officials_is_not_written_as_either(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """A score that reaches the cutoff but is not clear of the runner-up stays a guess.

    Two officials of one body can sound the same, and a reading that took the
    closer of the two would be picking between them rather than recognising one.
    The margin is what refuses that, and this is the case it exists for.
    """
    a_seated_body(sync, area, names=("Sean McCoy", "Sean McCaw"))
    meeting_id, transcript_id = a_transcript_of(
        sync,
        storage_root,
        area,
        (f"{CHAIR} Sean McCoy,", ">> I have a question about the budget."),
    )

    speak(sync, meeting_id)

    label = speaker_labels_of_meeting(sync.conn, meeting_id)[0]
    assert label.spoken_name == "Sean McCoy"
    assert label.score >= CUTOFF, "the name reached the cutoff and was still not written"
    assert label.person_id is None, "two officials sound like it, so it is neither of them"
    assert label.candidate_person_id is not None, "the closest official is kept as the guess"
    speaking = line_of(sync, transcript_id, ">> I have a question about the budget.")
    assert speaking.speaker_label is None
    assert speaker_label_of_segment(sync.conn, speaking.id) is not None, "the reading is kept"


# -- Check 2: an unidentified speaker ----------------------------------------


def test_a_name_that_matches_nobody_stays_an_unidentified_speaker(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """The chair names somebody the body has no seat for, and no name is written.

    "Zabrowski" sounds like nobody on this council, and the reading says so
    rather than writing the nearest name it can find. The words it heard and the
    closest official with its score are kept beside the line, which is what a
    reader needs to see that the reading looked and was not sure (spec 10.6).
    """
    a_seated_body(sync, area)
    meeting_id, transcript_id = a_transcript_of(
        sync,
        storage_root,
        area,
        (f"{CHAIR} Zabrowski,", ">> I object to the fee."),
    )

    speak(sync, meeting_id)

    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    assert len(labels) == 1
    label = labels[0]
    assert label.spoken_name == "Zabrowski", "the raw caption words are kept"
    assert label.person_id is None, "a name below the cutoff is not written as a name"
    assert label.score < CUTOFF, "the guess stayed below the cutoff"
    assert label.candidate_person_id is not None, "the best candidate and its score are kept"
    assert get_person(sync.conn, label.candidate_person_id) is not None

    speaking = line_of(sync, transcript_id, ">> I object to the fee.")
    assert speaking.speaker_label is None, "spec 10.6 shows this as an unidentified speaker"
    assert speaker_label_of_segment(sync.conn, speaking.id) is not None, "the reading is kept"


def test_a_run_the_chair_named_nobody_for_is_not_labelled(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """A run with no naming in front of it keeps a NULL label rather than a guess.

    The chair does not name everybody, and a reading that carried the last name
    forward would put words in somebody's mouth.
    """
    a_seated_body(sync, area)
    meeting_id, transcript_id = a_transcript_of(
        sync,
        storage_root,
        area,
        (
            f"{CHAIR} Popkin,",
            ">> The fee is fifty dollars.",
            ">> I would like to ask about the timeline.",  # nobody was named for this
            "It is three months.",
        ),
    )

    speak(sync, meeting_id)

    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    assert len(labels) == 1, "only the run the chair named has a label"
    unnamed = line_of(sync, transcript_id, ">> I would like to ask about the timeline.")
    assert unnamed.speaker_label is None
    assert speaker_label_of_segment(sync.conn, unnamed.id) is None


# -- Check 3: no person is created from a caption ----------------------------


def test_a_caption_never_creates_a_person(area: Area, sync: Sync, storage_root: Path) -> None:
    """The people are the ones the minutes name, and the captions add nobody.

    The minutes of a meeting record who voted on its motions, so a name in a
    vote list is a person a record establishes. A caption file is a captioner's
    spelling of a sound, and a misspelled one is a spelling rather than a
    person: the reading labels the lines with a seated official or with nobody,
    and the ``people`` table is left exactly as the minutes wrote it.
    """
    meeting_id = a_meeting(sync, area)
    follow = a_meeting(sync, area, starts_at=SEPT_22)
    run = a_draft_run(
        [
            *HEAD,
            "Mayor Pro Tem McCoy",
            *a_motion_block(
                "Matthew Popkin",
                "Diane Crist",
                "to amend O-2026-58, Section 2.95.050.F",
            ),
        ]
    )
    a_packet(sync, storage_root, area, follow, stored_pages(*run.pages))

    video_id = a_video(sync, area, meeting_id)
    a_spoken_transcript(
        sync,
        storage_root,
        video_id,
        (
            f"{CHAIR} Marcen,",
            f">> {CHAIR} Poppin,",
            f">> {CHAIR} Marcy,",
            ">> The permit was issued on June third.",
        ),
    )

    read(sync, meeting_id)  # the minutes seat the body, and ask for the speakers

    jobs = sync.jobs_of_kind(READ_SPEAKERS)
    assert len(jobs) == 1, "the reading of the minutes asked for the speakers of the meeting"
    assert sync.job(int(jobs[0]["id"]))["state"] == DONE
    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    assert [label.spoken_name for label in labels] == ["Marcen", "Poppin", "Marcy"]
    assert all(label.person_id is not None for label in labels), "all three were recognised"

    names = sorted(row[0] for row in sync.conn.execute("SELECT name FROM people"))
    assert names == sorted(COUNCIL), "the caption spellings created nobody"
    assert "Marcen" not in names and "Poppin" not in names and "Marcy" not in names

    titles = {
        str(row[0]): str(row[1])
        for row in sync.conn.execute(
            "SELECT people.name, seats.title FROM seats JOIN people ON people.id = seats.person_id"
        )
    }
    assert titles["Sean McCoy"] == "Mayor Pro Tem", "the minutes printed that title for him"
    assert titles["Matthew Popkin"] == "Council Member", "a vote list names a council member"
    assert len(titles) == len(COUNCIL)


# -- Check 4: where a label starts and stops ---------------------------------


def test_a_label_runs_from_one_speaker_change_to_the_line_before_the_next(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """The ``>>`` line is the first line of the run it begins, not the last of the one it ends.

    A captioner marks the change where the new speaker starts, so the line
    carrying the mark belongs to the person who is about to talk, and it runs to
    the line before the next mark. Nothing is taken out of the text (spec 10.1):
    the mark stays where the captioner wrote it, which is why the label has to
    start there rather than after it.
    """
    seated = a_seated_body(sync, area)
    meeting_id, transcript_id = a_transcript_of(
        sync,
        storage_root,
        area,
        (
            f"{CHAIR} Popkin,",
            ">> The fee is fifty dollars.",
            "And it starts on January first.",
            f">> {CHAIR} Christ,",
            ">> I would like to ask about the timeline.",
            "It is three months.",
        ),
    )

    speak(sync, meeting_id)

    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    assert len(labels) == 2
    first, second = labels
    assert first.start_segment_id == line_of(sync, transcript_id, ">> The fee is fifty dollars.").id
    assert (
        first.end_segment_id == line_of(sync, transcript_id, "And it starts on January first.").id
    )
    assert first.person_id == seated["Matthew Popkin"]
    assert (
        second.start_segment_id
        == line_of(sync, transcript_id, ">> I would like to ask about the timeline.").id
    )
    assert second.end_segment_id == line_of(sync, transcript_id, "It is three months.").id
    assert second.person_id == seated["Diane Crist"]

    chair = line_of(sync, transcript_id, f"{CHAIR} Popkin,")
    assert chair.speaker_label is None, "the run the naming is in is the chair's own"
    marking = line_of(sync, transcript_id, f">> {CHAIR} Christ,")
    assert marking.speaker_label is None, "the chair's naming is not the named person's line"


# -- Check 5: two records that agree, and two that do not --------------------


def a_motion_on(
    sync: Sync, meeting_id: int, item_id: int, *, mover: str, seconder: str, record_id: int
) -> int:
    """Store one motion of a meeting's minutes, linked to one item of its agenda.

    The motion names the record it was read out of, which is the packet whose
    draft minutes hold it, so a test stores a packet first the way the meeting
    itself would have one.
    """
    motion_id = insert_motion(
        sync.conn,
        meeting_id=meeting_id,
        record_id=record_id,
        page_number=17,
        ordinal=1,
        mover=mover,
        seconder=seconder,
        text="to amend O-2026-58, Section 2.95.050.F",
        result="passed",
        outcome="Carried: 7 – 0",
        evidence=f"{mover} moved, seconded by {seconder}, to amend O-2026-58",
    )
    insert_motion_item(
        sync.conn,
        motion_id=motion_id,
        agenda_item_id=item_id,
        link_kind="item_number",
        evidence="the motion names the ordinance of this item",
    )
    update_agenda_item_alignment(
        sync.conn,
        item_id,
        start_ms=4000,
        end_ms=7000,
        alignment_method="spoken_transitions",
        alignment_reason="the item's stretch of the transcript, measured here",
    )
    return motion_id


#: The words the chair uses to summarize a motion she has just put. Both the
#: mover and the seconder are named in the one sentence, which is the case that
#: tells a reading of the person from a reading of the line that follows.
CHAIRS_SUMMARY = (
    f"the motion has been made by {CHAIR.lower()} Popkin, seconded by {CHAIR.lower()} Christ"
)

#: The transcript the cross-check tests read: each of the two speaks before the
#: chair names them both, which is how a meeting runs.
CROSS_CHECKED = (
    f">> {CHAIR} Popkin.",
    ">> The fee is fifty dollars.",
    f">> {CHAIR} Christ.",
    ">> I would like to ask about it.",
    f">> {CHAIRS_SUMMARY}.",
    ">> All in favor? The motion carries.",
)


def test_a_motion_both_records_agree_on_is_marked_confirmed_by_the_minutes(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """Part D of the skill: two records naming one person is what either can say alone.

    The chair says who moved and who seconded the motion, and the minutes of the
    same meeting record the same two people for it. The labels on the lines
    where those two spoke are marked as confirmed by the minutes rather than
    left as one record's word.
    """
    seated = a_seated_body(sync, area)
    meeting_id, transcript_id = a_transcript_of(sync, storage_root, area, CROSS_CHECKED)
    item_id = insert_agenda_item(
        sync.conn,
        meeting_id=meeting_id,
        number="9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    packet_id = a_packet(sync, storage_root, area, meeting_id, [(17, "draft minutes")])
    a_motion_on(
        sync,
        meeting_id,
        item_id,
        record_id=packet_id,
        mover="Matthew Popkin",
        seconder="Diane Crist",
    )

    job_id = speak(sync, meeting_id)

    assert sync.job(job_id)["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["motions_checked"] == 1, "the motion's item has a time range"
    assert checkpoint["confirmed"] == 2, "the mover and the seconder were both read"
    assert checkpoint["conflicts"] == 0

    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    by_spoken = {label.spoken_name: label for label in labels}
    assert by_spoken["Popkin"].person_id == seated["Matthew Popkin"]
    assert by_spoken["Christ"].person_id == seated["Diane Crist"]
    assert by_spoken["Popkin"].confirmed_by == "minutes", "a second record agrees"
    assert by_spoken["Christ"].confirmed_by == "minutes"
    assert by_spoken["Popkin"].conflict is None
    assert by_spoken["Christ"].conflict is None

    # The confirmation is on the label of the person the chair named, not on the
    # run after the summary: the chair named two people in one breath, and only
    # one of them speaks next.
    assert (
        by_spoken["Popkin"].end_segment_id
        == line_of(sync, transcript_id, ">> The fee is fifty dollars.").id
    )


def test_a_motion_the_two_records_disagree_on_keeps_both_names(
    area: Area, sync: Sync, storage_root: Path
) -> None:
    """The captions name one mover and the minutes another, and neither is dropped.

    Spec 10.4 shows both records when they disagree rather than picking one. The
    label keeps the name the captions gave, and the conflict holds the sentence
    saying what the minutes say instead.
    """
    a_seated_body(sync, area)
    meeting_id, _ = a_transcript_of(sync, storage_root, area, CROSS_CHECKED)
    item_id = insert_agenda_item(
        sync.conn,
        meeting_id=meeting_id,
        number="9.B",
        title="O-2026-58, A Bill For An Ordinance Amending Title 2 Of The Code",
    )
    packet_id = a_packet(sync, storage_root, area, meeting_id, [(17, "draft minutes")])
    a_motion_on(
        sync,
        meeting_id,
        item_id,
        record_id=packet_id,
        mover="Diane Crist",
        seconder="Matthew Popkin",
    )

    job_id = speak(sync, meeting_id)

    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["motions_checked"] == 1
    assert checkpoint["confirmed"] == 0
    assert checkpoint["conflicts"] == 2, "the caption's mover and its seconder both disagree"

    labels = speaker_labels_of_meeting(sync.conn, meeting_id)
    by_spoken = {label.spoken_name: label for label in labels}
    spoken = by_spoken["Popkin"]
    assert spoken.person_id is not None, "the captions' name is kept"
    assert spoken.conflict is not None
    assert "Diane Crist" in spoken.conflict, "the sentence says what the minutes name"
    assert spoken.confirmed_by is None, "a disagreement is not a confirmation"
    assert by_spoken["Christ"].conflict is not None


# -- The real recordings ------------------------------------------------------

#: What the reading of the recorded meeting found. Every number was measured on
#: this machine from the recorded bytes, and none of them was written down
#: before it was measured.
MEASURED_SEGMENTS = 5787
MEASURED_LABELS = 58
MEASURED_IDENTIFIED = 52
MEASURED_UNIDENTIFIED = 6
MEASURED_PLACED_ITEMS = 28
MEASURED_MOTIONS_CHECKED = 14
MEASURED_CONFIRMED = 7
MEASURED_CONFLICTS = 8

#: How many labels the disagreements landed on. One label can be claimed twice,
#: as the mover's run of one motion and the seconder's run of another, so the
#: eight disagreements of this meeting are kept on seven labels: the second one
#: is written beside the first rather than over it.
MEASURED_CONFLICT_LABELS = 7

#: How many lines the reading wrote each official's name on. The Mayor is not
#: here: the chair is the one who names the others, and she names herself
#: nowhere, so her own lines keep the NULL label spec 10.6 shows.
MEASURED_PER_PERSON = {
    "Matthew Popkin": 18,
    "Crystal Prieto": 10,
    "Diane Crist": 10,
    "Alex Kalkhofer": 7,
    "Jake Marsing": 6,
    "Sean McCoy": 1,
}

#: Every way the captioner wrote a name that the reading of this meeting kept,
#: and the official it was read as, or None for the lines it refused to name.
#: It is a table of what the reading did rather than of what it should do: the
#: two readings marked wrong below are wrong, and they are kept in the table so
#: that the test says so out loud instead of hiding them.
MEASURED_SPELLINGS = {
    "Brietto": "Crystal Prieto",
    "Brito": "Crystal Prieto",
    "Calcoffer": None,
    "Chris": "Diane Crist",
    "Chris is coming": "Diane Crist",
    "Christ": "Diane Crist",
    "Christ Thank you": "Diane Crist",
    "Christ Yes": "Diane Crist",
    "Coloffer": "Alex Kalkhofer",
    "Colopper": "Alex Kalkhofer",
    "I want to": None,
    "Koffer": "Alex Kalkhofer",
    "Koffer has seconded": None,
    "Marcen": "Jake Marsing",
    "Marcy": "Jake Marsing",
    "Marzang to say": "Jake Marsing",
    "PTO bringing this": None,
    "Popkin": "Matthew Popkin",
    "Popkin And this": "Matthew Popkin",
    "Popkin I think": "Matthew Popkin",
    "Popkin Yes Go": "Matthew Popkin",
    "Popkins still has": "Matthew Popkin",
    "Port McCoy": None,
    "Prito": "Crystal Prieto",
    "Prito All right": "Crystal Prieto",
    "Prito in opposition": "Crystal Prieto",
    "Prito would uh": "Crystal Prieto",
    "Prom": "Crystal Prieto",  # wrong: this is the captioner's "Pro Tem"
    "Prom McCoy in": "Sean McCoy",  # a surname inside a garbled title, read right
    "Sandy Cedar": None,
}

#: The scores behind the spellings a reader would ask about: the readings that
#: cleared both bars, the one that cleared the cutoff and not the margin, and
#: the lowest score the reading still wrote as a name. The reading is pinned
#: rather than described because these are the numbers the cutoff and the
#: margin were chosen against.
MEASURED_SCORES = {
    "Marcen": 0.96,
    "Marcy": 0.9067,
    "Marzang to say": 1.0,
    "Coloffer": 0.7333,
    "Brietto": 0.7222,
    "Koffer": 0.805,
    "Prom McCoy in": 1.0,
    "Port McCoy": 1.0,  # cleared the cutoff, held back by the margin
    "Calcoffer": 0.8222,  # the same, against a closer runner-up
    "Prom": 0.8222,  # cleared both bars and is wrong
}

#: The reading of the recorded meeting that is wrong, named here so the report
#: and the test agree about it. The captioner writes "Prom." for "Mayor Pro
#: Tem", and the reading of the chair's summary takes the words after the title
#: "Mayor" as a name, so one run of Crystal Prieto's lines carries the name of
#: the Mayor Pro Tem. It clears both bars, which is why the score and not just
#: the cutoff is what tells a reader to look: 0.8222, the same score "Calcoffer"
#: got and was refused for, held back there by a closer runner-up.
MEASURED_WRONG = ("Prom",)


@needs(AGENDA_16805, CAPTIONS_16805, PACKET_MINUTES_SEP08)
def test_the_real_september_8_speakers_read_the_way_the_captions_hold_them(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The measured caption track and the measured packet, read end to end.

    The recording is the September 8, 2026 caption track of the meeting and the
    September 22 packet's pages 17 to 38, which hold that meeting's draft
    minutes. What the reading is held to here is what it found: the runs and the
    names on them, every spelling the captioner used and the official it was
    read as, and the motions the two records could be put against each other on.
    """
    wired.html_agenda = AGENDA_16805.read_bytes()
    meeting_id = a_listed_meeting(sync, area)
    video_id = its_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_16805.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_16805.read_bytes())
    align_id = align(sync, meeting_id, area.portal_id)
    assert sync.job(align_id)["state"] == DONE
    placed = [item for item in agenda_items(sync.conn, meeting_id) if item.start_ms is not None]
    assert len(placed) == MEASURED_PLACED_ITEMS, "the cross-check reads the items that were placed"

    follow = a_meeting(sync, area, starts_at=SEPT_22)
    reader = PdfReader(PACKET_MINUTES_SEP08)
    pages = [(17 + index, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    a_packet(sync, storage_root, area, follow, pages, data=PACKET_MINUTES_SEP08.read_bytes())

    minutes_id = read(sync, meeting_id)  # the minutes seat the body, and ask for the speakers
    assert sync.job(minutes_id)["state"] == DONE
    assert len(motions_of_meeting(sync.conn, meeting_id)) == 18, "the motions being checked"

    jobs = sync.jobs_of_kind(READ_SPEAKERS)
    assert len(jobs) == 1, "the minutes reading asked for the speakers once"
    job_id = int(jobs[0]["id"])
    assert sync.job(job_id)["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, job_id)

    assert checkpoint["segments"] == MEASURED_SEGMENTS
    assert checkpoint["labels"] == MEASURED_LABELS
    assert checkpoint["identified"] == MEASURED_IDENTIFIED
    assert checkpoint["unidentified"] == MEASURED_UNIDENTIFIED
    assert checkpoint["labels"] == checkpoint["identified"] + checkpoint["unidentified"]
    assert len(speaker_labels_of_meeting(sync.conn, meeting_id)) == MEASURED_LABELS

    per_person = Counter(
        str(row[0])
        for row in sync.conn.execute(
            "SELECT people.name FROM speaker_labels JOIN people ON people.id = "
            "speaker_labels.person_id WHERE speaker_labels.meeting_id = ?",
            (meeting_id,),
        )
    )
    assert per_person == Counter(MEASURED_PER_PERSON)
    assert sum(per_person.values()) == MEASURED_IDENTIFIED

    spellings = {
        str(row[0]): None if row[1] is None else str(row[1])
        for row in sync.conn.execute(
            "SELECT speaker_labels.spoken_name, people.name "
            "FROM speaker_labels LEFT JOIN people ON people.id = speaker_labels.person_id "
            "WHERE speaker_labels.meeting_id = ?",
            (meeting_id,),
        )
    }
    assert spellings == MEASURED_SPELLINGS, "every spelling and what it was read as"

    scores = {
        str(row[0]): round(float(row[1]), 4)
        for row in sync.conn.execute(
            "SELECT spoken_name, score FROM speaker_labels WHERE meeting_id = ?",
            (meeting_id,),
        )
    }
    assert set(MEASURED_SCORES) <= set(scores), "every pinned score is a score of this meeting"
    for spelling, score in MEASURED_SCORES.items():
        assert scores[spelling] == score, f"the score behind {spelling}"

    assert checkpoint["motions_checked"] == MEASURED_MOTIONS_CHECKED
    assert checkpoint["confirmed"] == MEASURED_CONFIRMED
    assert checkpoint["conflicts"] == MEASURED_CONFLICTS

    rows = speaker_labels_of_meeting(sync.conn, meeting_id)
    labels = {one.spoken_name: one for one in rows}

    # A label the two records agreed on is marked as confirmed by the minutes,
    # and it is a name rather than a NULL: two records naming one person is what
    # spec 10.6 makes the strongest label there is.
    confirmed = [one for one in rows if one.confirmed_by == "minutes"]
    assert len(confirmed) == MEASURED_CONFIRMED, "two records named these people"
    assert all(one.person_id is not None for one in confirmed)

    # The two readings of this meeting that are wrong are named here and are
    # still written as names. The test says so rather than leaving a reader to
    # find them: the captioner writes "Prom." for "Mayor Pro Tem", and the
    # reading of the chair's summary goes on to read the words after the title
    # "Mayor" as if they were a name.
    for spoken in MEASURED_WRONG:
        assert labels[spoken].person_id is not None, f"{spoken} was written as a name"

    conflicts = [
        one.conflict
        for one in sorted(rows, key=lambda label: label.start_segment_id)
        if one.conflict is not None
    ]
    # A disagreement is kept rather than resolved, and a label the minutes
    # disagree with twice keeps both sentences: one of these labels is the run
    # of a motion's mover and of another motion's seconder.
    assert len(conflicts) == MEASURED_CONFLICT_LABELS
    kept = [line for cell in conflicts for line in cell.splitlines()]
    assert len(kept) == MEASURED_CONFLICTS, "every disagreement is kept, not just the last"
    assert all("minutes name" in sentence for sentence in kept)
    assert all(sentence.endswith(".") for sentence in kept)
