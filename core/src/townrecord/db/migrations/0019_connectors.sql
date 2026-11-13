-- Additive shared-engine storage. Existing identities, jobs and evidence stay unchanged.
CREATE TABLE connector_state (
    scope TEXT NOT NULL,
    collection TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL CHECK (json_valid(value)),
    version INTEGER NOT NULL CHECK (version > 0),
    PRIMARY KEY (scope, collection, key)
);
CREATE TABLE connector_state_history (
    scope TEXT NOT NULL,
    collection TEXT NOT NULL,
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    value TEXT NOT NULL CHECK (json_valid(value)),
    PRIMARY KEY (scope, collection, key, version)
);
CREATE TABLE connector_captures (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    content BLOB NOT NULL,
    UNIQUE (scope, sha256)
);
CREATE TABLE connector_links (
    scope TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    meeting_id INTEGER REFERENCES meetings(id),
    record_id INTEGER REFERENCES records(id),
    video_id INTEGER REFERENCES videos(id),
    PRIMARY KEY (scope, identity_key)
);
CREATE INDEX connector_links_meeting ON connector_links(meeting_id);
CREATE INDEX connector_links_source ON connector_links(source_id);
