-- The PrimeGov sync (spec 9.2, 16.1).
--
-- A meeting a portal listed keeps the portal's own id. The id is a column and
-- not a field inside a JSON blob, because a second sync of the same window has
-- to find the row the first sync made, and it does that with one indexed
-- lookup on (portal_source_id, portal_meeting_id).
--
-- Nothing here holds a signed storage link. The portal, the meeting and the
-- template id are kept and the citation URL is built from them (spec 9.2).
-- The signed link expires in about two days, so a stored one would rot.

ALTER TABLE meetings ADD COLUMN portal_source_id INTEGER REFERENCES sources (id) ON DELETE RESTRICT;
ALTER TABLE meetings ADD COLUMN portal_meeting_id INTEGER;
ALTER TABLE meetings ADD COLUMN portal_html_template_id INTEGER;

CREATE UNIQUE INDEX meetings_portal_idx
    ON meetings (portal_source_id, portal_meeting_id)
    WHERE portal_meeting_id IS NOT NULL;

ALTER TABLE records ADD COLUMN portal_document_id INTEGER;
ALTER TABLE records ADD COLUMN portal_template_id INTEGER;

CREATE UNIQUE INDEX records_portal_document_idx
    ON records (meeting_id, portal_document_id)
    WHERE portal_document_id IS NOT NULL;

-- A video the portal listed and no watched channel has is still one video with
-- one platform id, so the sync can find the row a channel capture already
-- made. It is an index and not a UNIQUE: another adapter may use a platform
-- whose ids are not global, and a refusal there would be a bug of this table.
ALTER TABLE videos ADD COLUMN source_note TEXT;
CREATE INDEX videos_platform_video_id_idx ON videos (platform_video_id);

-- One alignment run per meeting: the offset measured for the whole video, the
-- anchors it was measured from, and how many items each method accounted for.
-- The meeting id is the key, so re-running the job rewrites this row rather
-- than adding a second one (same inputs, same rows).
CREATE TABLE meeting_alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    video_id INTEGER REFERENCES videos (id) ON DELETE RESTRICT,
    transcript_id INTEGER REFERENCES transcripts (id) ON DELETE RESTRICT,
    agenda_item_count INTEGER NOT NULL CHECK (agenda_item_count >= 0),
    -- The offset the aligner measured, in seconds, and whether it was accepted.
    -- An unaccepted offset is kept with its reason: the run is still recorded.
    offset_s INTEGER,
    offset_accepted INTEGER NOT NULL CHECK (offset_accepted IN (0, 1)),
    offset_reason TEXT NOT NULL DEFAULT '',
    -- The anchors the offset was measured from, as a JSON list of
    -- {item_number, agenda_seconds, spoken_ms, difference_s, matched_text}.
    anchors TEXT NOT NULL DEFAULT '[]',
    anchors_agreeing INTEGER NOT NULL DEFAULT 0 CHECK (anchors_agreeing >= 0),
    spoken_transitions INTEGER NOT NULL DEFAULT 0 CHECK (spoken_transitions >= 0),
    html_video_times INTEGER NOT NULL DEFAULT 0 CHECK (html_video_times >= 0),
    no_alignment INTEGER NOT NULL DEFAULT 0 CHECK (no_alignment >= 0),
    -- Why a method gave nothing, in the aligner's own words.
    spoken_reason TEXT,
    html_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (meeting_id)
);

CREATE INDEX meeting_alignments_video_idx ON meeting_alignments (video_id);
