# Windows packaging

Build-time requirements are Windows x64, Node/npm, uv, and Inno Setup 6.7.3.
The end user needs only the resulting installer. Install the pinned compiler
with `winget install --id JRSoftware.InnoSetup --exact --version 6.7.3 --source winget`.
The compiler's normal per-user location is used by default; `-Compiler` overrides it.

`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/build.ps1 -PrepareOnly`
downloads and verifies pinned tools and prepares an isolated full CPython runtime.
The full build uses the same command without `-PrepareOnly`. Run it from the
candidate checkout after tests and commit, so provenance identifies the source.
The script reports the operation's owner PID and terminal result in
`build/windows/build-receipt.json`; it overwrites only its isolated payload tree.
Archive hash failures require investigation, not accepting a new hash blindly.

Outputs are `dist/windows/TownRecord-0.2.0-windows-x64-setup.exe`, exact FFmpeg
source/build archives, and the build receipt in `build/windows`. The installer is
unsigned at this stage. The receipt records source commit, dirty status, pins,
application wheel hash, lock hash, payload manifest hash and unsigned installer
hash. The payload's manifest records each shipped file. Preserve this receipt
and separately record the signed file hash and independent signature checks.
Do not run the packaging script again for documentation-only changes.

The build copies only a clean portable Python installation, locked packages,
explicit tool binaries/DLLs and notices. No development virtual environment,
credentials, sample data, acceptance audio or user profile enters the payload.
Python dependency hashes come from the frozen `core/uv.lock`; wheel build
dependencies are pinned in `build-constraints.txt`. The vendored QuorumRelay
wheel is verified against the frozen export's hash.

`python scripts/windows/test_packaging.py` runs the bounded packaging checks.
The actual build runs `verify_payload.py` and verifies bundled imports/tool
versions before invoking Inno. Installer acceptance follows separately.

`sandbox-acceptance.ps1` is a phased helper for a clean Windows Sandbox mapped
input/evidence directory. It does not create or launch a Sandbox. Run `Install`,
then `Verify` to retain the running app for observed GUI acceptance. Other
explicit phases are `Stop`, `Recovery`, `Reinstall`, and `Uninstall`; none runs
automatically after `Verify`. All installed Python invocations use `-I`.
`Install` requires a valid Authenticode signature. Default mappings are
`C:\TownRecordInput` and `C:\TownRecordEvidence`; parameters can override them.
Pass `-Startup` consistently to Install/Reinstall and Verify when testing the
opt-in Windows startup shortcut. The default installation leaves it unchecked.

`Recovery` stops the app and uses its installed recovery code to create a new
backup and restore it to an evidence directory, verifying SQLite integrity and
the complete manifest file set. It does not switch the active user profile.
`Reinstall` tests same-version repair and config retention. `Uninstall` checks
program removal and retained configuration. GUI setup, settings readback,
resident workflow and real tool/model operation remain observed acceptance,
not claims made by these script results.
