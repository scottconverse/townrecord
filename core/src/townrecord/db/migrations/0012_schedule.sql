-- The daily schedule (spec 16.2).
--
-- Two things the schedule needs that the jobs table could not say on its own.
--
-- `origin` splits a scheduled run from a manual one. Spec 16.2 asks for the
-- two to be recorded separately, and a column on the job makes that true for
-- every reader instead of a join each caller has to remember. Every row that
-- exists before this migration is a manual run: nothing before this unit could
-- schedule anything, so 'manual' is the honest default.
--
-- `schedule_runs` is the idempotency guard. One row per task, per subject, per
-- local day, in the area's time zone. The scheduler asks it whether the day's
-- run already happened, so a service that restarts ten times in one morning
-- still syncs the day once. The UNIQUE key is the rule, not a convention.
--
-- A run that cannot happen is a row too, with state 'paused' and a plain
-- reason, so a schedule that stopped is visible rather than silent (spec 16.2,
-- 16.3). A paused run holds no job, and the CHECKs below say so: an enqueued
-- row has a job id and no reason, a paused row has a reason and no job id.

ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'
    CHECK (origin IN ('scheduled', 'manual'));

CREATE TABLE schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The job kind the run would enqueue, or enqueued: 'sync_primegov',
    -- 'runtime_update'.
    task TEXT NOT NULL CHECK (length(task) > 0),
    -- What the run is about, so one task can have many subjects a day:
    -- 'source:4' for one portal, 'tool:yt-dlp' for one runtime tool.
    subject TEXT NOT NULL CHECK (length(subject) > 0),
    -- The local date in the zone below, as YYYY-MM-DD. It is the day the run
    -- belongs to, which is what "once per local day" is measured in, and it is
    -- never the UTC date: a run at 22:00 in Denver belongs to that Denver day.
    local_date TEXT NOT NULL CHECK (length(local_date) = 10),
    -- The zone the local date was read in, kept so a later reader can tell
    -- which day a run was counted against when the area's zone changes.
    time_zone TEXT NOT NULL CHECK (length(time_zone) > 0),
    state TEXT NOT NULL CHECK (state IN ('enqueued', 'paused')),
    job_id INTEGER REFERENCES jobs (id),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (task, subject, local_date),
    CHECK ((state = 'enqueued') = (job_id IS NOT NULL)),
    CHECK (state <> 'paused' OR reason IS NOT NULL)
);

CREATE INDEX schedule_runs_local_date_idx ON schedule_runs (local_date, task);
