-- Public presentation contains reviewed snapshots, never live private rows.
CREATE TABLE publications (
    meeting_id INTEGER PRIMARY KEY REFERENCES meetings(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('published','withdrawn')),
    snapshot TEXT NOT NULL,
    published_at TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE operator_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER REFERENCES meetings(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE publication_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(meeting_id,revision)
);
ALTER TABLE records ADD COLUMN identity_key TEXT;
UPDATE records SET identity_key=(SELECT identity_key FROM connector_links
 WHERE connector_links.record_id=records.id LIMIT 1)
 WHERE id IN (SELECT record_id FROM connector_links WHERE record_id IS NOT NULL);
DROP INDEX records_unversioned_content_idx;
CREATE UNIQUE INDEX records_unversioned_content_idx ON records(meeting_id,kind,artifact_id)
 WHERE portal_document_id IS NULL AND identity_key IS NULL;

ALTER TABLE records ADD COLUMN origin_meeting_id INTEGER REFERENCES meetings(id);
