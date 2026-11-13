-- rebuild-tables
-- 0019 is reserved for the existing connector integration candidate.
-- A schedule is not an identity. Keep every existing primary/foreign key.
CREATE TABLE meetings_rebuilt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body_id INTEGER NOT NULL REFERENCES bodies(id) ON DELETE RESTRICT,
    title TEXT,
    starts_at TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'regular' CHECK(type IN ('regular','study','special','executive')),
    is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK(is_cancelled IN (0,1)),
    is_continued INTEGER NOT NULL DEFAULT 0 CHECK(is_continued IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    portal_source_id INTEGER REFERENCES sources(id) ON DELETE RESTRICT,
    portal_meeting_id INTEGER,
    portal_html_template_id INTEGER
);
INSERT INTO meetings_rebuilt SELECT * FROM meetings;
DROP TABLE meetings;
ALTER TABLE meetings_rebuilt RENAME TO meetings;
CREATE INDEX meetings_body_start_idx ON meetings(body_id,starts_at);
CREATE UNIQUE INDEX meetings_portal_idx ON meetings(portal_source_id,portal_meeting_id)
    WHERE portal_meeting_id IS NOT NULL;
-- Preserve old document rows and therefore their pages and citations.
-- The same bytes may be published again after an intervening revision. Artifact
-- storage deduplicates bytes; record IDs identify observations and citations.
CREATE TABLE records_rebuilt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE RESTRICT,
    source_id INTEGER REFERENCES sources(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('agenda','packet','minutes','ordinance','resolution','budget','report')),
    title TEXT,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
    page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    portal_document_id INTEGER,
    portal_template_id INTEGER
);
INSERT INTO records_rebuilt SELECT * FROM records;
DROP TABLE records;
ALTER TABLE records_rebuilt RENAME TO records;
CREATE INDEX records_meeting_idx ON records(meeting_id);
CREATE INDEX records_kind_idx ON records(kind);
CREATE INDEX records_portal_document_idx ON records (meeting_id, portal_document_id);
CREATE TABLE record_revisions (
    previous_record_id INTEGER PRIMARY KEY REFERENCES records(id) ON DELETE RESTRICT,
    current_record_id INTEGER NOT NULL UNIQUE REFERENCES records(id) ON DELETE RESTRICT,
    observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (previous_record_id <> current_record_id)
);
CREATE TABLE meeting_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE RESTRICT,
    previous_state TEXT NOT NULL,
    current_state TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TRIGGER meeting_schedule_history AFTER UPDATE ON meetings
WHEN OLD.starts_at IS NOT NEW.starts_at OR OLD.is_cancelled IS NOT NEW.is_cancelled
     OR OLD.is_continued IS NOT NEW.is_continued OR OLD.title IS NOT NEW.title
BEGIN
    INSERT INTO meeting_changes(meeting_id,previous_state,current_state)
    VALUES(NEW.id,
      json_object('starts_at',OLD.starts_at,'title',OLD.title,
                  'is_cancelled',OLD.is_cancelled,'is_continued',OLD.is_continued),
      json_object('starts_at',NEW.starts_at,'title',NEW.title,
                  'is_cancelled',NEW.is_cancelled,'is_continued',NEW.is_continued));
END;
