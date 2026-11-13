# Windows community-record operation

This is the bounded Python community-record service and TypeScript browser interface.
Tagging, downloadable
release publication and public deployment require Scott's approval.

## Install the reviewed revision

Use a dedicated Windows account/profile and a supported Python 3.12 or newer,
Git, uv and FFmpeg on PATH. FFmpeg is needed for local audio transcription;
it is not supplied by a browser or by the TownRecord wheel. Do not use a city
production profile for evaluation. Keep the reviewed source checkout to preserve
the pinned QuorumRelay wheel and uv lockfile.

```powershell
git clone https://github.com/scottconverse/townrecord.git
Set-Location townrecord
# Check out the exact reviewed commit named in the acceptance/handoff record.
uv sync --project core --frozen --no-dev
$profileRoot = Join-Path $env:LOCALAPPDATA 'TownRecord-Community'
New-Item -ItemType Directory -Force -Path $profileRoot | Out-Null
$env:TOWNRECORD_DB = Join-Path $profileRoot 'townrecord.db'
$env:TOWNRECORD_STORAGE = Join-Path $profileRoot 'storage'
$env:TOWNRECORD_RUNTIME_ROOT = $profileRoot
$env:TOWNRECORD_TIME_ZONE = 'America/Denver'
$env:TOWNRECORD_DAILY_TIME = '06:00'
# Use a known, publicly accessible recording from the configured community.
$env:TOWNRECORD_TEST_VIDEO = 'https://www.youtube.com/watch?v=3qfQAkAAC9U'
& core/.venv/Scripts/townrecord.exe token create --name 'Community operator' --scope write
& core/.venv/Scripts/townrecord.exe serve
```

Save these environment settings in a private local start script; use it every
restart. Save the token printed once by token create in the operator's password
manager. It grants access to unpublished material and publication. Create a named
token for each operator using the same existing token command. Do not paste tokens
in repository files, public documentation or a resident link. The service listens
on http://127.0.0.1:8190 by default. Leave its console running; Ctrl+C stops it.

## First setup and daily operation

1. Open http://127.0.0.1:8190/operator and sign in with the write token.
2. Add the community and two public bodies using the source's names. The pilot
   uses Longmont City Council and Planning and Zoning Commission. These are public
   source checks, not a city partnership or endorsement.
3. Configure https://longmont.primegov.com, provider `primegov`, the desired initial
   coverage dates and verified document origin https://pgwest.blob.core.windows.net.
   Other providers retain the capability limits in CONNECTORS.md; this pilot does
   not claim live acceptance of all providers.
4. Under Processing, set up/check yt-dlp and TextFlowKit. This runs the existing
   managed-tool jobs. Installation needs internet access and disk space. TextFlowKit
   uses the existing Whisper small model locally; its first transcription downloads
   the model. Meeting audio is not sent to a hosted transcription provider.
5. Retrieve coverage. Read the job result and any failures. A queued or failed job
   is not an acquired transcript. Review each meeting's dates, body, documents,
   transcript origin and source links. Correct metadata or associations with a reason.
6. If captions are absent or unusable, choose Create local transcript for that
   recording. Check the job result. Retry failed/paused jobs after addressing their
   stated cause. Then review the transcript against its recording before publication.
7. Publish with a reason. Residents see only the reviewed snapshot. Corrections
   and newly acquired text remain private until republished. Withdraw removes current
   resident access without destroying history or recalling copies already downloaded.
8. To hand off a meeting, export its evidence JSON, then import it from TownReporter's
   Sources & scan page. It becomes an unpublished New lead. Duplicate submissions
   preserve newsroom edits; revised evidence requires a newly published revision.

Daily retrieval runs at the configured local time while `serve` is running.
Scheduled sync uses the existing rolling window (30 days back, 60 days ahead);
the operator's explicit coverage dates govern manual retrieval. `townrecord status`
reports scheduled runs, paused causes and source health. No machine wake-up or
Windows startup registration is installed by this candidate. To run after sign-in,
IT may schedule the same private start script under the same account. Never run a
second service against the same profile. Missing timezone or unavailable tools
must be corrected rather than treating a paused schedule as successful.

## Residents and network access

Open `/` to find published meetings by topic, public body or date; open a document,
read available text, and follow transcript/agenda timestamps to the original video.
`/?meeting=ID` opens a specific reviewed meeting. Missing text remains visible as
missing; machine-generated transcripts are not certified minutes. Source recording
availability is controlled by the publisher.

Loopback is the default. For a managed LAN deployment, set TOWNRECORD_ALLOW_LAN=true
and TOWNRECORD_HOST to the intended interface. Serve the operator interface through
HTTPS before using bearer tokens off-machine. The `/public/v1/` GET routes and
resident page are deliberately anonymous; private routes still require tokens.
Keep the original-source URLs and public community URL usable from the recipient's
machine before transferring evidence. A localhost URL on another computer refers
to that computer, not to the community server.

## Backup and recovery

Stop the service before backup. Backups include unpublished material, token hashes,
connector captures, source configuration, publication history, instance identity,
all referenced artifacts and schedule settings. Treat the backup as private city
information. External provider credentials in environment variables and managed
programs/model caches are not copied; preserve credentials separately and reinstall
managed tools after restore. No source data or signed release is deleted by backup.

```powershell
& core/.venv/Scripts/townrecord.exe backup 'D:/TownRecord-Backups/2026-11-13'
& core/.venv/Scripts/townrecord.exe restore 'D:/TownRecord-Backups/2026-11-13' 'C:/TownRecord-Restored'
```

Both commands require a new destination and refuse to overwrite existing data.
Backup verifies immutable artifact hashes. Restore verifies all hashes, database
integrity and relationships before creating the destination, then verifies copied
bytes. A partial directory without a valid manifest is not a backup. Keep the old
profile until the restored one is checked. Update the start script's DB/storage/
runtime paths to the paths printed by restore, and restore timezone/daily time/test
video settings from restored-settings.json. Start it on a separate test port first,
sign in, check a known meeting/document/publication and retrieve again. Instance
identity survives restore, so the same export remains idempotent in TownReporter.

## Handoff boundaries

See COMMUNITY-RECORD-STATE.md for the candidate and observed acceptance status.
No operator can assume missing minutes, captions or records exist merely because
listing succeeded. The wider product specification remains a reference/backlog;
these instructions do not claim its unimplemented desktop or question-answering
features are complete.
