# TownRecord community candidate acceptance

November 13, 2026. Engineering acceptance of a bounded Windows local-service
candidate, not a release announcement or completed human community trial.

## Exact identity

- TownRecord source: `538d044c8b87549e7f02ad59704fbdd9341f8611`.
- Implementation merged through PRs [39](https://github.com/scottconverse/townrecord/pull/39),
  [40](https://github.com/scottconverse/townrecord/pull/40) and
  [41](https://github.com/scottconverse/townrecord/pull/41).
- Tested wheel: `townrecord-0.1.0-py3-none-any.whl`, SHA-256
  `066b7cfd413f87d72c415058f2a7ddf4d272eddafe2267876cacddb00884d49f`.
  All 147 packaged source/web/migration files matched the candidate checkout.
- Installed into a separate Windows Python 3.13.5 environment with locked dependencies;
  readback used the installed package, not a PYTHONPATH overlay.
- TownReporter handoff: [PR388](https://github.com/scottconverse/TownReporter/pull/388),
  candidate `a66b331c254d34227a064f2f1648b1421efd3dbb`. Check the state record for merge status.
- This is a Python service plus browser UI. No TownRecord MSI, signed desktop
  installer or public deployment is claimed. Tagging/publication is held.

## Observed results and limits

| Outcome | Observed evidence | Limit |
|---|---|---|
| Original identity/revision defects | Fixes and regression tests merged in PR39; connector/workflow repairs in PR40 | Does not establish every provider |
| Two-body source configuration/acquisition | Longmont PrimeGov September discovery yielded ten meetings for Council and Planning; other bodies explicitly outside coverage | Public-source evaluation, not an agreed city partnership |
| Repeat acquisition | Repeated discovery preserved all ten meeting identities | Not a months-long scheduling observation |
| Documents and text | Council September 8: three documents, 559 extracted PDF pages. Planning September 16: four documents, 86 pages | A provider attachment named Minutes is not proof that it contains approved minutes for that same meeting |
| Failed download recovery | The previously failed job4 was retried through the installed operator API; job69 completed; repeat retry request returned the same job | API plus real worker evidence, not a fresh browser-click recording of this retry |
| Full source captions | Council: 5,787 segments; Planning: 2,415 segments. Both capture jobs completed | Automatic captions remain provisional, not certified minutes |
| Resident transcript navigation | Installed browser opened the Council deep link, rendered provisional origin and timestamp links through the final segment | No full human line-by-line transcript review |
| Local transcription | Both Whistle and Whisper transcribed the same two real two-minute meeting clips on CPU; see comparison | Not a full-meeting local-fallback run; four minutes is not general accuracy proof |
| Runtime setup control | Installed operator browser queued TextFlowKit setup/check; actual runtime job57 completed | Local-transcribe control has API authorization/idempotency tests; full browser-button-to-full-meeting fallback was not observed |
| Review/publication/privacy | Synthetic browser lifecycle exercised review, publication, search, correction isolation, withdrawal, sign-out and narrow viewport; API tests cover unauthorized/private access | Agent-run tests, not an independent resident usability trial |
| TownReporter browser handoff | Actual agenda export produced one New lead; duplicate returned same lead; original meeting/document/video citations visible | Isolated Windows preview, not production; no article or model run |
| Complete TownReporter handoff | Complete Council and Planning exports passed the actual receive function and isolated database, retained final transcript text and duplicate identity | This larger payload check used the receive function, not a second browser walk |
| Backup/restore | CLI backup and clean-directory restore verified hashes, integrity and stable instance identity; restored public meeting/documents returned200, private operator route401 | Snapshot was taken before later full caption/packet acquisition; external credentials/models are deliberately not backup contents |
| Candidate CI | TownRecord PR41 required existing CI passed, including Windows | No new OS packaging work; unaffected evidence reused |

Two full-record handoff defects were found and fixed before merge: the complete
Council JSON exceeded the old 2 MB limit (now 8 MiB, byte-counted), and PDF-derived
null characters could not be stored as JSONB. The exact JSON snapshot is now stored
as text so escaped characters survive. Regression tests verify complete text,
escaped-character roundtrip, oversized Unicode refusal and editorial retry safety.

## Remaining acceptance

The planned second operator deployment/recovery and resident/reporter trial have
not occurred. No participating organization or human approval is implied. A new
live meeting appearing after setup has not yet been observed. Agent fixtures and
historical replay cover workflows but cannot stand in for those observations.
The wider specification's desktop shell and question-answering features remain
outside this bounded delivery. All-provider live acceptance is not claimed.

## Reproduce and maintain

Follow [Windows operations](WINDOWS-COMMUNITY-OPERATIONS.md) using the exact source
above, a separate profile and publicly accessible sources. It documents Python,
uv, FFmpeg, managed tools, operator tokens, service startup, daily retrieval,
publication boundaries, handoff and backup/restore. Do not put tokens in Git.

Local raw receipts, public-source exports, scripts, screenshots and the wheel are
preserved in `outputs/townrecord-community-acceptance` in the implementation
workspace. That directory also includes private lab tokens/database: do not upload
it wholesale. `package-provenance.json`, `two-body-final-readback.json`,
`repeat-discovery.json`, `installed-retry-receipt.json`, `restore-readback.json`,
`full-record-handoff-receipt.json` and the two engine comparison JSON files identify
the specific observations. Screenshots are `packaged-resident-transcript.jpg` and
`townreporter-handoff.jpg`. The comparison document is also a verified source in
The Big Picture Google Drive Project.

No production instance was modified. Test browser tabs, local preview/API services
and workers were stopped. Do not start scheduled processing against the lab merely
to inspect its evidence. See the concise state record for any remaining hosted CI.
