# Active Windows installer delivery — November 13, 2026

Scott authorized implementation, signing, clean Windows acceptance, documentation,
push, merge, tag and publication of TownRecord 0.2.0. Preserve v0.1.0. Keep the
TypeScript browser/Python service stack; no Tauri. Normal operators must not need
Python, commands or copied tokens. Public hosting remains a separate deployment.

Branch: feat/windows-installer-020; PR49. Runtime source: 2acd5cde0ea1d72a1e9ac24f96db1040c320ab43.
Required CI43561395186 passed. First signed outer installer SHA256:
4a52333a0760a0cccc92ab1803d2c9c504f6c3e8c5684d8e9ed776a4a37ba0c1.
It is NOT accepted: clean Windows Application Control blocked unsigned internal
Inno setup and bundled native programs. Failed evidence is preserved under
outputs/townrecord-windows-020; no release/tag has been made for 0.2.0.

Packaging commit d83997b passed required CI43563463235. Native signing run43563556453 signed 87 files but rejected optional Tcl/Tk DLLs tcl86t.dll and tk86t.dll with SignTool 0x800700C1. Logs and input are preserved. The browser application and QuorumRelay do not use Tk. Packaging agent is excluding that unused toolkit through the existing build path and producing a fresh payload manifest; Windows policy stays unchanged.

Current work: packaging agent prepares two-pass signed Inno internals and signs
88 unsigned payload PE files, preserving two valid Microsoft DLL signatures.
Reuse the recorded wheel and unchanged Python/TypeScript payload; record every
native signature overlay. Root audits and dispatches signing. UI agent prepared
guest-only headless setup/review/recovery scripts; execution awaits installation.
Runtime helper is prepared for installed status and the bounded real-audio test.

Owned guest: 3bca7846-b403-4ac2-916e-b33763117c21, 8GB, no app installed yet.
Guest remains running headlessly; its two owned visible remote-session clients
were closed. Cancel after evidence preservation with wsb stop --id <owned ID>.
Owned draft signing intake: civiccast/townrecord-020-signing-2acd5cde.
No production, QuorumRelay or unrelated desktop work is authorized.

Global rule saved: remain headless while Scott uses the machine or occupancy is
unknown. No visible browser/app/Sandbox/VM windows or interactive computer control
without specific prior approval. Continue independent authorized headless work.

Remaining: complete native signatures and corrected outer installer; install and
verify first-run/operator/resident/runtime/recovery/reinstall/uninstall behavior;
update docs and evidence, required CI, merge/tag/release/Pages, verify public bytes
and pages, update existing Big Picture project sources, remove owned test resources
and temporary signing intake. Reuse unaffected v0.1.0 domain evidence with attribution.
Next action: commit completed packaging correction, prepare internal PE and dispatch
hash-gated Azure native signing. No Windows policy bypass or weakened checks.
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
