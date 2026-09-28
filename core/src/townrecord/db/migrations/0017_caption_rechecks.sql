-- Caption revisions, the rechecks that find them, and what a revision raises
-- (spec 8.7).
--
-- Spec 8.7 makes a capture provisional for 24 hours after the meeting ends and
-- says it is rechecked every 3 hours during that time. That is a schedule the
-- daily schedule of spec 16.2 cannot keep: it runs once per local day, so a
-- capture taken at 21:00 would get its first recheck tomorrow. The recheck is
-- its own job kind instead, and it defers itself to the next check rather than
-- sleeping (spec 16.1).
--
-- Three tables, because three different things have to survive a restart.
--
-- `caption_checks` is what each recheck saw: the caption hash, the caption
-- revision time and the duration of the source at that moment. The 24 hour
-- window is counted from the *first* capture, which is a row of `transcripts`,
-- but "two unchanged checks at least an hour apart" is a fact about the last
-- two checks and nothing else, so the checks are rows rather than a pair of
-- columns somewhere. `transcript_id` is the version that was in hand when the
-- check was taken, and `changed` says whether this check found a change, which
-- is what "settled under churn" is read from at the 48 hour backstop.
--
-- `review_items` is the one item a revision raises for the user. Spec 8.7 says
-- a revision "raises one review item", so "one" is a constraint rather than a
-- habit: the UNIQUE key on (kind, subject) makes a second raise of the same
-- revision write no second row. `subject` is the identity of what the item is
-- about -- one video and one changed set of caption bytes -- and the sentence
-- is stored whole, because a user reads it rather than composing it.
--
-- `rerun_marks` is the alignment and the votes a revision puts back in doubt.
-- Spec 8.7 says a revision never rewrites anything already exported and that
-- what used an older version is marked for a rerun, so the mark is a row that
-- says what is stale and why, and nothing is changed in place. It points at
-- (kind, row_id) rather than at a foreign key per kind, because three tables
-- are marked and one column holding three meanings is what a CHECK is for.

CREATE TABLE caption_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos (id) ON DELETE RESTRICT,
    -- The transcript version that was in hand when this check was taken. The
    -- check compares the source against this version, so the row says which
    -- version an "unchanged" answer is about.
    transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE RESTRICT,
    -- When the check ran, as UTC. This is the time the 1 hour rule of spec 8.7
    -- is measured between, so it is the moment of the check and not the moment
    -- it was written.
    checked_at TEXT NOT NULL,
    changed INTEGER NOT NULL DEFAULT 0 CHECK (changed IN (0, 1)),
    -- Which of the three signals of spec 8.7 differed, or NULL when none did.
    -- The order of the test is fixed by the spec -- hash, then revision time,
    -- then duration -- so at most one is named.
    what_changed TEXT CHECK (what_changed IN ('hash', 'revision_at', 'duration_s')),
    -- What the source said at this moment, so the next check has a baseline
    -- that does not depend on the caption artifact still being readable.
    observed_sha256 TEXT,
    observed_revision_at TEXT,
    observed_duration_s REAL,
    -- Why the check could not answer, when it could not. A check that failed is
    -- still a row: a silent gap is what spec 16.3 forbids, and `changed` 0 with
    -- a note is not the same answer as an unchanged source.
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (changed = 0 OR what_changed IS NOT NULL),
    CHECK (changed = 1 OR what_changed IS NULL)
);

-- The last two checks of one video, which is the whole of the 1 hour rule.
CREATE INDEX caption_checks_video_time ON caption_checks (video_id, checked_at);

CREATE TABLE review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- What kind of item this is. 'caption_revision' today; the column is what
    -- lets a second kind of item exist without a second table.
    kind TEXT NOT NULL CHECK (length(kind) > 0),
    -- The identity of the thing under review, unique per kind. One revision of
    -- one video is one item however many times the raise is attempted.
    subject TEXT NOT NULL CHECK (length(subject) > 0),
    meeting_id INTEGER REFERENCES meetings (id) ON DELETE RESTRICT,
    video_id INTEGER REFERENCES videos (id) ON DELETE RESTRICT,
    -- The sentence the user reads, stored whole rather than composed from the
    -- columns above: what changed and when is a fact of the moment it was
    -- found, and a reader should not have to re-derive it.
    sentence TEXT NOT NULL CHECK (length(sentence) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (kind, subject)
);

CREATE TABLE rerun_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 'alignment' is a row of meeting_alignments, 'motion' one of motions,
    -- 'speakers' one of speaker_labels. Each was produced from a transcript
    -- version that a revision replaced.
    kind TEXT NOT NULL CHECK (kind IN ('alignment', 'motion', 'speakers')),
    row_id INTEGER NOT NULL,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    -- Why the row is stale, in the words of the revision that staled it.
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- A revision that runs twice marks the same row once.
    UNIQUE (kind, row_id, reason)
);
