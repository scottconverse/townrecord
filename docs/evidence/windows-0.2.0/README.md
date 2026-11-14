# Windows 0.2.0 build and signing evidence

These receipts establish the prepared candidate's provenance, signing and required
CI. **Installed acceptance and public delivery are pending.** They are not proof
of a completed clean Windows workflow.

- [Build provenance](build-provenance.json) preserves application source 4ca190e,
  packaging source 02b6699, wheel 4cd30b55, compiler identity, unsigned installer,
  native overlay and derived seed hashes. Machine workspace paths and transient
  process information were omitted; original receipt hashes remain recorded.
- [Native signing receipt](native-signing-receipt.json) is the unmodified original:
  125 newly signed files, comprising 124 payload files and one internal installer
  executable. The intake separately records 22 upstream vendor signatures retained.
  All 125 returned entries have status Valid. This is not an installed-file count.
- [Seed derivation](signed-seed-derivation.json) records the deterministic substitution
  of verified signed PE hashes into the original seed metadata. The derived seed
  manifest SHA256 is 0bf631371bf3247f35c1c80e28b341821899fcc0b667648270ede12bc404d41f.
- [Outer signing receipt](outer-signing-receipt.json) and
  [independent verification](independent-signature-verification.json) identify the
  signed installer: 221,381,512 bytes; SHA256
  01af39b6984aea6c09770a5a864cb40d4de99aa161d6f4ea4b138d3af263f719.
  Scott Converse Authenticode and Microsoft timestamp verification are recorded.
- [Required CI](required-ci.json) was queried from Actions run 43569493649 attempt 2:
  Windows, Ubuntu and macOS jobs are all successful. Attempt 2 reran Windows and
  retains the existing successful POSIX jobs; it did not change the CI matrix.
- [Receipt index](receipt-index.json) records original-byte provenance and the
  curation boundary. Full payload manifests/binaries can accompany release assets;
  they are not duplicated in Git here.

Workflow runs: [native signing](https://github.com/scottconverse/civiccast/actions/runs/43569539324),
[outer signing](https://github.com/scottconverse/civiccast/actions/runs/43570543904),
[required CI](https://github.com/scottconverse/townrecord/actions/runs/43569493649/attempts/2).

No private profiles, credentials, databases, token material or raw full logs are
included. Prior v0.1.0 acquisition, full transcription and TownReporter evidence
remains historical in the [acceptance record](../../COMMUNITY-ACCEPTANCE.md).
The new candidate needs its own clean installed/runtime/recovery observations.
