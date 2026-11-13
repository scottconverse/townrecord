# Community record workflow (implementation in progress)

The November 13 bounded TownRecord plan adds an explicitly public, reviewed
meeting record. This supersedes the original local-API specification's
all-routes-private rule **only** for the resident page, its two static assets,
and GET routes under `/public/v1/`. Every private API route, API documentation,
and operator operation continues to require an existing bearer token. Operator
operations require `read_write`. No new account system is introduced.

## Review and publication

Open `/operator` and sign in with an administrator-issued write token. The token
is held in browser memory only. Sign out before leaving a shared workstation.
Create the community and its two public bodies using their source names. Add
the supported provider URL and explicit coverage dates. Validate and save,
then retrieve that coverage. The service workers process the queued work.

Review the acquired meeting, document text, original source links, recording
status and transcript origin. Unavailable material remains an explicit gap.
A publisher's captions are source-supplied text, not certified meeting minutes;
automatic captions and local speech-to-text are machine-generated.

Correct meeting metadata with a reason. For a wrongly associated document or
recording, choose its destination meeting and give the reason. A recording
selected this way becomes the destination's primary recording. Document
revisions and their original bytes remain available internally.

Publish only after reviewing the record. Publication stores a snapshot of the
meeting, its document IDs/text and transcript. Subsequent imports and corrections
remain private until the operator republishes. Withdrawing a meeting removes
its resident page and document download access, while retaining internal
history and evidence. Previously downloaded copies cannot be recalled.

Residents use `/` to browse published meetings, filter by body/date, search
available text, read extracted document pages and follow original sources.
Private acquisitions and operator history are never resident search results.

## Current acceptance boundary

This document describes the workflow under implementation. It is not a release
announcement or a claim that the whole bounded plan has passed. Initial API
publication/privacy tests passed; browser acceptance, transcript runtime,
TownReporter transfer, deployment and restore remain outstanding. Tagging and
publishing are held for Scott's approval.
