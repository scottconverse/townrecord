# Community record workflow

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

The Windows candidate passed real two-body acquisition, repeat discovery, document
extraction, full source-caption readback and timestamp navigation. A full 98-minute
local Whisper small transcription completed with 1542 timed segments. Resident
readback preserved its provisional origin and final timestamp. Full Council and
Planning evidence files passed actual TownReporter browser imports as unpublished
New leads; duplicate and malformed-file behavior was checked. Operator corrections,
backup/restore and a completed scheduled retrieval also passed.

Whistle and Whisper were compared on identical real clips; Whisper small was
retained for the better observed wording. Synthetic tests cover staged arrival and
republication. These are bounded observations, not acceptance of every provider,
human accuracy certification or completion of the wider specification.

See the [user manual](USER-MANUAL.md), [technical manual](TECHNICAL-MANUAL.md) and
[acceptance record](COMMUNITY-ACCEPTANCE.md) for instructions and exact evidence.
An independent operator/resident trial remains a suggestion; Scott defines any
required human work. Tagging, release publication and website deployment are held
for Scott's approval.
