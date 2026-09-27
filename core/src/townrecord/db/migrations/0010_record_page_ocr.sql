-- The pages that have no text layer (spec 9.6).
--
-- A scanned page is a page of a record with no text in it. It is still a page,
-- so the row is written and its page number is counted in the record's page
-- count; what it cannot have is text. Reading it needs OCR, which no unit does
-- yet, so the row carries the plain reason it has no text rather than an empty
-- string that reads as a page nobody could extract from.
--
-- Empty text alone cannot carry that meaning: a page of a text PDF that
-- genuinely prints nothing and a page that was never anything but an image
-- would be one row. The reason is NULL for a page that was read, and a
-- sentence for a page that needs OCR.

ALTER TABLE record_pages ADD COLUMN ocr_reason TEXT;
