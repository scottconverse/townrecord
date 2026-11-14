# Active Windows installer delivery — November 13, 2026

Scott authorized implementation, signing, clean Windows acceptance, documentation,
push, merge, tag and publication of TownRecord 0.2.0. Preserve v0.1.0. Keep the
TypeScript browser/Python service stack; no Tauri. Normal operators must not need
Python, commands or copied tokens. Public hosting remains a separate deployment.
Root coordinates/audits/delivers; implementation belongs to the authorized agents.

Branch feat/windows-installer-020; PR49. Current packaging source:
497a1696e62ab68862f16da211c5fb116da09d32; required CI43564202207 passed.
Wheel remains byte-identical to the initially tested app:
bcaa66a4726dc648a8b420fa96c7548230dbaaf2bc6bc1e06d93cc9acf55af85.
Native signing43564421083 PASSED: 82 payload PE files plus Inno internal executable,
Scott Converse Valid/timestamp verified; two Microsoft DLL signatures preserved.
Independent content checks permit only Authenticode-related byte changes.
Compiled installer SHA81d90152e292164bac29bd26f4dd12777c780f008dd4de65fd002cce985da6f8,
96,777,921 bytes. Outer signing43564959702 PASSED; final signed SHA547ce1abac70309beab32cb0604647d848e346f88a96f3d6b21864e7701f919b (96,793,560 bytes). Clean Install and Verify PASSED; all85installed PE signatures Valid. Headless browser setup PASSED in Edge157 using supported JavaScript Playwright with signed Node (Python test-only greenlet was blocked).
Current local doc count corrections do not alter this candidate or require rebuild.

Observed defects corrected: first installer was blocked by Windows Application
Control because inner setup and bundled programs were unsigned. First native batch
rejected two upstream Tcl/Tk DLLs (0x800700C1). Browser app/QuorumRelay do not use Tk;
existing build now excludes that optional toolkit. Failed evidence is retained
under outputs/townrecord-windows-020/failed. No Windows policy was weakened.

Owned guest 3bca7846-b403-4ac2-916e-b33763117c21 remains running headlessly, 8GB;
no TownRecord app installed yet. Both visible remote-session clients were closed.
Stop after evidence preservation: wsb stop --id 3bca7846-b403-4ac2-916e-b33763117c21.
Owned temporary signing draft: civiccast/townrecord-020-signing-2acd5cde; helper
branch townrecord-signing-020-20261113. Root owns dispatch and cleanup. See job JSON
in outputs/townrecord-windows-020 for run IDs, start times and cancellation commands.

Global rule: headless while Scott uses the machine or occupancy is unknown. No
visible browser/app/Sandbox/VM windows or interactive control without approval.
No production, QuorumRelay or unrelated desktop changes.

Remaining: retrieve/verify outer signature; install in existing guest; run prepared
headless setup/review/recovery phases, real managed tool setup/sample transcription,
startup/reinstall/uninstall retention. Managed downloaded native binaries remain an
unverified policy risk, not an observed failure or authority for speculative work.
Then finish current docs/evidence, merge/tag/release/Pages, public download and live
site verification, update existing Big Picture project sources, owned cleanup.
Reuse unaffected v0.1.0 domain evidence with attribution.
Next action: runtime agent diagnoses actual TextFlowKit job2 WinError4551 on generated console executable, probing supported module/native dependencies before any larger fix. Packaging agent diagnoses actual yt-dlp job1 sample failure and PrimeGov jobs3/4 provider_page_failed from raw installed logs/public HTTP. UI settings, one-use login, 31 choices, body/source save and job submission passed; no successful acquisition, active tools, review/recovery or transcription yet. Existing guest remains headless with app running. External harness task phases run as WDAG session1 via limited InteractiveToken; ExistingLogin CLI fails for disconnected session. No publication/merge/tag yet.
---
## Preserved prior delivery record
# TownRecord bounded completion state — November 13, 2026

## Contract
Design first; strict TypeScript browser interface/Python service; no desktop shell.
Finish engineering items1–6, reuse unaffected evidence, push/merge on pass.
Scott approved this exact candidate publication on November13. Scott defines human work.
No production changes, new OS packaging, new providers or delegated agents.

## Candidate
Packaged source758abf6fa4a0f629bc2c54a4dd937ade423f364a; wheel SHA256
9cab2e58d36b3024caa095815854ae5d2d367708e3dba2ac6b539e0f17d2b5ce.
All147 payload files match. Later commits contain tests/documentation/evidence only.
The packaged source passed CI43518744977, including Windows.
TownRecord PR43 is the TypeScript/design/completion increment; original PR39–42 merged.
TownReporter PR388 merged at755827c0f251bd8b3507da8295e11830e62a733d;
accepted head a66b331c254d34227a064f2f1648b1421efd3dbb, required/post-merge CI passed.
v0.1.0 and the website are published; this is a Python service package, not an MSI.

## Completed engineering
1. Full local Whisper small CPU job70 completed the5900-second Planning recording:
   1542 timed segments through5897.220s. Published only in isolated lab revision2;
   resident search/origin/final98:16 recording link passed.
2. Design written before TypeScript conversion. Strict compile, installed filters,
   keyboard/focus, correction/history, document/recording reassignment/restoration,
   correct HTML download and narrow resident layout passed. Mistake/prevention
   recorded in authorized memory note. No framework or runtime Node added.
3. Full Council/Planning browser handoff into isolated TownReporter passed. Two
   New leads, duplicate preserved, malformed file rejected, valid retry succeeded;
   no scan/model/article/publication. Full-content retention tests reused.
4. Staged agenda -> recording/transcript -> source-approved minutes publication
   and cancellation/continuation tests passed. Existing identity/revision tests
   and live two-body acquisition/captions reused with their stated limits.
5. Fresh locked environment/exact wheel served, acquired4 documents; verified
   backup/restore retained identity/token/schedule; restarted restored service
   acquired4more documents. Actual installed scheduler due tick and real scheduled
   source retrieval passed; duplicate tick queued nothing. No tool-cache backup claim.
6. Acceptance and sanitized evidence are in docs/evidence/community-2026-11-13.
   Design verified in actual Big Picture Project sources, not only supporting folder.

## Delivery
PR43 merged at0f2ae54632d3a234626f353311a93de81bbcde57 after final-head
CI43520915463 passed all existing jobs, including Windows. Engineering items1–6
are complete. This documentation follow-up records the merge without rebuilding.
Final handoff, design and updated acceptance are verified sources in the actual
Big Picture Project (now thirteen sources including the documentation follow-up). Scott subsequently approved publication; see the final delivery below.
See COMMUNITY-COMPLETION-HANDOFF.md for the next agent's starting point.

## Ownership and limits
All local test services/workers stopped; loopback18290/18292/18310 clear and
transcription children exited. Browser test tabs closed; viewport restored.
No VM/Sandbox was created. Raw evidence/candidate preserved; no production touched.
PR43 CI is complete. GitHub may automatically run its normal main-branch CI;
that does not alter the accepted wheel or authorize a release.
Independent community trials are suggested, not mandatory until Scott says so.
No genuinely new post-setup publisher meeting or human transcript certification
is claimed. Historical live retrieval and synthetic lifecycle tests are distinct.

## Documentation delivery (November 13)

PR45 merged at ffbd012d7b830cb09e3d9185dbd9ef6f6fa2e517 after all existing CI
jobs passed in run43532515496. Automatic post-merge run:43533143321.
User and technical manuals, four verified Mermaid diagrams, corrected README/
workflow and static landing page are on main. Build, local/remote links, responsive
browser review and keyboard focus passed. Full details: COMMUNITY-COMPLETION-HANDOFF.md.
Big Picture has13 verified Project sources, including the new manuals/site guide/README.
Its existing design and handoff documents were updated. Preview stopped, port18320
clear, browser tabs closed, viewport restored. No product profile or VM started.
Tagging/release and website deployment were subsequently approved and completed.
No product rebuild, additional engineering requirement or human trial is imposed.

## Public delivery — complete

Release: https://github.com/scottconverse/townrecord/releases/tag/v0.1.0
Website: https://scottconverse.github.io/townrecord/
Tag targets exact accepted source758abf6; wheel hash unchanged. Nine public assets
were anonymously downloaded and hash-verified. Fresh downloaded-wheel dependency/
CLI install check passed; temporary environment removed. No product service started.
PR47 merged at a9b6662 after all CI43557289241 checks passed. Pages43557671448
passed. Automatic post-merge CI:43557671485. Live desktop/mobile and14 URL checks
passed. Updated existing Big Picture sources in place;13 Project sources retained.
No owned local process or browser tab remains. Publication is complete with no
remaining owner approval for this release. See COMMUNITY-COMPLETION-HANDOFF.md.

Integration check session10269 finished:1580passed48skipped1failure (new public desktop/info not yet in explicit anonymous-route guard). Runtime agent correcting exact exception and checking response; unaffected tests reused. Ruff/UI/site/packaging checks pass. No Sandbox started.

Candidate source2acd5cde0ea1d72a1e9ac24f96db1040c320ab43 built clean. Unsignedsetup99003002bytes SHA35f9c91e1b46e75c0f6723ee2ec01600e142b644def52e71d8066d3d8a7bff70. Azure signing run43561666388 active; CI43561395186 active. No Sandbox started yet. Buildowner43712 exited. BigPicture twoWindowsdesigns added andverified actualProject15sources; existinghandoffupdated in-place. Next retrieve signedartifact, independentlyverify, runownedSandbox. Signingintake townrecord-020-signing-2acd5cde isownedtemporarydraft; clean afterverifiedpublicdelivery.

Current checkpoint: CI43561395186 PASSED alljobs; Azure43561666388 PASSED. Independent signedsetup Valid ScottConverse timestampverified SHA4a52333a0760a0cccc92ab1803d2c9c504f6c3e8c5684d8e9ed776a4a37ba0c1. OwnedSandbox3bca7846-b403-4ac2-916e-b33763117c21 RUNNING; cancellation wsb stop --id3bca7846-b403-4ac2-916e-b33763117c21. Clean install FAILED exit1: ApplicationControl event3077 blocks unsignedInno temporaryselfcopy. Notpublished. Agentwindows_packaging fixingofficialtwo-passSignedUninstaller/compile-only flow; reuseexistingwheel/payload then signinternalstub andoutersetup. No securitypolicychanges. Elevatedhelperjobbbc6251cf6d14bb6bb0b36d5c30584c7 couldnotreachSandboxbroker; normal signed-in guardedlauncher createdguest. NativeCUA Sandbox capture unavailable (returnsbackground); UI partclosed fornow, gueststillup. TwoRDPclients mayremain; do notmistake for2guests. Exactevidence outputs/townrecord-windows-020. Next commitpackagingfix, signinternalstub, repack/signouter, retest sameguest. Rootmustresumeexistingwork, notreportdone.

## Current installed failure resolution

Headless first-run UI passed on the exact installed 497a1696 candidate. Jobs exposed
real failures: TextFlowKit console executable and numpy/torch/tiktoken native loads
were blocked by Application Control; PrimeGov and yt-dlp failed TLS issuer lookup.
Read-only diagnostics proved bundled certifi SSL_CERT_FILE resolves both exact
network paths without disabling TLS verification. Runtime agent owns launcher CA
configuration plus child-environment allowance, supported Windows module invocation,
and pinned signed-seed bootstrap. Packaging agent owns seed dependency pins/build,
native signing coverage and deterministic final seed manifest. Contract: app/runtime-seeds/
textflowkit/0.1.19/{manifest.json,Lib/site-packages/**,Scripts/python.exe,optional pythonw.exe};
no generated console executables or pyvenv.cfg. Manifest files carry final signed
SHA256 values; runtime verifies/copies then generates config for installed Python.
First bootstrap can use verified bundled seed offline with explicit reason; existing
online checks, probes, active-pointer and rollback behavior remain. No new channel,
policy changes or model weights in installer. These fixes require a new candidate;
current installation is diagnostic evidence, not final acceptance. Agent implementations
are pending; root must inspect affected paths, test, build/sign and verify exact final
candidate headlessly. Existing guest contains failed jobs and setup data, not a passed
acquisition/transcription workflow. No merge/tag/publication yet.
