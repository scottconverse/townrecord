# Active Windows installer delivery — November 13, 2026

Scott authorized implementation, signing, clean Windows acceptance, documentation,
push, merge, tag and publication. New version 0.2.0; preserve released v0.1.0.
Use existing TypeScript/Python stack, no Tauri. Normal operators need no Python,
uv, terminal commands or copied bearer token. Keep public hosting a separate
operator deployment decision; this task installs the local operator application.

Delegation explicitly authorized: windows_runtime owns Python launcher/config/API
and tests; windows_ui owns TypeScript first-run and maintained manuals/site;
windows_packaging owns bundled runtime/Inno installer/version/build scripts.
Coordinator owns architecture, integration audit, signing, clean Windows evidence,
required CI, delivery and Big Picture updates. Branch feat/windows-installer-020.

Passed preflight: repository clean at a06a162; ~400 GiB disk headroom; Codex elevated
helper CheckAdmin job4b03d49620314ec99b7fc3cc205188d3 returned is_admin=true.
wsb list reports no guests. WindowsSandboxServer alone is infrastructure, not a
running guest. No Sandbox started for this task yet. No signed candidate exists.

Remaining: implementation, affected checks and required CI, one integrated build,
Azure signing and independent verification, exact signed installer clean Windows
GUI/setup/runtime/recovery/uninstall acceptance, docs, merge/release/Pages,
public download verification, actual Big Picture source updates, owned cleanup.
Next: receive contracts/implementation, audit security and lifecycle, build once
integrated. Reuse unchanged v0.1.0 domain/transcription evidence with attribution.

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
