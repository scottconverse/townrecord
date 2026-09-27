-- Jobs (spec 16.1). Claim tokens, heartbeats and checkpoints, so a long job
-- can be claimed by one worker and resume where it stopped.
-- Two lanes: 'heavy' (one worker) and 'normal' (two workers).
-- State values are 'queued', 'running', 'done', 'failed' and 'paused'.
-- This migration creates the table only. No worker code here.

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT,
    lane TEXT NOT NULL DEFAULT 'normal' CHECK (lane IN ('heavy', 'normal')),
    state TEXT NOT NULL DEFAULT 'queued',
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

CREATE INDEX jobs_lane_state_idx ON jobs (lane, state);
CREATE INDEX jobs_claim_token_idx ON jobs (claim_token);
