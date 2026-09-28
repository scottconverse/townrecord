-- The motions the minutes record, and where a meeting's minutes were found
-- (spec 9.4, 10.4).

-- A meeting's own minutes are not written until the next regular session meets,
-- because that is when they are approved, so the draft of one session is read
-- out of the next session's packet (spec 9.4). This table is the answer to
-- "where were they, and where is the answer that they are not there": one row
-- per run of pages read as the minutes of one meeting, holding the record and
-- the packet pages the run covers.

CREATE TABLE minutes_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    -- The record the pages were read out of, which is a packet of another
    -- meeting and not a record of this one.
    record_id INTEGER NOT NULL REFERENCES records (id) ON DELETE RESTRICT,
    -- The run's first and last page in the packet, one based, and how many
    -- pages it covers. The pages carry the packet's own footer (spec 9.4), so
    -- these are the numbers a citation of one of them names.
    start_page INTEGER NOT NULL CHECK (start_page >= 1),
    end_page INTEGER NOT NULL CHECK (end_page >= start_page),
    page_count INTEGER NOT NULL CHECK (page_count >= 1),
    -- The head the first page prints, kept so a reader can see why these pages
    -- were read as the minutes rather than taking the rule on trust.
    head TEXT NOT NULL CHECK (length(head) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (meeting_id, record_id)
);

CREATE INDEX minutes_documents_meeting_idx ON minutes_documents (meeting_id);

-- One motion as the minutes wrote it. The `votes` table holds one row per
-- (item, source kind), which cannot hold two motions on one item: the
-- September 8, 2026 minutes moved seven times on item 9.B, three of them
-- withdrawn, and only the last carried. A motion is therefore its own row
-- here, and `votes` keeps the outcome of the final one per source kind. The
-- amendments are not dropped, they are the record of how the item reached its
-- outcome (spec 10.4).

CREATE TABLE motions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings (id) ON DELETE RESTRICT,
    -- The record and page the motion was read from, which is the citation of
    -- it: the packet page, not the draft page the minutes printed (spec 9.4).
    record_id INTEGER NOT NULL REFERENCES records (id) ON DELETE RESTRICT,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    -- The motion's place in its meeting's minutes, counted from one, so two
    -- readings of the same minutes write one set of rows.
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    mover TEXT NOT NULL CHECK (length(mover) > 0),
    -- Empty when the minutes print no seconder ("seconded by ,"), which is what
    -- the September 8, 2026 draft does for one of its motions.
    seconder TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL CHECK (length(text) > 0),
    result TEXT NOT NULL
        CHECK (result IN ('passed', 'failed', 'tabled', 'withdrawn', 'unknown')),
    -- How the minutes closed the motion: the line itself, verbatim ("Carried:
    -- 7 – 0"), or the words "MOTION WITHDRAWN" for a motion that was withdrawn.
    -- A withdrawn motion prints a bare "0 – 0" under it as a template
    -- artifact; that line is not a count and is not stored.
    outcome TEXT NOT NULL,
    -- The names as the minutes print them, each a JSON array. "None" is an
    -- empty array, which is what the minutes say and is not the same as a list
    -- nobody could read.
    approved TEXT NOT NULL DEFAULT '[]',
    dissented TEXT NOT NULL DEFAULT '[]',
    abstained TEXT NOT NULL DEFAULT '[]',
    -- The counted result as JSON: yes, no and abstain. A motion that was
    -- withdrawn was not counted, so its tally is NULL and the "0 – 0" it
    -- printed stays in `outcome` where it was read (spec 10.4).
    tally TEXT,
    -- The motion and its outcome, verbatim, from the mover's line through the
    -- outcome line. This is the excerpt a citation of it shows (spec 10.5).
    evidence TEXT NOT NULL CHECK (length(evidence) > 0),
    -- The citation of the packet page this motion was read from (spec 10.5).
    -- A vote that came out of this motion cites the same page and rests on the
    -- same hash, so it points at this one rather than at a copy of it.
    citation_id INTEGER REFERENCES citations (id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (meeting_id, ordinal)
);

CREATE INDEX motions_meeting_idx ON motions (meeting_id, ordinal);

-- Which agenda items one motion is a motion on. A motion names an item by the
-- ordinance or resolution number in its text, by an item number ("except items
-- 9B and 9E"), or by approving the consent agenda, which is a motion on every
-- item under it except the ones it names. The link is its own row because one
-- consent motion covers many items and one item can carry many motions.

CREATE TABLE motion_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    motion_id INTEGER NOT NULL REFERENCES motions (id) ON DELETE RESTRICT,
    agenda_item_id INTEGER NOT NULL REFERENCES agenda_items (id) ON DELETE RESTRICT,
    link_kind TEXT NOT NULL CHECK (link_kind IN ('identifier', 'item_number', 'consent')),
    -- The words the link rests on, so a reader can see what was read.
    evidence TEXT NOT NULL CHECK (length(evidence) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (motion_id, agenda_item_id)
);

CREATE INDEX motion_items_item_idx ON motion_items (agenda_item_id);

-- A vote now names the motion it came out of, and the citation it rests on.
-- Both are optional: a vote that came from a structured record or a packet has
-- no motion in a minutes document, and the source's own row is then the
-- evidence. Naming the motion is what keeps the reading answerable: a reader
-- who sees one outcome per item can walk to the motion that produced it and to
-- the amendments that did not.

ALTER TABLE votes ADD COLUMN motion_id INTEGER REFERENCES motions (id) ON DELETE RESTRICT;
ALTER TABLE votes ADD COLUMN citation_id INTEGER REFERENCES citations (id) ON DELETE RESTRICT;
