# Community record implementation

Authorized November 13, 2026: implement the nine-item Big Picture bounded plan,
reuse the connector candidates, test/push/merge incrementally. **No tagging or
publishing without Scott's approval.** One community, two bodies, Windows
operation, operator and resident workflows, one TownReporter evidence handoff.

Baseline: main `851b7bd`; audit in The Big Picture Project, document
`TOWNRECORD-BASELINE-AND-COMPLETION-ESTIMATE.md`.

## Current work

- First: meeting rescheduling, identity collisions and same-ID document revisions.
- Then: reconcile QuorumRelay candidate `c5fe052`, operator/publication workflow,
  resident pages, transcript navigation, TownReporter transfer, deployment and
  backup/restore, exact-candidate acceptance and documentation.
- Passed baseline: 1,543 tests and two archived-real-data tests; live PrimeGov
  agenda acquisition; authenticated loopback API. These are not UI acceptance.
- Worktree: `work/townrecord-community`, branch `implement/community-record`.
- No owned background jobs. No candidate installer yet. Nothing released.

Completion means every bounded-plan outcome, including correction/withdrawal,
unauthorized access, revised/missing material, retry/deduplication and restoration,
passes against the candidate. Missing workflows stay incomplete.

## First repair validation (in progress)

Regression tests for all three defects, document reversion, failure preservation,
and populated-database migration passed (5 tests). Migration rollback/enforcement
restoration passed separately. Lint passes. Full local suite running as session
57661 (started 2026-11-13 09:37 America/Denver); receipts in
`outputs/townrecord-baseline-2026-11-13/first-change-tests.*`; cancel using Ctrl-C
on that owned session. Initial free disk 506 GB, available RAM about 32 GB.
Next: review full-suite and hosted CI results, merge repairs, then reuse connector
candidate. No release authorization.
