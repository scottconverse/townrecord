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

## Delivery and ownership

TownReporter PR388 merged at 755827c0f251bd8b3507da8295e11830e62a733d.
Accepted head a66b331c254d34227a064f2f1648b1421efd3dbb passed required CI43479944986.
Complete exports exposed two defects: the old 2 MB limit and JSONB rejection of
PDF null characters. Both are fixed; complete packet and duplicate tests passed.
The documentation follow-up is TownRecord PR42. The exact tested wheel is unchanged.

The acceptance record and transcription comparison are verified sources in the
actual Big Picture Project, not only its supporting folder. No local service,
benchmark, worker or owned VM/Sandbox remains running. Lab evidence and candidate
wheel are preserved. No owned asynchronous job remains required for this handoff;
GitHub may run its normal post-merge CI automatically. No release was published.

## Remaining acceptance

Second-human operator deployment/recovery and resident/reporter acceptance have not
occurred; no new post-setup meeting has been observed. Full-meeting local Whisper
fallback has not been run end-to-end through the browser button; real clip engines,
full source-caption path and endpoint tests are distinct evidence. Never relabel
these limits as completed acceptance. Tagging and publishing remain unapproved.
