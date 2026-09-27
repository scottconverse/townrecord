-- A job that knows it cannot run yet says when to try again (spec 8.2, 16.1).
--
-- "Skip upcoming and live videos, count them, and retry later" (spec 8.2) is a
-- delay, not a failure: the job is still work somebody has to do. Without this
-- column the runner would claim the job again at once and pace around a live
-- stream as fast as the loop allows. `run_after` holds the earliest moment the
-- job may be claimed, in the same UTC, ISO 8601 shape as every other time here
-- (`strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`), so the two compare as text.
--
-- NULL means "no delay": the job is claimable now, which is what every job
-- written before this migration gets.

ALTER TABLE jobs ADD COLUMN run_after TEXT;

-- The queue asks for the oldest queued job of a lane that is due, so the index
-- carries the three columns that question uses.
CREATE INDEX jobs_lane_due_idx ON jobs (lane, state, run_after);
