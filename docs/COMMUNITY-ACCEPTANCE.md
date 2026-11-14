# TownRecord Windows 0.2.0 candidate acceptance

## Current scope and status — November 14, 2026

Build provenance, payload/installer signing and required CI are recorded below.
**Clean installed acceptance is PENDING. No 0.2.0 public release or final acceptance
is claimed.** PR49 remains the active delivery. The preserved 0.1.0 record below
covers its own package and cannot establish this installer's first-run behavior.

## Exact prepared candidate

| Layer | Recorded identity/evidence |
|---|---|
| Application source | `4ca190e3449604488dd765a39f6f33e3d927604c` |
| Wheel | `townrecord-0.2.0-py3-none-any.whl`; SHA256 `4cd30b55bfb691a0fb849464855a2d8894110385455561e00f1abddcd3bff8d0` |
| Packaging source | `02b66993cb741c6d571f67d2031f7946c8b6b001`; uses the unchanged application wheel above |
| Required CI | Run 43569493649 attempt 2: Windows, Ubuntu and macOS jobs successful; [queried receipt](evidence/windows-0.2.0/required-ci.json) |
| Native signing | Run 43569539324: 125 newly signed PE files, comprising 124 payload files plus one internal installer executable; 22 already valid vendor signatures preserved. All 125 returned entries Valid; [original receipt](evidence/windows-0.2.0/native-signing-receipt.json) |
| Native overlay | SHA256 `55ba405951af47eeb4bc60c4b9257db656fa28715cb3a742052a69749c5878e8` |
| Final seed manifest | SHA256 `0bf631371bf3247f35c1c80e28b341821899fcc0b667648270ede12bc404d41f`; [derived from verified signing hashes](evidence/windows-0.2.0/signed-seed-derivation.json) |
| Compilation | Inno Setup 6.7.3; compiler exit 0. Unsigned outer 221,365,875 bytes; SHA256 `ac7e2d8e71ddd10724c4612060b77783577dde3e0ff756548ca3a1b2afcfc9cc`; [build/repack provenance](evidence/windows-0.2.0/build-provenance.json) |
| Outer signing | Run 43570543904; final 221,381,512 bytes; SHA256 `01af39b6984aea6c09770a5a864cb40d4de99aa161d6f4ea4b138d3af263f719`; [original receipt](evidence/windows-0.2.0/outer-signing-receipt.json) |
| Independent signature verification | Scott Converse Authenticode signer and Microsoft timestamp recorded as verified at 2026-11-14T07:22:20.4927023Z; [receipt](evidence/windows-0.2.0/independent-signature-verification.json) |

The candidate carries bundled Python, FFmpeg and a pinned TextFlowKit 0.1.19/Whisper
native dependency seed without model weights. It verifies/copies that seed into
per-user managed storage, invokes the supported Python module and checks native
imports before activation. Desktop certificate-bundle configuration preserves TLS
verification and operator overrides. These implementation facts and passing tests
are distinct from observing the final installer perform the workflow.

## Installed outcomes still pending

| Agreed outcome | Current final-candidate evidence |
|---|---|
| Clean normal-user installation and bundled runtime/signatures | PENDING; build/signing receipts are not installed acceptance |
| Start Menu one-use sign-in, first-run settings and source/tool setup | PENDING on the exact final signed installer |
| Real source/video retrieval and installed local transcription | PENDING; prepared 120-second public meeting audio is a sample, not the historical full 98-minute workflow |
| Operator review/publication, resident access and applicable handoff checks | PENDING final installed observations; unchanged v0.1.0 evidence may be reused with explicit attribution |
| Graceful stop/reopen, scheduler/startup, verified recovery, reinstall/update and uninstall data retention | PENDING |
| Authorized merge/tag/release/site delivery and post-delivery download/site verification | PENDING |

The retired 497a1696 candidate's Install/Verify/UI observations and its native/TLS
failures remain diagnostic evidence. They must not be presented as final candidate
acceptance. New receipts will name their actual installer hash and scope. See the
[curated build/signing evidence](evidence/windows-0.2.0/README.md) and
[current handoff](COMMUNITY-COMPLETION-HANDOFF.md). Private profiles, credentials
and raw full logs are excluded from the public evidence directory.

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
