# Meeting and document updates

A provider's source and meeting ID identify a meeting. Refreshing that identity
updates its scheduled time without changing local links. Same-time meetings with
distinct provider IDs remain distinct. A time-only association is allowed only
when exactly one existing meeting has no portal identity; ambiguous candidates
are not silently merged.

Schedule, title and cancellation/continuation changes are retained in
`meeting_changes`. Cancellation signals from source listings remain conservative
until an explicit operator correction is available.

Document refreshes re-fetch bounded source bytes and compare content-addressed
artifacts. Unchanged bytes retain their record. Changed bytes create a new record
and a `record_revisions` link. A later reversion to earlier bytes is another
observation with its own record ID. Earlier records, pages and citation IDs stay
readable; meeting document lists select current revisions. A source failure does
not remove the previous record.

Migration 0020 rebuilds the meeting and record tables to remove schedule/content
uniqueness constraints that incorrectly treated them as identities. It preserves
primary keys and existing references, checks the foreign-key graph before commit,
and restores enforcement after success or failure. Back up the database and
artifact directory together before upgrading; stop the service while copying.
0019 is reserved for the existing connector candidate. Do not edit historical
migration files or apply an older program to the upgraded database.

Regression coverage includes rescheduling, distinct IDs at one time, unchanged
refresh, replacement, reversion, failed revalidation, and a populated-database
migration that preserves pages and meeting references. This is a backend repair;
operator/publication controls and resident pages remain work in progress.
