# TownRecord Windows installer handoff — November 14, 2026

## Active delivery: Windows 0.2.0

Scott authorized the signed Windows installer, normal-user first-run, clean
Windows acceptance, documentation and merge/tag/release/site/Big Picture delivery.
The TypeScript browser interface and Python service remain; no Tauri or new provider.
Published v0.1.0 and its exact source 758abf6 remain historical and unchanged.

Current source is `3a247d8865f1692911f4bed15cae370410ec6d70`.
Wheel SHA256: `88857f9b821e67a273bb5197356a2125a04fbfad3961124fb1da9e0ba8da0bec`.
Signed installer SHA256: `e08a4f4447abb611a331cd7146df56493eb92f76bc14c9a660cb80fb9c3067a8`,
221,865,160 bytes. Required CI 43573893300, native signature reuse and outer
signing 43574774784 passed; independent Scott Converse signature and Microsoft
timestamp verification passed. [Receipts](evidence/windows-0.2.0/candidate-3a247d8/).

Clean ordinary-user installation verified 23,094 payload and 18,330 seed files.
Headless first-run settings, automatic one-use sign-in, two-body/source setup,
31 actual provider choices and both managed tools passed. Real PrimeGov sync
completed 12 downloads, 9 PDF extraction and 3 caption jobs. A 120-second Council
sample transcribed locally through the installed managed Whisper small/CPU path
in 39.398s (34 segments, 329 words, 119.820s final timestamp). Actual Council and
Planning review/publication/export and resident access passed; sign-out cleared
private content. These are scoped installed observations, not a full-meeting
transcription or verification of every provider.

Verified desktop backup/restore passed: 19 files rehashed, distinct restored profile
active, original database/publication retained and UI read/sign-out verified. The
initial harness navigation failure was resolved with a fresh document, without
repeating the backup/restore or changing the product. Same-version reinstall then passed with saved
configuration/data/credentials unchanged, full post-reinstall payload/seed proof
and service reopen. Uninstall removed program, exact HKCU registration, Start Menu
Open/Stop links and startup shortcut while retaining the saved profile. Installed
acceptance is complete within the scope recorded in acceptance. All 14 acceptance tasks are terminal; product/browser processes and owned jobs
are absent, API access is closed after uninstall, and the owned guest was stopped
with live Sandbox inventory empty. Private recovery files and database integrity
were verified separately and remain excluded from public receipts.

Public 0.2.0 release, merge/tag, site deployment and
post-delivery verification remain pending. [PR49](https://github.com/scottconverse/townrecord/pull/49)
is open. Download links in the manuals are prepared for publication, not evidence
that an asset is already public. Do not repeat completed acquisition/model jobs
or rebuild unchanged signed application bytes for documentation edits.

The installer includes bundled Python/FFmpeg, a pinned signed TextFlowKit seed
without model weights, app-local Microsoft CRT files and process-scoped certifi
CA handling. Data remains separate from program files; desktop and connector jobs
share the configured profile. Retired failed candidates and their preserved
recovery data are diagnostic history. Public hosting remains an operator deployment.

[Exact acceptance and remaining outcomes](COMMUNITY-ACCEPTANCE.md) ·
[Current state/ownership](COMMUNITY-RECORD-STATE.md) ·
[Windows setup](WINDOWS-COMMUNITY-OPERATIONS.md) ·
[Installer design](WINDOWS-INSTALLER-DESIGN.md) ·
[First-run design](WINDOWS-FIRST-RUN-DESIGN.md) ·
[Interface contract](COMMUNITY-INTERFACE-DESIGN.md)

## Historical 0.1.0 completion record
# TownRecord community completion handoff — November 13, 2026

Townlight is the umbrella. This is the bounded TownRecord Windows community-record
service, with a strict TypeScript browser interface and Python backend. There is
no Tauri step, desktop-shell rewrite, new operating-system package or new provider.

## What is complete

- Interface design was written before conversion: COMMUNITY-INTERFACE-DESIGN.md.
  TypeScript is strict and compiled into the Python wheel; operators need no Node.
  Current architecture documentation no longer proposes Tauri.
- The original three identity/reschedule/document-revision defects and operator/
  resident workflows are merged in PR39–42. PR43 completes the design/TypeScript
  increment and remaining bounded engineering acceptance.
- Full local transcription: 98-minute September16 Planning recording, Whisper small
  on CPU through TextFlowKit0.1.19, 1542 timed segments, completed in21m24s.
  Its isolated resident snapshot preserved machine/provisional origin, search of
  the final sentence and final recording timestamp98:16.
- Installed interface: body/date filters, keyboard opening/focus after refresh,
  cancellation/postponement corrections/history, document and recording moves
  with original associations restored, correct HTML download and narrow layout.
- Full Council and Planning evidence files imported through TownReporter browser
  as two New leads. Duplicate returned the same lead. Malformed file was rejected;
  valid retry succeeded. No article, model run, scan or publication was triggered.
- Synthetic lifecycle covered agenda first, later recording/transcript and later
  source-approved minutes, plus cancellation/continuation and explicit republication.
  Live two-body acquisition, source captions and original regression evidence reused.
- Fresh locked Windows environment ran the exact installed wheel. Acquisition,
  verified backup/restore, same token/identity/schedule and continued acquisition
  passed. An installed scheduler tick enqueued a source retrieval that completed;
  a second tick did not duplicate it.

## Exact delivery and evidence

TownRecord package source: 758abf6fa4a0f629bc2c54a4dd937ade423f364a.
Wheel: townrecord-0.1.0-py3-none-any.whl.
SHA256: 9cab2e58d36b3024caa095815854ae5d2d367708e3dba2ac6b539e0f17d2b5ce.
All147 payload files match. Later tests/docs do not require another build.
TownRecord PR43: https://github.com/scottconverse/townrecord/pull/43
Final merge/check status: PR43 merged at0f2ae54632d3a234626f353311a93de81bbcde57 after all checks
in CI43520915463 passed (including Windows).
TownReporter PR388 merged at755827c0f251bd8b3507da8295e11830e62a733d;
accepted head a66b331c254d34227a064f2f1648b1421efd3dbb, CI passed.

Maintained repo documents: README.md; docs/COMMUNITY-INTERFACE-DESIGN.md;
docs/COMMUNITY-ACCEPTANCE.md; docs/COMMUNITY-RECORD-STATE.md;
docs/WINDOWS-COMMUNITY-OPERATIONS.md; docs/TRANSCRIPTION-COMPARISON.md.
Sanitized receipts/screenshots: docs/evidence/community-2026-11-13/.
Local candidate and raw evidence remain in outputs/townrecord-community-acceptance
under C:/Users/scott/Documents/Codex/2026-11-02/rea. Raw lab profiles contain private
tokens; do not upload that folder wholesale or start its queued jobs for inspection.

## Owner decision and actual limits

Scott approved publication on November 13, 2026. The exact tested Python package
is now released; see the public-delivery record below. No signed MSI or hosted
TownRecord application service is claimed. Follow the Python-service installation.
No additional mandatory human trial has been invented. Scott defines human work.
Suggested next observation: an independent operator/resident uses the documented
workflow and checks transcript wording against audio. This is a suggestion, not
a hidden release condition. No participating city partnership, human accuracy
certification, months-long unattended operation or genuinely new publisher meeting
arriving after setup is claimed. Historical live retrieval and fixtures are distinct.
A provider file named Minutes is not proof of approval for its listing's meeting.

## Cleanup and prevention

Owned local API, preview, transcription and fresh/restored service processes stopped;
ports18290/18292/18310 are clear; transcription children exited. Browser test tabs
closed and viewport restored. No VM/Sandbox was created. Production was untouched.
Source, data, candidate and evidence are preserved.

The mistake was treating the existing broad desktop-shell plan and handwritten
JavaScript shortcut as acceptable without a dedicated design and reconciliation
with Scott's TypeScript/Python direction. Prevention is recorded in the authorized
memory note: design first, verify current owner stack, distinguish compiled JS
from source language, do not carry forward stale architecture as a requirement,
and let Scott define human acceptance. No framework was needed for this conversion.

At the initial implementation handoff, Big Picture had nine verified sources, including the
interface design and updated candidate acceptance. Existing file identities and
sharing were preserved.

## Documentation and website delivery — November 13

[PR45](https://github.com/scottconverse/townrecord/pull/45) merged at
`ffbd012d7b830cb09e3d9185dbd9ef6f6fa2e517` after all existing jobs passed in
[CI43532515496](https://github.com/scottconverse/townrecord/actions/runs/43532515496),
including the Windows TypeScript compile and new static website build.
[Post-merge CI43533143321](https://github.com/scottconverse/townrecord/actions/runs/43533143321)
is the automatic main-branch verification for that merge.

Delivered documents: [User manual](USER-MANUAL.md), [technical manual](TECHNICAL-MANUAL.md),
[website design/build/deploy guide](SITE.md), corrected README, workflow, core README
and interface-design links. Four Mermaid diagrams render on GitHub. The new manuals
cover new operators, residents, technical architecture, HTTP/API access and recovery.
The source-access guarantee and stale implementation-in-progress wording are removed.

The static landing page is in `site/`; `npm run build:site` produces `dist/site`.
It uses the existing application palette and a real acceptance screenshot.
Build and nine local links/assets/anchors passed; 41 relative documentation links
resolved. Ten unique external page URLs returned HTTP200 after the merge.
Desktop/tablet/mobile review passed at effective CSS widths1309/698/355 (existing
browser zoom preserved): no horizontal overflow, image loaded, anchors worked and
keyboard focus was visible. Text contrast was at least6.4:1. All diagrams were
checked in GitHub's actual renderer; a sequence delimiter defect was corrected.

The Big Picture Project now has13 sources. The user manual, technical manual,
website guide and README were added to **Project sources**, not just the supporting
folder. Its existing interface-design and completion-handoff documents were updated.
Drive copies use absolute GitHub links. Project URL:
https://drive.google.com/drive/u/0/project/1oOMbBp0vHKu_c6bOzqlojqQbDwxNvhoT

Local evidence: `landing-desktop.png`, `landing-tablet.png`, `landing-mobile.png`
and `big-picture-manuals-sources.png` in the existing acceptance output folder.
The loopback website preview/session31172 was stopped, port18320 is clear, browser
viewport reset and owned tabs closed. No application profile or VM was started.
The superseded PR run43532395907 was cancelled; the final-head run passed.

The documentation-stage publication hold was lifted by Scott on November 13.
The accepted wheel is unchanged. No additional implementation or human trial is imposed.

## Public release and website — complete

Scott approved tagging, publication and deployment on November 13, 2026.

- Release: https://github.com/scottconverse/townrecord/releases/tag/v0.1.0
- Website: https://scottconverse.github.io/townrecord/
- Tag `v0.1.0` points to tested source `758abf6fa4a0f629bc2c54a4dd937ade423f364a`.
- Wheel SHA-256 remains `9cab2e58d36b3024caa095815854ae5d2d367708e3dba2ac6b539e0f17d2b5ce`.
- Published as a community-preview pre-release, with nine assets: original wheel,
  corresponding-source ZIP, checksums, provenance, sanitized acceptance evidence,
  acceptance summary, Windows install guide, user manual and technical manual.
- All nine assets were downloaded anonymously and matched their local SHA-256.
  The source ZIP includes the lockfile and vendored QuorumRelay. Of147 payloads,
  145 are byte-identical to Git source; two differ only by Windows line endings.
  No application content changed and the wheel was not rebuilt.
- The public wheel and archived lockfile installed into a fresh temporary Windows
  Python3.13.5 environment. TownRecord0.1.0 / QuorumRelay0.1.0.dev11 and CLI help
  passed. No product service/profile was started. The temporary environment and
  extracted source copy were removed; downloads and receipts are preserved.
- PR47 merged at `a9b66620fbf68f7f192411b308cfec6388993d20` after all existing
  checks passed in CI43557289241. Automatic post-merge run:43557671485.
- Pages deployment43557671448 succeeded from that commit. Public HTML matches
  the build after newline normalization; CSS and screenshot match byte-for-byte.
  All14 linked page/asset URLs returned HTTP200. Chrome desktop/mobile review
  passed with no horizontal overflow, loaded image and working release button.
- Existing13 Big Picture Project sources retain their identities and sharing.
  The handoff, README, manuals/site guide and acceptance publication status are
  updated in place; this is not a supporting-folder-only delivery.

Evidence folder: `outputs/townrecord-public-download-verification` contains
`verification.json`, `website-verification.json`, `website-live.png` and
`website-live-mobile.png`. Release upload files remain in
`outputs/townrecord-community-release-0.1.0`.
No local service, model process, Sandbox or VM was started for publication.
Browser tabs closed and viewport reset. No additional publication approval is
outstanding for this release; no future product expansion is implied.
