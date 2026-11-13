# Community record implementation state

Authorized November 13, 2026: finish the nine-item Big Picture bounded plan,
reuse connectors, test/push/merge incrementally. Hold tagging and publishing.
Windows operation; Longmont Council and Planning; one unpublished TownReporter
handoff. Production and unrelated products remain outside this work.

## Delivered
- TownRecord PR39 merged 99cb5ef: three original identity/revision defects.
- TownRecord PR40 merged b12aefebfeedeca2deff71593f682c961f8bce27:
  connector reuse, operator/resident workflow and guarded PrimeGov repairs.
  Required CI run 43471427931 passed all existing jobs.
- Actual September discovery mapped ten meetings across two configured bodies.
  Guarded agenda HTML and PDF retrieval passed; 20 agenda items parsed.
  Original unavailable-page and missing-download-origin failures retained.
- Synthetic browser lifecycle passed review/publish/search/correction isolation/
  withdrawal/sign-out/mobile width. Twenty concurrent requests cleaned up correctly.

## Current candidate and evidence
TownRecord worktree work/townrecord-community, implement/community-delivery.
Uncommitted delivery increment: runtime setup/transcription controls, explicit
Whisper engine/dependencies and version normalization, portable backup/restore,
instance identity for handoff, resident deep links, Windows operator guide.
Scott requested testing Whistle and Whisper and choosing the better result.
Both completed identical two-minute Council and Planning clips on CPU.
Whistle 7.2–7.3 seconds / 83–85 MiB; Whisper small 28.9–30.4 seconds / 1884–1896 MiB.
Whisper retained for better observed terminology; not a gold-reference accuracy
benchmark. Details and limits: TRANSCRIPTION-COMPARISON.md. Forty-eight focused
runtime/STT tests passed, four operator API tests passed, source/test lint passed.
Full local suite: 1565 passed, 48 skipped, three stale migration-list assertions failed. Those assertions now include migration22; affected migration/recovery rerun: 23 passed. New operator test passed separately. JavaScript syntax and Ruff format/check passed.

TownReporter worktree work/townreporter-handoff, implement/townrecord-handoff.
PR388 open, latest e04ef3f2. First hosted run failed the daily-scan DOM fixture's
unstubbed importer dependency; reproduced and fixed. Targeted test passed and fix
pushed. Await required hosted results; do not merge failed CI.
Actual Windows Chrome handoff passed: real reviewed meeting export -> Lead 1 New,
repeat -> same Lead 1; Sources panel shows meeting, PrimeGov, YouTube citations.
No draft/article published. Screenshot/export retained in acceptance output.
Browser tab closed and loopback preview stopped; port18310 has no listener.

Actual CLI backup and clean-directory restore of the live lab passed hash and
integrity checks (four files). Restored public meeting and both documents returned 200; instance identity preserved; private operator route still requires authentication.

## Owned jobs and next actions
- Local pytest and selected caption worker finished. Real video2 capture produced 5787 provisional automatic-caption segments; no errors. Other queued jobs restored to their original run_after values.
- Hosted TownReporter PR388 CI latest push; inspect checks before merging.
No VM/Sandbox, TownReporter preview or TownRecord HTTP service running.

## Still required
Finish real transcription/caption readback and timestamp navigation; rerun source
coverage classification and repeat acquisition; validate new runtime UI controls;
restore readback; exact packaged candidate and Windows deployment acceptance;
correct affected maintained docs, commit/push delivery increment and required CI;
merge eligible PRs; finalize acceptance/handoff and add documents to actual Big
Picture project (folder membership alone is insufficient). An actual second human
operator/resident trial has not occurred. Never represent agent tests as that trial.
No tag, release or public deployment authorized or performed.
