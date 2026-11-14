# Verified public delivery — TownRecord 0.2.0

This Markdown receipt curates terminal public-download/source/signature and live-site receipts. It excludes local paths, process ownership and credentials. It records observed delivery, not a new build or installed test.

## Release and checks

- [PR49](https://github.com/scottconverse/townrecord/pull/49) merged at `73f282e8e45679b1d539adf8194706782e01f98c` on 2026-11-14T08:45:45Z after [final PR CI 43576964924](https://github.com/scottconverse/townrecord/actions/runs/43576964924) passed.
- [v0.2.0 community-preview pre-release](https://github.com/scottconverse/townrecord/releases/tag/v0.2.0) published at 2026-11-14T08:45:53Z. Tag targets exact application/packaging source `3a247d8865f1692911f4bed15cae370410ec6d70`.
- [Post-merge CI 43577345645](https://github.com/scottconverse/townrecord/actions/runs/43577345645) completed successfully on all existing platform jobs at 08:50:58Z.
- All 29 release assets were downloaded using anonymous HTTP GET, without Authorization, cookies or credentials. Every response was HTTP 200; total 243,662,812 bytes. All 28 checksum-covered assets matched SHA256SUMS; the checksum file's own identity independently matched the prepared manifest. Terminal result: PASSED at 2026-11-14T08:50:54Z.
- Downloaded installer SHA256 `e08a4f4447abb611a331cd7146df56493eb92f76bc14c9a660cb80fb9c3067a8` (221,865,160 bytes), with Valid Scott Converse Authenticode and Microsoft timestamp. Downloaded wheel SHA256 `88857f9b821e67a273bb5197356a2125a04fbfad3961124fb1da9e0ba8da0bec`.
- Downloaded source ZIP SHA256 `ea7a3c070abe30326e1ebfd95f24cceaee11a07fb97637ce79b1cff0991bb82e`, exact 3a commit comment and 365 files verified against its complete Git tree: 27 raw blob matches and 338 Git archive CRLF conversion matches. Reproducing the archive gave the identical ZIP hash.
- Existing published v0.1.0 and its nine assets remain unchanged. Original native signing/reuse receipt identities and the immutable application/source artifacts remain intact.

## Live website

[Pages 43577354304](https://github.com/scottconverse/townrecord/actions/runs/43577354304) passed from main `73f282e8e45679b1d539adf8194706782e01f98c`. Anonymous GET of [the public website](https://scottconverse.github.io/townrecord/) returned HTTP 200 at 2026-11-14T08:47:05Z and confirmed the exact Windows setup download URL with no stale preparation/pending wording. HTML SHA256: `a9cf7ec2fc0214aa2d9192e3fd8be8d20f9c28a67e6db8151972ac502e7bff8c`.

## Downloaded asset identities

| Public asset | Bytes | SHA256 |
|---|---:|---|
| [ACCEPTANCE.md](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/ACCEPTANCE.md) | 2,528 | `5657361c4ac561b2ef1a8dd108fdebad7d68389104abe82fe91f63a7736766ae` |
| [build-provenance.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/build-provenance.json) | 10,145 | `db162300b51a55038d6f19f7310e559af3a49ed168060556cf4ac317f1dd721f` |
| [candidate-provenance.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/candidate-provenance.json) | 1,225 | `b4db020b936ff9a2bb0033565f02ea7fe94d2597a208fb113a61125e4b9ac9df` |
| [candidate-status.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/candidate-status.json) | 1,635 | `c738ea6722f973e25dac1647066d2244a01cee6d02c918532e3df13258934907` |
| [ffmpeg-build-source.tar.gz](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/ffmpeg-build-source.tar.gz) | 119,484 | `1b57e95ae01b03da36cbfd231d7482c85df2ce87ad4a0e491ca656fa7e3706be` |
| [ffmpeg-source.tar.gz](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/ffmpeg-source.tar.gz) | 17,330,939 | `479396204090c378cc2d181fef5355a1da64c5903e29a7fda687960101e70df9` |
| [independent-signature-verification.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/independent-signature-verification.json) | 541 | `4e7251be1d2f19550980738db02e6dfb94f7bf932277dabf783cbd6bca35284b` |
| [INSTALL-Windows.md](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/INSTALL-Windows.md) | 13,290 | `34a23908f293f806a906925c40735b5d2a4f6fbb554408053f573878326b5efa` |
| [installed-cleanup-observation.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-cleanup-observation.json) | 909 | `8b3177702bb77e48f4a85b5277f5d9a45efcd71b7e7dd6200b9fe1d577455642` |
| [installed-lifecycle-observation.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-lifecycle-observation.json) | 2,483 | `9e280a121604187548b8818c520939185b1f7f051c69e97dcfc6719b5941f6ce` |
| [installed-payload-verification.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-payload-verification.json) | 861 | `3191028147b3096db14c016075b5f8109f0bd6d8703ec264d2a295c7e903d277` |
| [installed-recovery-observation.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-recovery-observation.json) | 2,332 | `04549543d064f5d80cde156378f4caba2ddb2780cc082310c7704bc58f441ce2` |
| [installed-transcription-observation.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-transcription-observation.json) | 1,902 | `81ecf6ceb801faf8b5569b0afea8b3e3eac909635d7e366d50c913e8929f1cdb` |
| [installed-workflow-observations.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/installed-workflow-observations.json) | 8,187 | `471f332af7ffeb09b16637c0f21b3a227136ec8c47e1e1f17acd3b9341589783` |
| [Microsoft-VC-Runtime-LICENSE.docx](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/Microsoft-VC-Runtime-LICENSE.docx) | 39,644 | `f1e3d56ceb2ad68aae0711b910375009e651ac5530fa0760f0dea6e81e54fae1` |
| [Microsoft-VC-Runtime-LICENSE.txt](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/Microsoft-VC-Runtime-LICENSE.txt) | 6,857 | `8ee08259581e18cda6ee8a2e000d4e08e895646eef9bda95c9f658e44d65b167` |
| [native-signing-original-receipt.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/native-signing-original-receipt.json) | 110,007 | `c2160604894dc35075b72164e1a71edf75ad3d880567b95b167638dce25b6c82` |
| [native-signing-reuse.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/native-signing-reuse.json) | 1,281 | `77f57d5958475c835cef8c12381e6d723396a8749982ce4de7d863ea69fca754` |
| [outer-signing-receipt.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/outer-signing-receipt.json) | 668 | `d8f19f55139e858401b5c786f983544e911e2a022692806e0f6ece32f648d204` |
| [README.md](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/README.md) | 1,473 | `f6b188e08ee25158b63791d99188f127e380638f779e612004033c34bb1f205a` |
| [required-ci.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/required-ci.json) | 1,251 | `264e7795c36f0018131664fe3d4aef25c8324de5ed19baeaf10e5b8ab7926087` |
| [SHA256SUMS.txt](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/SHA256SUMS.txt) | 2,676 | `a0e9169f70e8d14565abae583123ca7833f20a4d236bdc736c26309453679146` |
| [TECHNICAL-MANUAL.md](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TECHNICAL-MANUAL.md) | 15,255 | `a252e73b9dd74273962971fe6bd9d9adb73f14574e94f733a43520b0b8051753` |
| [townrecord-0.2.0-py3-none-any.whl](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/townrecord-0.2.0-py3-none-any.whl) | 501,631 | `88857f9b821e67a273bb5197356a2125a04fbfad3961124fb1da9e0ba8da0bec` |
| [TownRecord-0.2.0-source-3a247d8.zip](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TownRecord-0.2.0-source-3a247d8.zip) | 3,602,580 | `ea7a3c070abe30326e1ebfd95f24cceaee11a07fb97637ce79b1cff0991bb82e` |
| [TownRecord-0.2.0-windows-x64-setup.exe](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/TownRecord-0.2.0-windows-x64-setup.exe) | 221,865,160 | `e08a4f4447abb611a331cd7146df56493eb92f76bc14c9a660cb80fb9c3067a8` |
| [USER-MANUAL.md](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/USER-MANUAL.md) | 12,542 | `1d9481a5439d817341bb50a5fb2aa7ace9eff0cd5ee41a13e6cfb036ff7a651a` |
| [vc-runtime-official-source.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/vc-runtime-official-source.json) | 583 | `0b124d70da0c368fb60c08c522d4c3b0485b6b5d36f902a86772428d30e1cf17` |
| [vc-runtime-provenance.json](https://github.com/scottconverse/townrecord/releases/download/v0.2.0/vc-runtime-provenance.json) | 4,743 | `da95d8b756861530d759bd0cd4a51f034c91651a5188e27aa58fb60a48956c30` |

## Receipt provenance and cleanup

| Original terminal receipt | SHA256 |
|---|---|
| `public-download-verification.json` | `0d739861155079f09773105944af337dfa38931d1b70c6a10b8da4bb75cc7b27` |
| `summary.json` | `f54044d1491134f74f64bf1a1af10f1739ffc299cac8dd51b4e198e7af613975` |
| `source-zip-verification.json` | `c2950aa85cc21be9f01361e1ac3baa1639f75366e9996947aceb60e7fae61cd7` |
| `public-site-verification.json` | `8b9f9b7dfc56cb76bbe8ba003595ffe39a14090cd45f1f5916c3d411212eb233` |

Owned acceptance tasks/processes and guest cleanup are recorded in [installed cleanup](installed-cleanup-observation.json). The coordinator removed only the verified temporary signing-intake draft and exact helper branch after public delivery verification; follow-up paginated release/branch queries found no remaining match. Public signed assets/evidence and private recovery data remain intact. No public download-verification worker remains running.

JSON files in this directory preserve their original capture-time checkpoint fields. Current installed completion and public delivery are recorded by this receipt and the maintained [acceptance](../../../COMMUNITY-ACCEPTANCE.md) and [handoff](../../../COMMUNITY-COMPLETION-HANDOFF.md); obsolete pending fields in immutable checkpoint JSON are not current status.
