# Community record implementation state

Authorized November13,2026: implement the bounded TownRecord plan, reuse connectors,
fix the original three defects, finish operator/resident workflows and one unpublished
TownReporter handoff. Test/push/merge; hold tagging and publishing for Scott.

## Delivered / exact identity

TownRecord PR39, PR40 and PR41 merged. PR41 merge65b8b499cea7ebd337f411ff6acc0374db480d87;
required CI43477439541 passed. Packaged source538d044c8b87549e7f02ad59704fbdd9341f8611.
Wheel SHA256066b7cfd413f87d72c415058f2a7ddf4d272eddafe2267876cacddb00884d49f.
No signed MSI/Tauri shell, tag, release or public deployment is claimed.

Passed: live two-body discovery/repeat identity; actual documents/text; full source
captions (5787 Council/2415 Planning segments); installed resident timestamp
navigation; synthetic operator lifecycle/privacy; actual setup/check control;
installed failed-download retry; CLI backup/restore/readback; isolated TownReporter
browser handoff/idempotency; full Council/Planning receive-function handoff.
See COMMUNITY-ACCEPTANCE.md for evidence tiers and limits.

Scott requested both transcription engines tested. Same two real120-second clips:
Whistle7.2–7.3s /83–85MiB; Whisper small28.9–30.4s /1884–1896MiB.
Whisper retained for better observed terminology, not a gold-reference accuracy
claim. TRANSCRIPTION-COMPARISON.md is added to the actual Big Picture Project.

## Current authorized work

TownReporter PR388 head a66b331c254d34227a064f2f1648b1421efd3dbb pushed.
Complete exports exposed two defects: old2MB limit and JSONB rejection of PDF null
characters. Fixed with8MiB byte bound and exact JSON-text snapshot storage.
Focused tests, typecheck/lint and actual full-record receive/retry passed.
Required CI43479944986 is running; prior head CI passed. Merge only after current
required checks pass. Next: verify CI, merge expected head, update this status and
add final acceptance/handoff to the actual Big Picture Project (folder alone is
insufficient). Maintained documentation correction is on docs/community-acceptance.

## Owned work / remaining acceptance

No local service, benchmark, VM/Sandbox or worker remains running. Lab evidence,
original source artifacts, comparison outputs and candidate wheel are preserved.
Only owned asynchronous work is the hosted PR388 CI above; completion means required
checks passing/failing, next action inspect/merge or diagnose, cancellation via gh
run cancel43479944986 if Scott stops work. Do not cancel unrelated GitHub runs.

Second-human operator deployment/recovery and resident/reporter acceptance have not
occurred; no new post-setup meeting has been observed. Full-meeting local Whisper
fallback has not been run end-to-end through the browser button; real clip engines,
full source-caption path and endpoint tests are distinct evidence. Never relabel
these limits as completed acceptance. Tagging and publishing remain unapproved.
