-- Search that finds a phrase split across two caption lines (spec 12.4).
--
-- Why a second index. A caption line is five to eight words, so a phrase the
-- user types often crosses a line break. The September 22, 2026 Longmont
-- transcript says "motion to pass and adopt ordinance" on one line and
-- "2026-58." on the next. FTS5 matches inside one row, and in `segment_search`
-- from 0007 one row is one caption line, so a phrase that spans a break is in
-- no row and cannot be found. This table holds every run of one, two or three
-- consecutive lines of a transcript as one row, so a phrase of up to three
-- lines is again a phrase inside a single row.
--
-- Why three lines and not two. The measured break is one line wide, but a
-- spoken sentence that runs to a third line is ordinary, and a three line
-- window holds a phrase of roughly twenty words, longer than the query a person
-- types. Four lines would add a row per line for a case that does not occur, so
-- three is the smallest size that covers the measured break with a margin.
-- Windows of one and two lines are here as well: a phrase that fits on one line
-- must be found at its own line, and the de-duplication in the search picks the
-- shortest window that holds the match, so a one line phrase cites one line and
-- not the three lines it happens to sit inside.
--
-- Why the rowid is the last line's id times four plus the length. A window is
-- identified by the line it ends at and how many lines it holds, so a row can be
-- found and replaced without a scan. The search recovers the length as
-- `rowid - last_segment_id * 4` and uses it to choose between overlapping
-- windows. The factor four leaves room for three lengths and is not a guess: a
-- window can hold one, two or three lines and never more.
--
-- How an insert finds the six windows it changes. A window that contains a new
-- line is a window of one, two or three lines ending at one of three lines: the
-- new one and the two that follow it in time. Everything the trigger needs is
-- therefore the new line, the two lines before it and the two after it, in
-- (start_ms, id) order. Those five rows are read once per insert in a subquery
-- that seeks the index `segments_transcript_start_idx` three times and stops
-- after a couple of rows each time, so the cost of an insert does not grow with
-- the length of the transcript. `lag` over that small set gives every window's
-- text without a second look at `segments`.
--
-- Why the insert uses INSERT OR REPLACE and no delete. Inserting X between A and
-- B can only change windows that contain X, and a window contains X only if it
-- ends at X, at B or at B's successor. Those are exactly the six rows this
-- trigger writes, each at its own fixed rowid, so writing them replaces the six
-- that are now wrong. A window that stops existing, because there is no line
-- before X to join it to, is a window that never existed: the old text of such a
-- row is still the right text for it, and the trigger simply does not write it.
-- That is the whole correctness argument, and it is why no delete is needed.
--
-- Ordering is by `(start_ms, id)`, which is the order `segments_of` reads a
-- transcript in and is a total order, because `id` is unique. Two lines that
-- share a millisecond are ordered by id, so a caption file read as it is written
-- lands in the index in the order the reader meant.
CREATE VIRTUAL TABLE segment_window_search USING fts5(
    text,
    transcript_id UNINDEXED,
    first_segment_id UNINDEXED,
    last_segment_id UNINDEXED
);

-- The three lengths, as three rows, so one statement writes all of them.
-- A window is written only when the lines it holds are all there.

-- An insert moves the index with the row, as 0007 does, so no code path can
-- forget it.
CREATE TRIGGER segment_window_insert
AFTER INSERT ON segments
BEGIN
    INSERT OR REPLACE INTO segment_window_search(
        rowid, text, transcript_id, first_segment_id, last_segment_id)
    SELECT near.id * 4 + lengths.length,
           CASE lengths.length
               WHEN 1 THEN near.text
               WHEN 2 THEN near.lag_text || ' ' || near.text
               ELSE near.lag2_text || ' ' || near.lag_text || ' ' || near.text
           END,
           new.transcript_id,
           CASE lengths.length
               WHEN 1 THEN near.id
               WHEN 2 THEN near.lag_id
               ELSE near.lag2_id
           END,
           near.id
    FROM (
        SELECT nearby.id, nearby.text, nearby.offset,
               lag(nearby.id) OVER window_order AS lag_id,
               lag(nearby.text) OVER window_order AS lag_text,
               lag(nearby.id, 2) OVER window_order AS lag2_id,
               lag(nearby.text, 2) OVER window_order AS lag2_text
        FROM (
            SELECT s.id, s.text, 0 AS offset
            FROM segments AS s
            WHERE s.id = new.id
            UNION ALL
            SELECT earlier.id, earlier.text, -earlier.rank
            FROM (
                SELECT s.id, s.text,
                       row_number() OVER (ORDER BY s.start_ms DESC, s.id DESC) AS rank
                FROM segments AS s
                WHERE s.transcript_id = new.transcript_id
                  AND (s.start_ms < new.start_ms
                       OR (s.start_ms = new.start_ms AND s.id < new.id))
                ORDER BY s.start_ms DESC, s.id DESC
                LIMIT 2
            ) AS earlier
            UNION ALL
            SELECT later.id, later.text, later.rank
            FROM (
                SELECT s.id, s.text,
                       row_number() OVER (ORDER BY s.start_ms, s.id) AS rank
                FROM segments AS s
                WHERE s.transcript_id = new.transcript_id
                  AND (s.start_ms > new.start_ms
                       OR (s.start_ms = new.start_ms AND s.id > new.id))
                ORDER BY s.start_ms, s.id
                LIMIT 2
            ) AS later
        ) AS nearby
        WINDOW window_order AS (ORDER BY nearby.offset)
    ) AS near
    CROSS JOIN (
        SELECT 1 AS length UNION ALL SELECT 2 UNION ALL SELECT 3
    ) AS lengths
    WHERE near.offset >= 0
      AND (lengths.length = 1
           OR (lengths.length = 2 AND near.lag_id IS NOT NULL)
           OR (lengths.length = 3 AND near.lag2_id IS NOT NULL));
END;

-- A change to a line and the removal of a line are both rare: a revision is new
-- bytes and so a new transcript, not an edit (spec 8.7), and a line cannot be
-- removed while its transcript exists (`ON DELETE RESTRICT`). Both are therefore
-- handled the simple and always right way: rebuild every window of that one
-- transcript in a single pass. A change that moves a line in time is covered
-- too, which the incremental path of the insert trigger would not be. The cost
-- is one pass over one transcript, paid only when a caller edits or removes a
-- line by hand.
CREATE TRIGGER segment_window_update
AFTER UPDATE ON segments
BEGIN
    DELETE FROM segment_window_search WHERE transcript_id = old.transcript_id;

    INSERT INTO segment_window_search(
        rowid, text, transcript_id, first_segment_id, last_segment_id)
    SELECT line.id * 4 + lengths.length,
           CASE lengths.length
               WHEN 1 THEN line.text
               WHEN 2 THEN line.lag_text || ' ' || line.text
               ELSE line.lag2_text || ' ' || line.lag_text || ' ' || line.text
           END,
           line.transcript_id,
           CASE lengths.length
               WHEN 1 THEN line.id
               WHEN 2 THEN line.lag_id
               ELSE line.lag2_id
           END,
           line.id
    FROM (
        SELECT s.id, s.text, s.transcript_id,
               lag(s.id) OVER window_order AS lag_id,
               lag(s.text) OVER window_order AS lag_text,
               lag(s.id, 2) OVER window_order AS lag2_id,
               lag(s.text, 2) OVER window_order AS lag2_text
        FROM segments AS s
        WHERE s.transcript_id = old.transcript_id
        WINDOW window_order AS (ORDER BY s.start_ms, s.id)
    ) AS line
    CROSS JOIN (
        SELECT 1 AS length UNION ALL SELECT 2 UNION ALL SELECT 3
    ) AS lengths
    WHERE lengths.length = 1
       OR (lengths.length = 2 AND line.lag_id IS NOT NULL)
       OR (lengths.length = 3 AND line.lag2_id IS NOT NULL);
END;

CREATE TRIGGER segment_window_delete
AFTER DELETE ON segments
BEGIN
    DELETE FROM segment_window_search WHERE transcript_id = old.transcript_id;

    INSERT INTO segment_window_search(
        rowid, text, transcript_id, first_segment_id, last_segment_id)
    SELECT line.id * 4 + lengths.length,
           CASE lengths.length
               WHEN 1 THEN line.text
               WHEN 2 THEN line.lag_text || ' ' || line.text
               ELSE line.lag2_text || ' ' || line.lag_text || ' ' || line.text
           END,
           line.transcript_id,
           CASE lengths.length
               WHEN 1 THEN line.id
               WHEN 2 THEN line.lag_id
               ELSE line.lag2_id
           END,
           line.id
    FROM (
        SELECT s.id, s.text, s.transcript_id,
               lag(s.id) OVER window_order AS lag_id,
               lag(s.text) OVER window_order AS lag_text,
               lag(s.id, 2) OVER window_order AS lag2_id,
               lag(s.text, 2) OVER window_order AS lag2_text
        FROM segments AS s
        WHERE s.transcript_id = old.transcript_id
        WINDOW window_order AS (ORDER BY s.start_ms, s.id)
    ) AS line
    CROSS JOIN (
        SELECT 1 AS length UNION ALL SELECT 2 UNION ALL SELECT 3
    ) AS lengths
    WHERE lengths.length = 1
       OR (lengths.length = 2 AND line.lag_id IS NOT NULL)
       OR (lengths.length = 3 AND line.lag2_id IS NOT NULL);
END;

-- A database that already held caption lines before this migration ran needs its
-- windows built once, the same way 0007 rebuilds its two indexes. The window is
-- partitioned here because this pass is the only one that reads more than one
-- transcript at a time.
INSERT INTO segment_window_search(
    rowid, text, transcript_id, first_segment_id, last_segment_id)
SELECT line.id * 4 + lengths.length,
       CASE lengths.length
           WHEN 1 THEN line.text
           WHEN 2 THEN line.lag_text || ' ' || line.text
           ELSE line.lag2_text || ' ' || line.lag_text || ' ' || line.text
       END,
       line.transcript_id,
       CASE lengths.length
           WHEN 1 THEN line.id
           WHEN 2 THEN line.lag_id
           ELSE line.lag2_id
       END,
       line.id
FROM (
    SELECT s.id, s.text, s.transcript_id,
           lag(s.id) OVER window_order AS lag_id,
           lag(s.text) OVER window_order AS lag_text,
           lag(s.id, 2) OVER window_order AS lag2_id,
           lag(s.text, 2) OVER window_order AS lag2_text
    FROM segments AS s
    WINDOW window_order AS (PARTITION BY s.transcript_id ORDER BY s.start_ms, s.id)
) AS line
CROSS JOIN (
    SELECT 1 AS length UNION ALL SELECT 2 UNION ALL SELECT 3
) AS lengths
WHERE lengths.length = 1
   OR (lengths.length = 2 AND line.lag_id IS NOT NULL)
   OR (lengths.length = 3 AND line.lag2_id IS NOT NULL);
