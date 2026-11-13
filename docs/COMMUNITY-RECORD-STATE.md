# Community record implementation state

Authorized November 13, 2026: complete the nine-item Big Picture bounded plan,
reuse connector work, test/push/merge incrementally. Hold tagging and publishing
for Scott's approval. Windows operation; one community, two bodies; one
TownReporter unpublished evidence handoff. No unrelated products or platforms.

Baseline: main `851b7bd`, audit document in The Big Picture Project.
First repair PR #39 merged as `99cb5efcbd8332c57c473a45ad020c2ec78c5dc7`.
Repository CI run 43465538628 passed all existing jobs, including Windows.

Current worktree: `work/townrecord-community`, branch `implement/community-workflows`.
Existing connector candidate `c5fe052` reused via merge `37322c9`.
Added immutable connector revisions, operator/publication API, resident pages,
authorized metadata/association correction, and public snapshot isolation.
Focused regression run: 15 passed (publication/privacy, corrected association
through refresh, native and shared revision/identity, populated upgrade).
Ruff passed before final overview status-field addition. JavaScript syntax passed.
Earlier test runs 57661 and 61478 were cancelled; no full local-suite pass claimed.
A fixture collection error was fixed by moving the upgrade test beside its fixtures.

Remaining: browser workflow and real two-body source acceptance; transcription
runtime/retry; TownReporter transfer with duplicate/editorial protection;
documented Windows deployment/scheduling/backup-restore; exact candidate acceptance
and full operator/resident documentation. Do not call the plan complete yet.

No owned local jobs, services, VMs or sandboxes running. No candidate installer,
tag, release or public deployment created. Next: push current increment for CI,
then test browser workflow in an isolated loopback instance.
