# TownRecord Windows 0.2.0 acceptance

## Current scope and status — November 14, 2026

The exact signed Windows candidate passed clean ordinary-user installation,
first-run setup, real source acquisition, managed local transcription and browser
review/publication. **Installed acceptance is complete within the scope below. Public delivery and
post-delivery verification remain pending.**
[PR49](https://github.com/scottconverse/townrecord/pull/49) is open. The download
and release links in the manuals are prepared for publication; no public 0.2.0
availability or completed Pages deployment is claimed at this checkpoint.

## Exact signed candidate

| Layer | Identity/evidence |
|---|---|
| Application and packaging source | `3a247d8865f1692911f4bed15cae370410ec6d70` |
| Wheel | `townrecord-0.2.0-py3-none-any.whl`; SHA256 `88857f9b821e67a273bb5197356a2125a04fbfad3961124fb1da9e0ba8da0bec` |
| Signed installer | `TownRecord-0.2.0-windows-x64-setup.exe`; 221,865,160 bytes; SHA256 `e08a4f4447abb611a331cd7146df56493eb92f76bc14c9a660cb80fb9c3067a8` |
| Required CI | [43573893300](https://github.com/scottconverse/townrecord/actions/runs/43573893300), all three platform jobs passed; [receipt](evidence/windows-0.2.0/candidate-3a247d8/required-ci.json) |
| Native signatures | Original signing 43569539324: 125 newly signed files (124 payload plus one internal); exact native input identity verified for reuse. Corrected app-local Microsoft CRT files retain vendor signatures. [Original receipt](evidence/windows-0.2.0/candidate-3a247d8/native-signing-original-receipt.json), [reuse receipt](evidence/windows-0.2.0/candidate-3a247d8/native-signing-reuse.json) |
| Native overlay / final seed | `c9a5ec586bfdeadf4f0723b4af3ad95f48d16b62ba8bab30f062b5973771ce2b` / `0bf631371bf3247f35c1c80e28b341821899fcc0b667648270ede12bc404d41f` |
| Build and outer signing | Inno 6.7.3 compiled successfully; outer signing 43574774784 passed. [Build](evidence/windows-0.2.0/candidate-3a247d8/build-provenance.json), [outer signing](evidence/windows-0.2.0/candidate-3a247d8/outer-signing-receipt.json) |
| Independent verification | Valid Scott Converse Authenticode signature and Microsoft timestamp, verified 2026-11-14T08:17:16Z; [receipt](evidence/windows-0.2.0/candidate-3a247d8/independent-signature-verification.json) |

The installer bundles Python 3.13.15, uv, FFmpeg, a pinned signed TextFlowKit 0.1.19/
Whisper seed and the coherent app-local Microsoft VC runtime. Model weights are
not bundled. Seed setup verifies/copies actual signed file hashes and uses the
supported module entry point. Desktop certifi CA defaults preserve explicit
operator overrides and TLS verification. The desktop API and connector jobs use
the same configured profile, including after restart.

## Observed exact installed outcomes

| Agreed outcome | Observed result |
|---|---|
| Clean normal-user installation | PASSED without preinstalled Python/uv/FFmpeg commands. All 23,094 installed payload files, 124 signed payload replacements and 18,330 seed files verified against the exact manifests. Python 3.13.15, FFmpeg/ffprobe, uv and the opt-in startup shortcut verified. [Payload receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-payload-verification.json) |
| First-run access/settings/source/tool setup | PASSED through headless installed Edge under an ordinary account: one-use launch, fragment removal, anonymous private-read rejection, fresh settings restart, two bodies, PrimeGov validation/source save and actual managed tool jobs. Provider selector matched 31 installed choices; this does not prove 31 live providers. yt-dlp 2026.10.26 and TextFlowKit 0.1.19 active. [Workflow observations](evidence/windows-0.2.0/candidate-3a247d8/installed-workflow-observations.json) |
| Live bounded acquisition | PASSED September 8–16 Longmont PrimeGov sync: 12 download jobs, 9 PDF extraction jobs and 3 caption captures completed. Missing official minutes and one absent primary video remain explicit paused gaps. Source captions are distinct from machine transcription. [Job counts and gaps](evidence/windows-0.2.0/candidate-3a247d8/installed-workflow-observations.json) |
| Direct installed managed transcription | PASSED 120-second Council audio sample (source interval 600–720s), Whisper small/en on local CPU, exit 0 in 39.398s, 34 segments, 329 words, final timestamp 119,820ms. Both managed environments use installed bundled Python; certifi CA path passed to children with verification enabled. [Receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-transcription-observation.json) |
| Operator publication and resident access | PASSED actual UI review/publish/export for Council meeting 3 (3 documents, 921,653-byte export) and Planning meeting 4 (4 documents, 503,299-byte export), revision 1. Export schema/instance/revision and full payload matched public snapshots; resident links opened. Sign-out cleared private containers and reload offered Start Menu reopen without a token form. This is new TownRecord export evidence; TownReporter receiver/browser imports remain historical 0.1.0 evidence. [Receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-workflow-observations.json) |
| Verified desktop backup/restore | PASSED existing-operation continuation: 19 backup files rehashed, separate restored profile active, original database retained, known publication/documents readable through the UI and sign-out verified. Initial harness fragment-only navigation made no exchange request; a fresh document completed readback without repeating backup/restore or changing product code. [Recovery receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-recovery-observation.json) |
| Graceful stop/reopen, startup registration, same-version reinstall and uninstall retention | PASSED: exact signed 0.2.0 reinstall preserved config/database/artifact/DPAPI fingerprint; post-reinstall all 23,094/124/18,330 payload/signature-replacement/seed entries verified, startup target and service reopen passed. Uninstall stopped desktop/profile, removed interpreter, exact HKCU product registration, Open/Stop Start Menu links and startup shortcut; saved data/config/credentials remained unchanged. This proves same-version repair, not a future-version migration. [Lifecycle receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-lifecycle-observation.json) |
| Acceptance cleanup | PASSED: 14 owned tasks terminal, 0 product/browser processes or owned jobs, API unreachable after uninstall, private recovery hashes and 3 SQLite integrity checks verified, owned guest stopped and live Sandbox inventory empty. [Cleanup receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-cleanup-observation.json) |
| Authorized merge/tag/release/site and post-delivery verification | PENDING. Installed acceptance above is complete; no public 0.2.0 publication/deployment claimed yet. |

The existing v0.1.0 scheduler tick/deduplication evidence is retained below; no
new overnight run or machine wake-up is claimed. The optional startup target was
verified and service reopened; a new interactive Windows sign-in was not simulated.

The 120-second direct installed transcription proves this packaged tool path; it
is not a new full-meeting transcription or a claim of transcript accuracy. The
historical 98-minute transcription, staged-arrival fixtures, original corrections
and TownReporter receiver/browser acceptance below remain attributed to 0.1.0.
Neither evidence tier establishes every provider, human certification, city
adoption or months of unattended operation.

Retired 497a and 02b candidates remain diagnostic history. The latter exposed
missing CRT dependencies and a desktop storage-root mismatch. The modified-guest
four-DLL import proof established the packaging fix; it does not replace the clean
corrected-candidate results above. Sanitized receipts omit private profile paths,
full configuration/database/token material and raw logs. [Current handoff](COMMUNITY-COMPLETION-HANDOFF.md).

---
## Preserved historical v0.1.0 acceptance

# TownRecord community candidate acceptance

**Publication update — November 13, 2026:** Scott approved publication of this
unchanged tested candidate as [v0.1.0](https://github.com/scottconverse/townrecord/releases/tag/v0.1.0).
The [website](https://scottconverse.github.io/townrecord/) is deployed. The original
acceptance observations and pre-publication wording below remain historical evidence.

November 13, 2026. Engineering acceptance of a bounded Windows local-service
candidate. Tagging and publication remain held for Scott. Independent community
trials are suggestions unless Scott makes them requirements.

## Exact identity

- TownRecord source: `758abf6fa4a0f629bc2c54a4dd937ade423f364a`.
- Implementation merged through PRs [39](https://github.com/scottconverse/townrecord/pull/39),
  [40](https://github.com/scottconverse/townrecord/pull/40) and
  [41](https://github.com/scottconverse/townrecord/pull/41).
- Tested wheel: `townrecord-0.1.0-py3-none-any.whl`, SHA-256
  `9cab2e58d36b3024caa095815854ae5d2d367708e3dba2ac6b539e0f17d2b5ce`.
  All 147 packaged source/web/migration files matched the candidate checkout.
- Installed into a separate Windows Python 3.13.5 environment with locked dependencies;
  readback used the installed package, not a PYTHONPATH overlay.
- TownReporter handoff: [PR388](https://github.com/scottconverse/TownReporter/pull/388),
  candidate `a66b331c254d34227a064f2f1648b1421efd3dbb`, merged as
  `755827c0f251bd8b3507da8295e11830e62a733d`; required and post-merge checks passed.
- TypeScript conversion and design: [PR43](https://github.com/scottconverse/townrecord/pull/43).
  Tests/documentation after the packaged source do not change its 147 payload files.
- This is a Python service plus strict TypeScript browser UI. No TownRecord MSI, signed desktop
  installer or public deployment is claimed. Tagging/publication is held.

## Observed results and limits

| Outcome | Observed evidence | Limit |
|---|---|---|
| Original identity/revision defects | Fixes and regression tests merged in PR39; connector/workflow repairs in PR40 | Does not establish every provider |
| Two-body source configuration/acquisition | Longmont PrimeGov September discovery yielded ten meetings for Council and Planning; other bodies explicitly outside coverage | Public-source evaluation, not an agreed city partnership |
| Repeat acquisition | Repeated discovery preserved all ten meeting identities | Not a months-long scheduling observation |
| Documents and text | Council September 8: three documents, 559 extracted PDF pages. Planning September 16: four documents, 86 pages | A provider attachment named Minutes is not proof that it contains approved minutes for that same meeting |
| Failed download recovery | The previously failed job4 was retried through the installed operator API; job69 completed; repeat retry request returned the same job | API plus real worker evidence, not a fresh browser-click recording of this retry |
| Full source captions | Council: 5,787 segments; Planning: 2,415 segments. Both capture jobs completed | Automatic captions remain provisional, not certified minutes |
| Resident transcript navigation | Installed browser opened the Council deep link, rendered provisional origin and timestamp links through the final segment | No full human line-by-line transcript review |
| Full local transcription | Installed browser button queued job70 for the 5,900-second Planning recording. TextFlowKit0.1.19 / Whisper small / CPU completed in21m24s:1,542 local-origin timed segments through5897.220s. Final line retained: Have a good evening. | Provisional machine text, not full human accuracy certification. Both-engine clip comparison reused; no new benchmark |
| Runtime setup and transcription control | Installed operator browser setup/check job57 completed; browser local-transcribe action now completed full job70. Parked unrelated lab jobs restored afterward | Explicit operator fallback on a meeting with captions; it does not claim that its publisher lacked captions |
| Review/publication/privacy | Synthetic browser lifecycle exercised review, publication, search, correction isolation, withdrawal, sign-out and narrow viewport; API tests cover unauthorized/private access | Agent-run tests, not an independent resident usability trial |
| TownReporter browser handoff | Full 2,020,167-byte Council export became New lead1; duplicate returned lead1. Invalid file rejected; valid Planning file then became New lead2. Queue showed exactly two New leads | Isolated Windows preview, not production; no scan, article or model run |
| Complete TownReporter handoff | Complete Council and Planning exports passed the actual receive function and isolated database, retained final transcript text and duplicate identity | Full receive-function retention checks are now supplemented by the full-file browser import above |
| Backup/restore and continued acquisition | Fresh locked environment and installed wheel ran CLI serve. September23 Planning acquisition produced four documents. CLI backup/restore verified five files; restored token, instance identity and schedule survived. Restored serve acquired September16 and four more documents | Tool caches and external credentials are not backup contents; fresh profile explicitly waits for tool setup. Existing public200/private401 restore checks reused |
| Daily schedule | Installed scheduler due tick enqueued source sync; second tick enqueued nothing; real scheduled source job23 completed. Existing DST/thread/restart tests reused | Focused cycle, not a months-long observation. Tool checks queued/paused as configured and were not rerun |
| TypeScript UI | Strict compilation; body/date filters, keyboard opening and restored focus after refresh; cancellation/postponement/history; document and recording reassignment restored afterward; HTML agenda downloaded as .html; narrow resident layout had no horizontal overflow or operator controls | Existing publication/privacy evidence reused; no formal accessibility certification |
| Staged record lifecycle | Synthetic agenda-only publication, later recording/local-origin transcript, later source-approved minutes, cancellation/continuation and explicit republication passed | Fixture minutes approval wording is attributed to the fixture source; not an assertion about live Longmont minutes |
| Candidate CI | Packaged source758abf6 passed existing CI43518744977, including Windows. PR43 final head8286b30 (including staged lifecycle regression) passed CI43520915463 and merged at0f2ae546 | Existing repository matrix retained; no new OS packaging work |

Two full-record handoff defects were found and fixed before merge: the complete
Council JSON exceeded the old 2 MB limit (now 8 MiB, byte-counted), and PDF-derived
null characters could not be stored as JSONB. The exact JSON snapshot is now stored
as text so escaped characters survive. Regression tests verify complete text,
escaped-character roundtrip, oversized Unicode refusal and editorial retry safety.

## Observational limits and owner decisions

No participating city partnership or independent resident/operator trial is implied.
Scott decides required human work; an independent trial is a suggestion, not an
agent-imposed gate. A genuinely new meeting published after setup has not been
observed; historical live retrieval, repeat identity and staged fixtures are named
separately. Machine transcripts require review before treating their wording as fact.
A source attachment called Minutes does not establish that it is approved minutes
for the meeting under which it was listed. All-provider live acceptance and the
wider specification's question-answering backlog are outside this bounded delivery.

## Reproduce and maintain

Follow [Windows operations](WINDOWS-COMMUNITY-OPERATIONS.md) using the exact source
above, a separate profile and publicly accessible sources. It documents Python,
uv, FFmpeg, managed tools, operator tokens, service startup, daily retrieval,
publication boundaries, handoff and backup/restore. Do not put tokens in Git.

Local raw receipts, public-source exports, scripts, screenshots and the wheel are
preserved in `outputs/townrecord-community-acceptance` in the implementation
workspace. That directory also includes private lab tokens/database: do not upload
it wholesale. `package-provenance.json`, `two-body-final-readback.json`,
`repeat-discovery.json`, `installed-retry-receipt.json`, `restore-readback.json`,
`full-record-handoff-receipt.json` and the two engine comparison JSON files identify
the specific observations. Screenshots are `packaged-resident-transcript.jpg` and
`townreporter-handoff.jpg`. The comparison document is also a verified source in
The Big Picture Google Drive Project.

No production instance was modified. See the concise state record for current
owned processes, remaining work and CI. Do not start scheduled processing against
the lab merely to inspect evidence; queued follow-ups are deliberately preserved.

Full local result: `full-transcription-result.json`; provenance includes audio SHA256,
model/tool/device and transcript ID. The isolated published snapshot is revision2. Resident search found its final
sentence; the page showed provisional machine origin and final recording link98:16.
See `full-local-browser-receipt.json`.

New receipt files: `typescript-package-provenance.json`, `typescript-ui-receipt.json`, `full-browser-handoff.json`,
`deployment-lifecycle-receipt.json`, `scheduled-cycle-receipt.json`, and
`lifecycle-check.txt`. UI evidence: `typescript-resident-mobile.jpg` and
`townreporter-full-import.jpg`. Design was verified as an actual Big Picture
Project source, with `big-picture-design-source.jpg` saved locally.

Sanitized new receipts and screenshots are committed in
[evidence/community-2026-11-13](evidence/community-2026-11-13/). Private tokens,
raw lab databases and environment credentials are excluded.
