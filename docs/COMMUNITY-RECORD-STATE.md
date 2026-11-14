# Active Windows installer delivery — November 14, 2026

Scott authorized implementation, signing, clean Windows acceptance, documentation, push, merge, tag and publication of TownRecord 0.2.0. Preserve published v0.1.0 and per-user data. Keep TypeScript/Python; no Tauri, new provider or update channel. Normal operators need no Python, terminal commands or copied tokens. Root owns integration, acceptance and delivery within the existing bounded completion contract.

## Current corrected candidate

[PR49](https://github.com/scottconverse/townrecord/pull/49) merged 73f282e after final CI 43576964924 passed. v0.2.0 is public as a community-preview pre-release with 29 assets and exact 3a tag. Final documentation is on `docs/windows-020-public-handoff`; separate main checkout is preserved.

- Application and packaging source: `3a247d8865f1692911f4bed15cae370410ec6d70`.
- Wheel `townrecord-0.2.0-py3-none-any.whl`: SHA256 `88857f9b821e67a273bb5197356a2125a04fbfad3961124fb1da9e0ba8da0bec`.
- Required [CI 43573893300](https://github.com/scottconverse/townrecord/actions/runs/43573893300) PASSED all three platform jobs, including strict TypeScript/package verification on Windows.
- Build and compilation PASSED. Unsigned outer SHA256 `8513c25113c857dd2c3e772c725c174af0d7ad172ebb53ce93dfdb24676e1063`, 221,849,525 bytes. This unsigned identity is not a deliverable signature claim.
- Outer signing 43574774784 PASSED. Independent Scott Converse signature and Microsoft timestamp verification PASSED at 08:17:16 UTC. Signed installer SHA256 `e08a4f4447abb611a331cd7146df56493eb92f76bc14c9a660cb80fb9c3067a8`, 221,865,160 bytes. This is the exact installed candidate; bounded installed acceptance is complete.
- Unchanged native inputs reuse original signing 43569539324 through exact path/kind/target/hash/size checks. The new verified overlay is `c9a5ec586bfdeadf4f0723b4af3ad95f48d16b62ba8bab30f062b5973771ce2b`; original signing receipt identities are retained. Final derived seed manifest remains `0bf631371bf3247f35c1c80e28b341821899fcc0b667648270ede12bc404d41f`.
- Curated current receipts: [candidate-3a247d8](evidence/windows-0.2.0/candidate-3a247d8/). Previous candidate receipts remain in the parent directory under their original identities. Staging is `outputs/townrecord-windows-020/release-staging-3a247d8`; it contains the exact verified signed installer, source, wheel, source/license archives and receipts. Earlier staging is preserved separately.

The corrected candidate includes official pinned Microsoft-signed VC runtime 14.44.35211.0 files deployed beside bundled Python, with license/provenance and vendor signatures preserved. Desktop connector jobs bind to the configured desktop database/storage environment on every service start (source fix 7eb098e; 23 affected tests passed). No systemwide redistributable installation or TLS verification change is involved.

## Passed evidence and observed installed defects

The previous 02b6699 candidate used application source 4ca190e and signed installer SHA256 `01af39b6984aea6c09770a5a864cb40d4de99aa161d6f4ea4b138d3af263f719`. Signing and independent verification passed. Clean Windows per-user installation, all 23,083 payload and 18,330 seed-file inventory checks, ordinary-user first-run configuration, browser/service bootstrap, live source retrieval and caption acquisition passed.

That installed candidate failed two required outcomes: managed TextFlowKit activation could not load Torch because Microsoft C++ dependencies were absent, and desktop publication/download used inconsistent storage roots. It is superseded, not accepted. PE imports and ordinary-user loader probes confirmed missing MSVCP140/VCRUNTIME140_THREADS, with additional MSVCP140_ATOMIC_WAIT/VCOMP dependencies. A clearly labeled diagnostic added only those four valid Microsoft-signed DLLs beside bundled Python. Exact TextFlowKit 0.1.19 version and Torch 2.15.0+cpu/Whisper 20250625 imports then passed through the managed environment and bundled Python. No model download/inference or service restart occurred; this cause proof does not establish corrected-installer acceptance.

Previously passed unaffected runtime/CA/long-path and security/lifecycle evidence is retained. The first-run signed seed, supported module invocation, offline bootstrap with honest online-check status, update retention/rollback, long-path support and process-scoped certifi CA handling remain. No model weights are bundled.

## Ownership and remaining authorized work

The failed 02b guest was retired after ordinary-user recovery shutdown and verified preservation of desktop data/configuration/DPAPI and the legacy record-storage root. Evidence and private recovery data remain local; never publish them wholesale. Retirement receipt: `outputs/townrecord-windows-020/retirement-02b6699.json`; 205 retained evidence files, no owned jobs remaining. Its four-DLL diagnostic mutation is recorded explicitly. No runtime-agent guest jobs remain. Fresh guest `f85b3c96-963f-416f-b39e-dfb04d5c604d` launched at 08:17:33 UTC. Root/UI acceptance completed the recovery/lifecycle checks; uninstall is terminal 08:36:06 UTC with no active task instances. Clean ordinary-user installation, first-run, live acquisition, direct managed transcription and two-body browser publication/resident access passed; [current observations](evidence/windows-0.2.0/candidate-3a247d8/installed-workflow-observations.json) and [transcription receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-transcription-observation.json) record the exact scope. All 14 owned tasks are terminal, no product/browser process or owned job remained, the API is unreachable after uninstall, and only the owned guest was stopped. Live Sandbox inventory is empty at 08:38 UTC. Recovery data was privately preserved and independently hash-verified on the host; no private payload enters public evidence. [Cleanup receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-cleanup-observation.json). All acceptance was headless.

1. Exact corrected signed installer retrieval and independent verification are complete. Continue with this e08a candidate; the old 01af installer remains superseded.
2. First-run/tool setup, actual live acquisition, direct 120-second managed transcription and configured-profile review/publication/resident/export checks passed. Verified recovery continuation passed: 19 backup files rehashed, separate restored active profile, retained original data and publication/UI read/sign-out. The harness required a fresh document after fragment-only navigation; no backup/restore was repeated. Same-version reinstall preserved all saved data/config/credentials; post-reinstall full byte verification, startup target and service reopen passed. Uninstall removed application/registration/Open/Stop/startup links and retained the saved profile. [Lifecycle receipt](evidence/windows-0.2.0/candidate-3a247d8/installed-lifecycle-observation.json). Existing v0.1.0 scheduled-tick/deduplication evidence is retained; no new overnight or interactive sign-in test is claimed. Installed acceptance is complete; protected recovery data remains local.
3. Product delivery is verified: PR49 merged 73f282e; exact-source v0.2.0 and 29 assets published. Both final PR CI 43576964924 and post-merge CI 43577345645 passed. Pages 43577354304 passed; live anonymous site HTTP 200 shows the correct download. All 29 anonymous assets/checksums/signature/source proof passed 08:50:54Z. [Receipt](evidence/windows-0.2.0/candidate-3a247d8/PUBLIC-DELIVERY.md).
4. Coordinator final step: merge this Markdown-only public handoff and update existing Big Picture sources in place using the prepared absolute-link copies. Application/release acceptance is complete; no product build or rerun is needed. Temporary signing intake/helper branch cleanup is verified; private recovery evidence and existing v0.1.0 remain protected.

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
