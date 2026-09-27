-- The jobs table accepts five states (spec 16.1). 0001 created the table
-- without a CHECK on `state`, so a typo could leave a job in a state no
-- worker understands. SQLite cannot add a CHECK with ALTER TABLE, so the
-- table is rebuilt here: create the new table, copy every row, drop the old
-- one, rename, and build the indexes again. The columns, the lane CHECK and
-- the two indexes of 0001 are kept exactly as they were.

CREATE TABLE jobs_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT,
    lane TEXT NOT NULL DEFAULT 'normal' CHECK (lane IN ('heavy', 'normal')),
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'done', 'failed', 'paused')),
    attempts INTEGER NOT NULL DEFAULT 0,
    claim_token TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    checkpoint TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    finished_at TEXT
);

INSERT INTO jobs_new (
    id, kind, payload, lane, state, attempts, claim_token, claimed_at,
    heartbeat_at, checkpoint, last_error, created_at, started_at, finished_at
)
SELECT
    id, kind, payload, lane, state, attempts, claim_token, claimed_at,
    heartbeat_at, checkpoint, last_error, created_at, started_at, finished_at
FROM jobs;

DROP TABLE jobs;

ALTER TABLE jobs_new RENAME TO jobs;

CREATE INDEX jobs_lane_state_idx ON jobs (lane, state);
CREATE INDEX jobs_claim_token_idx ON jobs (claim_token);
