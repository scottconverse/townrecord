-- The content-addressed artifact store (spec 8.6, 10.5).
--
-- An artifact is a file whose name carries the SHA-256 of its own bytes:
-- transcript-{sha256}.srv3, .vtt or .json, and info-{sha256}.json. Artifacts
-- never change, so the hash of the file is the identity of the row.
--
-- `rel_path` is always relative to the storage root the user chose, so the
-- user can move the root without rewriting a single row. The CHECK refuses an
-- absolute path and a Windows drive letter; the file names themselves are
-- built from a kind and an extension, never from a path the caller sends.
--
-- `meta` holds the facts a citation needs (spec 10.5): the video id and where
-- the text came from (publisher captions, auto-captions, sister channel,
-- local speech-to-text), and anything else the caller records.

CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (length(kind) > 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    rel_path TEXT NOT NULL CHECK (rel_path NOT LIKE '/%' AND rel_path NOT LIKE '%:%'),
    size INTEGER NOT NULL CHECK (size >= 0),
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (kind, sha256)
);

CREATE INDEX artifacts_sha256_idx ON artifacts (sha256);

-- A missing info.json sidecar is recorded with its reason (spec 8.6). It never
-- passes as a silent success. The rows are append only: a later check adds a
-- row rather than rewriting what was seen the first time.

CREATE TABLE missing_sidecars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL CHECK (length(video_id) > 0),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX missing_sidecars_video_idx ON missing_sidecars (video_id);
