-- Who is speaking (spec 10.6).
--
-- An srv3 caption file marks a change of speaker with ">>" and names nobody.
-- The chair names the next speaker in the lines just before such a change, and
-- this table keeps what reading that found: one row for each run of lines the
-- chair's words give a speaker to, the words she said, the official they were
-- read as, and how close the reading was.
--
-- Two things are kept that a settled label does not need. `spoken_name` is the
-- chair's words exactly as the captioner spelled them, "Marcen" for Jake
-- Marsing, so a reader sees that the caption is wrong rather than that the
-- person is. `candidate_person_id` with `score` is the best match of a reading
-- that did not pass the cutoff: spec 10.6 shows such a run as "an unidentified
-- speaker", and a person can only confirm it later if the guess and the score
-- it was made with are still on the row.
--
-- `person_id` NULL is that unidentified speaker, and the lines of the run keep
-- a NULL speaker_label. `method` names the way the score was reached, because a
-- score with no method is not a fact a later reader can check. A row with
-- `person_id` and `confirmed_by` both set is a label two records agree on: the
-- minutes name the same person as the mover or seconder of the motion the run
-- is about. `conflict` is the other case, and it holds the two names in a
-- sentence rather than a flag, so a reader is told what the records say instead
-- of being told that they differ. One label can disagree about two motions, so
-- the column holds every sentence, one per line.
--
-- One row per run of one transcript, which the UNIQUE key is, so a reading that
-- runs twice writes one set of rows.

CREATE TABLE speaker_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE RESTRICT,
    -- The run: the lines from the speaker change this label starts at through
    -- the line before the next change.
    start_segment_id INTEGER NOT NULL REFERENCES segments (id) ON DELETE RESTRICT,
    end_segment_id INTEGER NOT NULL REFERENCES segments (id) ON DELETE RESTRICT,
    -- The chair's words for the speaker, as the captioner wrote them.
    spoken_name TEXT NOT NULL CHECK (length(spoken_name) > 0),
    -- The title the chair used, which is what the recognition was found by.
    title_kind TEXT NOT NULL CHECK (title_kind IN ('member', 'mayor_pro_tem', 'mayor')),
    person_id INTEGER REFERENCES people (id) ON DELETE RESTRICT,
    candidate_person_id INTEGER REFERENCES people (id) ON DELETE RESTRICT,
    score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
    method TEXT NOT NULL CHECK (length(method) > 0),
    evidence TEXT NOT NULL CHECK (length(evidence) > 0),
    -- Which other record agrees with this label. Only the minutes can agree so
    -- far, and the column says which record rather than that one did.
    confirmed_by TEXT CHECK (confirmed_by IS NULL OR confirmed_by IN ('minutes')),
    conflict TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (transcript_id, start_segment_id),
    CHECK (confirmed_by IS NULL OR person_id IS NOT NULL)
);

CREATE INDEX speaker_labels_meeting_idx ON speaker_labels (meeting_id, start_segment_id);
CREATE INDEX speaker_labels_person_idx ON speaker_labels (person_id);
