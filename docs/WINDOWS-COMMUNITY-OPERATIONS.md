# Windows community-record operation

TownRecord uses a Python service and TypeScript browser interface. Start with the
0.2.0 Windows installer candidate guidance below; final acceptance and publication
status remain in the [candidate handoff](COMMUNITY-COMPLETION-HANDOFF.md).
The earlier Python-wheel preview instructions are preserved in the historical
advanced section.

## Install and open the Windows application (0.2.0)

The 0.2.0 Windows installer candidate is the normal-user path. Its build, signing,
installed acceptance and publication status are recorded in
[the candidate handoff](COMMUNITY-COMPLETION-HANDOFF.md); this guide is not a
signing or publication receipt. Obtain the signed installer from the
[TownRecord releases page](https://github.com/scottconverse/townrecord/releases)
when 0.2.0 is available. Check Windows file Properties > Digital Signatures for
**Scott Converse**, following the release verification notes.

Run the installer for your Windows account, then choose **TownRecord** in Start
Menu. Python, FFmpeg and a pinned, signed TextFlowKit 0.1.19 runtime are supplied;
model weights are not bundled. No pip, Node, PowerShell or
copied operator token is needed. The browser opens already signed in. Its launch
link is short-lived and works once. After reload or sign out, open TownRecord
from Start Menu again. Closing the browser leaves retrieval running. Use **Stop
TownRecord** in the workspace to stop it; Start Menu starts it again.

The application runs under your signed-in Windows account. Windows sign-in startup,
where enabled by the installer, uses that account; this is not a machine-wide
Windows service and does not wake a sleeping computer. Reopening Start Menu
reuses the running instance. Public hosting and a resident community URL remain
separate IT work.

### First-run checklist

1. Confirm the detected community timezone, choose the daily retrieval time, and
   paste a known accessible public recording URL for downloader checks. **Save
   settings and apply** restarts automatically. Wait for **Settings are applied**.
2. Add the community and public bodies using the source names. Configure the
   supported provider URL, verified document origins and explicit coverage dates.
   **Validate and save source** uses existing connector validation. See
   [connector capabilities](CONNECTORS.md).
3. Under Processing, **Set up / check** yt-dlp and TextFlowKit. TextFlowKit setup
   verifies and copies the bundled signed 0.1.19 runtime into managed storage;
   this initial bootstrap can work offline. FFmpeg is bundled. Model downloads,
   yt-dlp setup/checks and meeting-source access still need internet access and
   disk space. Existing online update checks retain a working runtime if a newer
   version fails installation or its check. Read job states; **Refresh status**
   checks progress and **Retry** retries failed/paused jobs after resolving their
   stated cause.
4. Retrieve configured coverage, review records and publish deliberately. Setup
   saved or a queued job does not establish completed acquisition.

### Settings and recovery without commands

The desktop panel shows the active data and backup folders. Edit settings and save
for automatic restart. **Restart TownRecord** provides an explicit retry. If the
connection does not return, reopen from Start Menu and check persisted settings.

**Create verified backup** stops workers, creates and verifies a timestamped backup
in the shown folder, then restarts. Wait for its verified result. If it fails,
check the error and backup folder before repeating. Backups include unpublished
records and token hashes; protect them and copy verified backups to a separate
protected device under your community's policy.

Expand **Restore a backup**, paste the backup folder path from File Explorer,
then **Restore and switch profile** and confirm. Restore checks the backup into
a separate profile, then restarts using it. Reopen from Start Menu to authenticate.
Check a known meeting, document, publication and retrieval before relying on the
restored copy. Keep the old profile. External provider credentials and managed
tool/model caches are excluded; set up tools again after restore when needed.

## Advanced: historical 0.1.0 Python installation

[Historical 0.1.0 Python-wheel preview](https://github.com/scottconverse/townrecord/releases/tag/v0.1.0).
Scott approved publication of that unchanged wheel on November 13, 2026.

The following commands preserve the published preview instructions for a manually
managed service. They are not required for the 0.2.0 installer. Manual token entry
and environment variables apply to nonpackaged mode.

Use a dedicated Windows account/profile, Python 3.12 or newer, uv and FFmpeg on
PATH. FFmpeg is needed for local transcription and is not supplied by the wheel.
Use an evaluation profile first. Download these three release assets into the same
working directory: `TownRecord-0.1.0-source.zip`,
`townrecord-0.1.0-py3-none-any.whl`, and `SHA256SUMS.txt`.
Compare `Get-FileHash` results with the checksum file before extraction/install.
The wheel SHA-256 must be
`9cab2e58d36b3024caa095815854ae5d2d367708e3dba2ac6b539e0f17d2b5ce`.

The source archive includes the lockfile and vendored QuorumRelay dependency.
The commands install those locked dependencies, then the exact release wheel;
they do not rebuild TownRecord or substitute the current main branch.
Keep the archive checkout for future dependency setup. Internet access is needed
for dependencies, source retrieval and initial managed-tool/model downloads.

```powershell
Get-FileHash .\TownRecord-0.1.0-source.zip -Algorithm SHA256
Get-FileHash .\townrecord-0.1.0-py3-none-any.whl -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
Expand-Archive .\TownRecord-0.1.0-source.zip -DestinationPath .
Set-Location townrecord-0.1.0
uv sync --project core --frozen --no-dev --no-install-project
uv pip install --python core/.venv/Scripts/python.exe --no-deps ../townrecord-0.1.0-py3-none-any.whl
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

## Advanced CLI setup and shared daily operation

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

Windows application users use the desktop controls above. These commands apply
to the historical manually managed service and preserve the same verification.

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
