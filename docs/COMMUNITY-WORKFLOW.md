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
then retrieve that coverage. If documents redirect to provider storage, enter
the exact verified storage origin in Document download origins. For the
Longmont pilot the observed PDF origin is `https://pgwest.blob.core.windows.net`.
Do not enter a wildcard or unrelated host. The shared connector still validates
public addresses and redirect destinations; a rejected download is retryable.
Only selected bodies are imported; other named bodies are reported as outside
configured coverage, not as a source outage. The service workers process the queued work.

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

The exact Windows candidate has passed real two-body acquisition and repeat
discovery, document extraction, full source-caption readback, timestamp navigation,
an isolated TownReporter browser handoff, and backup/restore. Full Council and
Planning evidence packages also passed the TownReporter receive function without
truncation. Local Whistle and Whisper ran identical real meeting clips; Whisper
small was retained for the better observed wording. These are bounded observations,
not a claim that all providers or the wider specification are complete.

See [Community acceptance](COMMUNITY-ACCEPTANCE.md) for exact identities, evidence
tiers and remaining acceptance. A second human operator and resident/reporter
have not yet completed the planned community trial. Tagging and publishing remain
held for Scott's approval.
