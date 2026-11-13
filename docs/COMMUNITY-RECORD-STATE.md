# Current completion increment — November 13, 2026

Scott authorized: design first, remove desktop-shell architecture, convert interface
to TypeScript, record the mistake in memory, finish engineering items 1–6,
push/merge on pass; hold tag/publication. Scott determines required human work.
Goal active. One accountable agent, serial work; no delegated jobs.

Design written before conversion: COMMUNITY-INTERFACE-DESIGN.md. Explicit memory
note saved in the authorized extensions/ad_hoc/notes directory. Strict TypeScript
source in frontend/community.ts; compiler pinned 5.9.3; compiled asset stays in
Python wheel. Current architecture docs updated. Typecheck/build and 28 affected
community/serving/scheduler/recovery tests pass. UI busy buttons and back focus
corrected within the design. No new framework or runtime Node dependency.

Branch implement/typescript-community; PR43 draft. Installed source758abf6 includes
strict TypeScript and the observed refreshed-list keyboard-focus correction.
Passed browser checks: date/body filters, keyboard open, correction/history,
document and recording reassociation with original associations restored, and
HTML agenda download with correct extension. No public snapshot changed.
Next: verify installed focus fix, finish full local transcription and remaining
UI/full-file TownReporter handoff, documented service/schedule/recovery, required
CI, final handoff/project updates and merge. Reuse prior evidence below.
Owned API: loopback18290, exec43262, typescript_browser.py; cancel Ctrl+C.
Owned transcription: exec52730, full_transcription.py, job70/video4, started
2026-11-13T22:14:10Z. Expected saved full local transcript or explicit job error;
next read full-transcription-result.json. Other heavy jobs parked temporarily,
restored by script finally. Interrupt only owned process and verify children.
TownReporter full-browser handoff passed: Council lead1, duplicate same lead,
invalid file rejected, valid Planning retry lead2, both New and no model/scan.
Preview exec31245 stopped and tab closed; receipt full-browser-handoff.json.
Owned fresh installed CLI service: exec36507, loopback18292, deployment-profile,
started22:32UTC; initial bounded Sept23 source sync queued. Ctrl+C stops; next
verify acquisition, stop/restart, backup/restore and continued retrieval.
Design verified as eighth actual Big Picture source; screenshot preserved.
Installed keyboard focus regression passed after refresh on meeting9.
No VM/Sandbox launched by this increment. Older ownership statements below are
historical checkpoints, superseded by this current record.
Any human trial below is a suggestion until Scott defines it as required.

---

# Community record implementation state

Authorized November13,2026: implement the bounded TownRecord plan, reuse connectors,
fix the original three defects, finish operator/resident workflows and one unpublished
TownReporter handoff. Test/push/merge; hold tagging and publishing for Scott.

## Delivered / exact identity

TownRecord PR39, PR40 and PR41 merged. PR41 merge65b8b499cea7ebd337f411ff6acc0374db480d87;
required CI43477439541 passed. Packaged source538d044c8b87549e7f02ad59704fbdd9341f8611.
Wheel SHA256066b7cfd413f87d72c415058f2a7ddf4d272eddafe2267876cacddb00884d49f.
No signed MSI, tag, release or public deployment is claimed.

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
