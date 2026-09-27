-- Full-text search over the spoken lines of a transcript and the text of a
-- record page (spec 12.4). Semantic search, the other half of that section,
-- is a later unit and is not here.
--
-- Why FTS5 and not a LIKE query: the spec asks for full-text search over
-- transcripts and records, and FTS5 is what SQLite provides for it (spec 14.3).
--
-- Why external content: `content=` makes the index a pointer into the table it
-- indexes. The text lives in exactly one place, `segments.text` and
-- `record_pages.text`, so the index can never disagree with the row it
-- describes and the database does not hold every caption line twice
-- (decision 10 rule C, one source of truth per fact).
--
-- Why triggers: the index is maintained by the database itself, so no code
-- path can forget it. An insert, an update and a delete on the indexed table
-- each move the index with the row. The alternative, a call in the repository
-- layer, is one forgotten call away from a search that silently misses a
-- segment.
--
-- The 'delete' command below is the FTS5 form for removing a row from an
-- external content index. It has to be given the old values, which is why the
-- update trigger writes the delete before the insert.

CREATE VIRTUAL TABLE segment_search USING fts5(
    text,
    content='segments',
    content_rowid='id'
);

CREATE TRIGGER segment_search_insert
AFTER INSERT ON segments
BEGIN
    INSERT INTO segment_search (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER segment_search_delete
AFTER DELETE ON segments
BEGIN
    INSERT INTO segment_search (segment_search, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER segment_search_update
AFTER UPDATE ON segments
BEGIN
    INSERT INTO segment_search (segment_search, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO segment_search (rowid, text) VALUES (new.id, new.text);
END;

CREATE VIRTUAL TABLE record_page_search USING fts5(
    text,
    content='record_pages',
    content_rowid='id'
);

CREATE TRIGGER record_page_search_insert
AFTER INSERT ON record_pages
BEGIN
    INSERT INTO record_page_search (rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER record_page_search_delete
AFTER DELETE ON record_pages
BEGIN
    INSERT INTO record_page_search (record_page_search, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER record_page_search_update
AFTER UPDATE ON record_pages
BEGIN
    INSERT INTO record_page_search (record_page_search, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO record_page_search (rowid, text) VALUES (new.id, new.text);
END;

-- A database that already held segments and record pages before this migration
-- ran needs its index built once. 'rebuild' reads every row of the content
-- table, which is the only way an external content index can be filled from
-- rows that were inserted while it did not exist.
INSERT INTO segment_search (segment_search) VALUES ('rebuild');
INSERT INTO record_page_search (record_page_search) VALUES ('rebuild');
