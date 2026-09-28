-- The catalog of records a body should have and does not (spec 9.5).
--
-- Spec 9.5 puts the rule in one sentence: a record that should exist and does
-- not is a finding, and the finding is "a catalog note, not a story". A note
-- needs three things a derivation from the meetings alone cannot give -- when
-- the gap opened, whether it has since been filled, and when -- so the finding
-- is a row here and the row's state is what changes when the record appears.
-- No row is ever deleted.
--
-- `opened_at` is derived and not observed: it is the meeting's start plus the
-- 36 hours the rule waits, so a sync that runs late writes the moment the gap
-- opened rather than the moment somebody looked. `created_at` is when the row
-- was written, and the two differ exactly when the sync ran late.
--
-- What this catalog does not hold: spec 9.5 states the rule for minutes alone,
-- so only a minutes gap is open today. The `kind` check accepts the agenda and
-- the packet as well, so a later rule of the spec's own can open one without a
-- schema change (decision 10 rule C).
--
-- The alert is a setting and not a delivery. Spec 12.7 names the alert
-- triggers and the four channels, and the column below is the first half of
-- one trigger ("a missing record older than N days") and none of the delivery.

-- The kind of body a meeting is held by, which the missing-record rule reads
-- (spec 9.5). Spec 6.2 names the bodies a town has -- a city council, a
-- planning and zoning commission, a board of education -- and declares no
-- column for the kind, so the kind is added here and left NULL where nobody
-- has stated one.
--
-- A NULL kind is not one of the four the rule reads, so such a body is never
-- flagged, and the catalog says that in words rather than staying quiet about
-- it (rule F). 'other' is a stated answer meaning "none of those four", which
-- is a different fact from nobody having said.
ALTER TABLE bodies ADD COLUMN type TEXT
    CHECK (type IS NULL OR type IN ('council', 'commission', 'board', 'authority', 'other'));

-- "Alert me after N days" for one body (spec 12.7). NULL is the default and
-- means no alert has been set. The catalog row says whether the threshold has
-- been crossed; nothing is delivered by this column and nothing by this unit.
ALTER TABLE bodies ADD COLUMN missing_alert_days INTEGER
    CHECK (missing_alert_days IS NULL OR missing_alert_days >= 0);

CREATE TABLE missing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The meeting whose record is missing. The body is read from the meeting
    -- and is never copied here: one fact lives in one column (rule C).
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('agenda', 'packet', 'minutes')),
    -- The moment the gap opened: the meeting's start plus the hours the rule
    -- waits. It is derived, so the same gap carries the same moment however
    -- late the row was written.
    opened_at TEXT NOT NULL,
    -- 'open' is a record that is still missing; 'filled' is one that appeared.
    -- A filled row is kept, so the catalog can say that the record arrived.
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'filled')),
    filled_at TEXT,
    -- When the sync wrote this row, which is not when the gap opened.
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- A row is open or it is filled, and a filled row always says when.
    CHECK ((state = 'filled') = (filled_at IS NOT NULL)),
    CHECK (filled_at IS NULL OR filled_at >= opened_at),
    -- One row per meeting and kind: a second sync finds the row it wrote.
    UNIQUE (meeting_id, kind)
);

-- The catalog is read per body and counted by state, and the meeting is where
-- the body is read from (rule C), so the join starts here.
CREATE INDEX missing_records_state_idx ON missing_records (state, meeting_id);
