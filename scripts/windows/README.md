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

The installer uses Inno's official cached signed-uninstaller flow. The first
compilation emits a stable `build/windows/signed-uninstaller/uninst-*.e32` file
and stops because that file needs signing. Sign that exact PE through the
authorized signing process, then retain its original filename in that directory.
If the signing intake requires `.exe`, a temporary renamed copy may be signed
and the signed bytes returned to the exact `.e32` name. Inno verifies that its
contents match the expected internal executable before reusing its signature.
This embeds signatures into the uninstaller and temporary setup copies; signing
only the outer setup afterwards does not do that.

For an already verified payload, use `build.ps1 -PrepareUninstaller` to produce
the internal signing input without reinstalling dependencies or rebuilding the
application. After signing, use `build.ps1 -CompileOnly` to repack. These modes
require a clean committed candidate, unchanged application inputs under `core`
and `frontend` (test-only changes are excluded),
and verification of every payload file and the retained wheel against the
original build receipt plus the explicitly verified signing overlay described
below. They produce `repack-receipt.json`, retaining
the original payload's source commit, hashes and build receipt identity as well
as the packaging candidate commit, compiler and internal signing identities.
The final outer installer still requires signing and independent verification.

Clean Windows Application Control also rejected the five unsigned bundled entry
executables. The initial base inventory had 90 native `.exe`/`.dll`/`.pyd` files:
88 unsigned files and two valid Microsoft runtime DLLs. The signing service
rejected only the optional Tcl/Tk DLLs (`tcl86t.dll`, `tk86t.dll`) with
`0x800700C1`. TownRecord and QuorumRelay have no Tkinter GUI usage, so the build
omits the unused optional Tcl/Tk component, its `_tkinter.pyd` extension and GUI
library/demos. The browser interface, interpreter, service libraries, `venv` and
`ensurepip` are retained. That historical base intake contained 82 unsigned files
and two valid Microsoft runtime DLLs, before the signed transcription seed was
added. The complete seed-inclusive batch signed 125 files: 124 payload files plus
one internal installer PE, with 22 existing vendor signatures preserved. The
corrected app-local CRT payload adds nine valid Microsoft-signed files while
preserving the two matching original Python runtime DLLs (31 vendor-signed files
in total). These are separate counts; preserve their original receipt identities
and Microsoft signatures. After preparing the internal Inno PE, run
`native-signing.ps1 -Mode Prepare`. This re-verifies the original payload and
creates `dist/windows/TownRecord-0.2.0-native-unsigned.zip`, containing only the
current unsigned native allowlist, the internal PE, and its hash manifest.
The internal `.e32` is staged as `.exe` without changing its PE bytes; its original
target filename is retained in the manifest.

The reusable `sign-native-payload.yml` and `signing-batch.ps1` are copied to the
isolated `townrecord-signing-020-20261113` helper branch in `scottconverse/civiccast`,
replacing that branch's existing `sign-native-installer.yml` only. The root task
uploads the intake ZIP and dispatches the existing workflow against that helper
branch with the exact ZIP SHA-256. The helper verifies the ZIP hash, rejects paths
or members outside its manifest, verifies every unsigned PE, signs recursively
through the existing Azure account, then verifies every Scott Converse signature
and timestamp. It returns `signed-native-payload.zip` and per-file before/after
hashes. It never publishes or changes a production branch.

Run `native-signing.ps1 -Mode Apply -Archive <returned-zip> -ExpectedSha256 <hash>`
after independently capturing the returned archive's SHA-256. It verifies the
complete return allowlist, original hashes, signed hashes, every actual signature
and timestamp, and that PE content changed only in Authenticode's certificate,
checksum and security-directory fields. Application Python, TypeScript, packages
and the wheel remain unchanged. The original `payload-manifest.json` is retained;
`payload-signing-overlay.json` records the permitted native-byte changes and their
workflow provenance. Repack verifies both manifests and signature-only changes
before using the signed payload and internal setup executable.

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

The installer also carries a locked TextFlowKit 0.1.19 seed, including the
Whisper Python dependency closure and no model weights. `build-seed.ps1`
prepares it from `textflowkit-seed-requirements.txt` (hash-locked) with pinned
source-build dependencies in `seed-build-constraints.txt`. It stages only
`Lib/site-packages` and the generic Python venv interpreter launcher. The app
copies verified files into its existing per-user managed runtime and generates
that runtime's local `pyvenv.cfg`; generated console launchers are not shipped.

Unsigned native seed files join the existing native signing batch. Applying
that batch preserves the original seed manifest in notices and derives the
final seed manifest by substituting only receipt-verified signed PE hashes.
The original full-payload manifest stays immutable; the signing overlay and
repack receipt explicitly record the derived JSON hash. Upstream wheel RECORD
files remain unchanged as provenance for the original wheel bytes; the final
seed manifest covers the actual signed bytes. No arbitrary non-native package
or metadata rewrites are allowed during this step.

The locked Torch tree has long source filenames. Manifest hashing uses extended
Windows file streams, and the Inno compiler reads the unchanged payload through
a short, owned temporary junction. The compiler wrapper records that alias and
its target and removes only the junction after compilation, including failures.
The installed default per-user path is shorter than the build workspace path;
clean-installed acceptance verifies the complete payload manifest after extract.

`build.ps1 -PayloadOnly` stages and verifies the normal pinned payload and app
wheel without compiling an outer installer. The app-local Microsoft x64 CRT and
OpenMP set is pinned to 14.44.35211.0, matching the two original Python runtime
DLLs byte for byte. `stage-vc-runtime.ps1` extracts only the release directories
from the hash-verified official VSIX, verifies every Microsoft signature/version
and preserves matching existing files. The payload verifier scans actual PE
imports for unresolved plain MSVC runtime dependencies.

When an app-only fix leaves the unsigned native inputs unchanged, preserve the
previous intake and signed archive. Restore the known original unsigned Inno
internal file, then run `native-signing.ps1 -Mode Prepare`. `-Mode Reuse` accepts
`-Archive`, `-ExpectedSha256` and `-PreviousIntake` (the old manifest path). It
requires every native path, target, kind, size and SHA-256 to match exactly,
rechecks signature-only executable content and all actual signatures, retains
the original cloud receipt/manifest, and explicitly binds those same signatures
to the new payload manifest. Then run `build.ps1 -CompileOnly`. Changed native
inputs fail reuse and require a new signing batch.
