-- The core data model (spec 6.2): the area, its people, the places it
-- publishes, the meetings, and everything the cross-reference layer cites.
--
-- Time columns follow one rule, and every time column says which half it is:
--   * a column the database fills itself (`created_at`) is UTC, ISO 8601,
--     written with strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
--   * a column that records what a publisher said (`starts_at`,
--     `published_at`, the dates on a seat) is the local time as published,
--     ISO 8601, with the UTC offset when the source gives one, because the
--     schedule runs in the area's own time zone (spec 16.2).
--
-- Delete policy, the same in every table here: no delete cascades. A row that
-- depends on another refuses the delete with ON DELETE RESTRICT, so no
-- capture, transcript or page can disappear as a side effect of removing a
-- parent row. Deleting is always an explicit decision, in order, and the
-- repository layer is the only place that does it.

-- ------------------------------------------------------------------ area --

CREATE TABLE jurisdictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (
        type IN ('state', 'county', 'city', 'school_district', 'special_district', 'federal')
    ),
    name TEXT NOT NULL CHECK (length(name) > 0),
    -- The official identifier where one exists (spec 6.2): a Census FIPS or
    -- GEOID, an NCES district ID for a school district, or the state's
    -- local-government ID for a special district.
    official_id TEXT,
    official_id_kind TEXT CHECK (
        official_id_kind IS NULL
        OR official_id_kind IN ('fips', 'geoid', 'nces', 'state_local_government')
    ),
    parent_id INTEGER REFERENCES jurisdictions (id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- An identifier is only useful with the kind of identifier it is, so the
    -- two columns appear together or not at all.
    CHECK ((official_id IS NULL) = (official_id_kind IS NULL)),
    CHECK (parent_id IS NULL OR parent_id <> id),
    UNIQUE (type, name, parent_id)
);

-- A root jurisdiction has no parent, and SQLite treats NULLs as distinct in a
-- UNIQUE, so the roots get their own partial index. Two states named Colorado
-- are then refused as well.
CREATE UNIQUE INDEX jurisdictions_root_name_idx
    ON jurisdictions (type, name) WHERE parent_id IS NULL;

CREATE INDEX jurisdictions_parent_idx ON jurisdictions (parent_id);
CREATE INDEX jurisdictions_official_id_idx ON jurisdictions (official_id)
    WHERE official_id IS NOT NULL;

-- Two jurisdictions overlap when neither contains the other: a city in more
-- than one county, a school district across city and county lines (spec 6.1).
-- Overlapping is one fact, not two, so a pair is stored once and a trigger
-- writes the other direction. Deleting either direction deletes both.
CREATE TABLE jurisdiction_overlaps (
    jurisdiction_id INTEGER NOT NULL REFERENCES jurisdictions (id) ON DELETE RESTRICT,
    overlaps_id INTEGER NOT NULL REFERENCES jurisdictions (id) ON DELETE RESTRICT,
    PRIMARY KEY (jurisdiction_id, overlaps_id),
    CHECK (jurisdiction_id <> overlaps_id)
);

CREATE TRIGGER jurisdiction_overlaps_mirror_insert
AFTER INSERT ON jurisdiction_overlaps
BEGIN
    INSERT OR IGNORE INTO jurisdiction_overlaps (jurisdiction_id, overlaps_id)
    VALUES (NEW.overlaps_id, NEW.jurisdiction_id);
END;

CREATE TRIGGER jurisdiction_overlaps_mirror_delete
AFTER DELETE ON jurisdiction_overlaps
BEGIN
    DELETE FROM jurisdiction_overlaps
    WHERE jurisdiction_id = OLD.overlaps_id AND overlaps_id = OLD.jurisdiction_id;
END;

-- A body is a group that holds public meetings and belongs to one
-- jurisdiction (spec 6.2).
CREATE TABLE bodies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jurisdiction_id INTEGER NOT NULL REFERENCES jurisdictions (id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK (length(name) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (jurisdiction_id, name)
);

CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(name) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- A person is found by name, and two people may share one, so the name is
-- indexed and not unique (spec 10.6, where captions misspell names).
CREATE INDEX people_name_idx ON people (name);

-- A seat is one person holding one title on one body for a range of dates
-- (spec 6.2).
CREATE TABLE seats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people (id) ON DELETE RESTRICT,
    body_id INTEGER NOT NULL REFERENCES bodies (id) ON DELETE RESTRICT,
    title TEXT NOT NULL CHECK (length(title) > 0),
    start_date TEXT, -- local date as published, ISO 8601 (YYYY-MM-DD)
    end_date TEXT, -- local date as published, ISO 8601; NULL while it is held
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX seats_body_idx ON seats (body_id);
CREATE INDEX seats_person_idx ON seats (person_id);

-- A source is a place where something is published (spec 6.2). A suggestion
-- is a status with a reason and an origin, and a person accepts or rejects
-- it, so the person and the reason are required on every row: a source can
-- never appear without them.
CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jurisdiction_id INTEGER NOT NULL REFERENCES jurisdictions (id) ON DELETE RESTRICT,
    body_id INTEGER REFERENCES bodies (id) ON DELETE RESTRICT,
    type TEXT NOT NULL CHECK (
        type IN (
            'meeting_portal',
            'video_channel',
            'records_archive',
            'website_news',
            'legislature_system'
        )
    ),
    -- Where the source lives. Every adapter call takes its origin from here
    -- and never from a default city (spec 9.3).
    origin TEXT NOT NULL CHECK (length(origin) > 0),
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'accepted', 'rejected', 'broken')),
    suggested_by TEXT NOT NULL CHECK (length(suggested_by) > 0),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    -- Re-discovery (spec 7.3): a source that fails three times in a row
    -- becomes broken and the user sees the last error. The status change is
    -- code's job; the count and the error are kept here.
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_error TEXT,
    last_checked_at TEXT, -- UTC, written by the checker
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (type, origin)
);

CREATE INDEX sources_jurisdiction_idx ON sources (jurisdiction_id);
CREATE INDEX sources_status_idx ON sources (status);
CREATE INDEX sources_body_idx ON sources (body_id);

-- -------------------------------------------------------------- meetings --

-- One sitting of one body (spec 6.2). A cancelled or continued meeting is
-- still a meeting: it is the reason its minutes will never appear, which is
-- what the missing-record rule of spec 9.5 reads.
CREATE TABLE meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body_id INTEGER NOT NULL REFERENCES bodies (id) ON DELETE RESTRICT,
    title TEXT,
    -- The local start time as the body published it, ISO 8601, with the UTC
    -- offset when the source gives one.
    starts_at TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'regular'
        CHECK (type IN ('regular', 'study', 'special', 'executive')),
    is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK (is_cancelled IN (0, 1)),
    is_continued INTEGER NOT NULL DEFAULT 0 CHECK (is_continued IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (body_id, starts_at)
);

CREATE INDEX meetings_body_start_idx ON meetings (body_id, starts_at);

-- A recording on a video source (spec 6.2). Nothing here downloads anything.
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    -- A listing is kept before it is matched to a meeting (spec 7.2 step 7),
    -- so the meeting is optional.
    meeting_id INTEGER REFERENCES meetings (id) ON DELETE RESTRICT,
    platform_video_id TEXT NOT NULL CHECK (length(platform_video_id) > 0),
    -- The publisher's title, exactly as listed. A video is never renamed.
    title TEXT,
    url TEXT, -- the watch address the source published
    published_at TEXT, -- the publisher's listed time as published (feeds carry UTC)
    duration_s INTEGER CHECK (duration_s IS NULL OR duration_s >= 0), -- seconds
    -- Two channels record the same meeting and both are kept; one is primary
    -- by channel priority (spec 7.2 step 7). The partial unique index below
    -- is the rule.
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    -- How far the capture of this video has got. The spec does not name the
    -- states; these are the pipeline of sections 8.2 to 8.5 in order, and
    -- 'skipped' is the upcoming or live video that is left for a later pass.
    capture_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (capture_state IN ('pending', 'skipped', 'captions', 'audio', 'failed')),
    -- Whether the video is upcoming, live, finished, or not yet known
    -- (spec 8.2). 'unknown' means "waiting for status metadata".
    readiness TEXT NOT NULL DEFAULT 'unknown'
        CHECK (readiness IN ('upcoming', 'live', 'finished', 'unknown')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, platform_video_id)
);

-- At most one primary video per meeting. A video that is not matched to a
-- meeting yet has a NULL meeting_id, and SQLite treats NULLs as distinct, so
-- unmatched primaries are not covered by this index.
CREATE UNIQUE INDEX videos_one_primary_per_meeting_idx
    ON videos (meeting_id) WHERE is_primary = 1;

CREATE INDEX videos_meeting_idx ON videos (meeting_id);
CREATE INDEX videos_source_idx ON videos (source_id);

-- A document of a meeting (spec 6.2).
CREATE TABLE records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    -- The source the document came from, when the capture knew one. A record
    -- citation names its source (spec 10.5).
    source_id INTEGER REFERENCES sources (id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (
        kind IN ('agenda', 'packet', 'minutes', 'ordinance', 'resolution', 'budget', 'report')
    ),
    title TEXT,
    -- The file is always an artifact (spec 8.6). A record never holds a path
    -- of its own, so the hash of the bytes is the identity of the document.
    artifact_id INTEGER NOT NULL REFERENCES artifacts (id) ON DELETE RESTRICT,
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- A meeting may hold two packets (a first and a second reading), so the
    -- same kind twice is allowed; the same file twice is not.
    UNIQUE (meeting_id, kind, artifact_id)
);

CREATE INDEX records_meeting_idx ON records (meeting_id);
CREATE INDEX records_kind_idx ON records (kind);

CREATE TABLE record_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES records (id) ON DELETE RESTRICT,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    -- The number printed in the page footer. In the Longmont example it
    -- equals the PDF page number (spec 9.4); it is kept apart from the page
    -- number because that is not true everywhere.
    footer_page_number INTEGER CHECK (footer_page_number IS NULL OR footer_page_number >= 1),
    text TEXT NOT NULL DEFAULT '',
    UNIQUE (record_id, page_number)
);

-- ------------------------------------------------------------- transcripts --

CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos (id) ON DELETE RESTRICT,
    artifact_id INTEGER NOT NULL REFERENCES artifacts (id) ON DELETE RESTRICT,
    origin TEXT NOT NULL CHECK (
        origin IN ('publisher_captions', 'auto_captions', 'sister_channel', 'local_speech_to_text')
    ),
    -- A capture is provisional for 24 hours after the meeting ends and is
    -- rechecked during that time; a capture made more than 24 hours after the
    -- meeting is final at once (spec 8.7).
    is_provisional INTEGER NOT NULL DEFAULT 1 CHECK (is_provisional IN (0, 1)),
    -- At 48 hours a capture is settled anyway; if the text was still
    -- changing, this flag says so (spec 8.7).
    settled_under_churn INTEGER NOT NULL DEFAULT 0 CHECK (settled_under_churn IN (0, 1)),
    settled_at TEXT, -- UTC, when the capture stopped changing
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- A final capture says when it settled.
    CHECK (is_provisional = 1 OR settled_at IS NOT NULL),
    -- A revision is a new artifact and so a new row (spec 8.7); the same
    -- bytes twice are the same transcript.
    UNIQUE (video_id, artifact_id)
);

CREATE INDEX transcripts_video_idx ON transcripts (video_id);

-- The spoken lines of a transcript. There is NO agenda item column here, on
-- purpose: the item for a segment is resolved at read time from the time
-- ranges on `agenda_items` (spec 10.2, last paragraph). A second item column
-- would be written by no code path and would answer null on every row.
CREATE TABLE segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE RESTRICT,
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL CHECK (end_ms >= start_ms),
    text TEXT NOT NULL,
    -- The recognition of a speaker (spec 10.6). NULL means no speaker was
    -- identified, and the line shows as "an unidentified speaker".
    speaker_label TEXT
);

CREATE INDEX segments_transcript_start_idx ON segments (transcript_id, start_ms);

-- A numbered item of a meeting, with the time range in the video when one is
-- known (spec 6.2, 10.2).
CREATE TABLE agenda_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    number TEXT NOT NULL CHECK (length(number) > 0),
    title TEXT NOT NULL DEFAULT '',
    -- Ordinance and resolution numbers and other identifiers found in the
    -- item (spec 10.1), as a JSON object.
    identifiers TEXT NOT NULL DEFAULT '{}',
    start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0),
    end_ms INTEGER CHECK (end_ms IS NULL OR end_ms >= 0),
    -- Which of the three methods of spec 10.2 produced the boundary.
    alignment_method TEXT NOT NULL DEFAULT 'none'
        CHECK (alignment_method IN ('html_video_times', 'spoken_transitions', 'none')),
    -- The plain reason when no method worked (spec 10.2 method 3: "no spoken
    -- transcript transition matched a packet item"). It is not required by a
    -- CHECK, because an item exists as soon as the agenda is read and before
    -- alignment has run, so at that moment there is nothing yet to say.
    alignment_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (start_ms IS NULL OR end_ms IS NULL OR end_ms >= start_ms),
    -- A method that found a boundary always leaves a whole range: an item is
    -- never half aligned.
    CHECK (alignment_method = 'none' OR (start_ms IS NOT NULL AND end_ms IS NOT NULL)),
    -- And 'none' means no boundary was found, so it carries no range.
    CHECK (alignment_method <> 'none' OR (start_ms IS NULL AND end_ms IS NULL)),
    UNIQUE (meeting_id, number)
);

CREATE INDEX agenda_items_meeting_start_idx ON agenda_items (meeting_id, start_ms);

-- ---------------------------------------------------------------- evidence --

-- The result on an item, with its evidence and its source (spec 10.4).
CREATE TABLE votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agenda_item_id INTEGER NOT NULL REFERENCES agenda_items (id) ON DELETE RESTRICT,
    result TEXT NOT NULL
        CHECK (result IN ('passed', 'failed', 'tabled', 'withdrawn', 'unknown')),
    -- Where the vote came from, in the precedence order of spec 10.4: a
    -- structured record, the minutes, the packet, the transcript.
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('structured', 'minutes', 'packet', 'transcript')),
    -- The lines or the identifier that carry the vote. A vote never appears
    -- without its evidence.
    evidence TEXT NOT NULL CHECK (length(evidence) > 0),
    -- The counted result as JSON (yes, no, abstain). A transcript-only
    -- mention is never a tally (spec 10.4), so it stays empty for one.
    tally TEXT CHECK (tally IS NULL OR source_kind <> 'transcript'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- When sources disagree, all of them are shown and none is picked
    -- (spec 10.4), so an item has one row per source kind.
    UNIQUE (agenda_item_id, source_kind)
);

-- A pointer to evidence (spec 10.5). The two shapes are the video citation
-- and the record citation, and the CHECK constraints below keep each shape
-- whole. The SHA-256 of the cited artifact is read from the `artifacts` row
-- and never copied here: an artifact never changes, so its row is the hash.
CREATE TABLE citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'record')),
    video_id INTEGER REFERENCES videos (id) ON DELETE RESTRICT,
    transcript_id INTEGER REFERENCES transcripts (id) ON DELETE RESTRICT,
    record_id INTEGER REFERENCES records (id) ON DELETE RESTRICT,
    source_id INTEGER REFERENCES sources (id) ON DELETE RESTRICT,
    artifact_id INTEGER NOT NULL REFERENCES artifacts (id) ON DELETE RESTRICT,
    -- The verbatim excerpt, as published.
    excerpt TEXT NOT NULL CHECK (length(excerpt) > 0),
    start_ms INTEGER CHECK (start_ms IS NULL OR start_ms >= 0),
    end_ms INTEGER CHECK (end_ms IS NULL OR end_ms >= 0),
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (start_ms IS NULL OR end_ms IS NULL OR end_ms >= start_ms),
    -- A video citation carries the video, its transcript and its seconds.
    CHECK (
        kind <> 'video' OR (
            video_id IS NOT NULL
            AND transcript_id IS NOT NULL
            AND start_ms IS NOT NULL
            AND end_ms IS NOT NULL
            AND record_id IS NULL
            AND page_number IS NULL
        )
    ),
    -- A record citation carries the document and the source it came from.
    -- The page is optional: a record published as HTML has no page numbers
    -- (spec 9.6).
    CHECK (
        kind <> 'record' OR (
            record_id IS NOT NULL
            AND source_id IS NOT NULL
            AND video_id IS NULL
            AND transcript_id IS NULL
            AND start_ms IS NULL
            AND end_ms IS NULL
        )
    )
);

CREATE INDEX citations_video_idx ON citations (video_id);
CREATE INDEX citations_record_idx ON citations (record_id);
CREATE INDEX citations_artifact_idx ON citations (artifact_id);
