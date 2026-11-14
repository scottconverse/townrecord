# Windows installer design (0.2.0)

TownRecord keeps its TypeScript browser interface and Python service. The installer
is a per-user Windows x64 Inno Setup package; it needs no administrator rights,
Python installation, terminal commands or Node installation on the target machine.

The application directory is `%LOCALAPPDATA%\Programs\TownRecord`. It contains a
portable CPython 3.13 runtime with the unused optional Tcl/Tk desktop toolkit
excluded, the app wheel and every locked production
Python dependency (including the vendored QuorumRelay wheel), uv, ffmpeg and
ffprobe. Only an explicit payload staging tree is packaged: never the checkout,
local virtual environment, caches, credentials or a user's data.

Start menu shortcuts invoke `pythonw.exe -I -m townrecord.windows open` and `stop`.
The final installer page offers to open TownRecord. Start at Windows sign-in is
an unchecked, explicit installer option invoking `start` without opening a browser.
All shortcuts use the installed interpreter; they do not depend on PATH.

The runtime owns `%LOCALAPPDATA%\TownRecord` for configuration, database, captures,
logs, tool environments and instance identity. Installation upgrades and uninstall
stop only that profile's identified TownRecord instance through the runtime's stop
command. A failed stop aborts replacement/removal. Uninstall removes installed
program files and shortcuts but preserves the profile. No recursive user-data
removal is included. A stable application ID allows upgrades in place.

The build script uses an isolated output tree, `uv.lock`, a pinned portable Python
request and pinned downloaded tool archives with SHA-256 verification. It builds
TypeScript, exports frozen production requirements, builds the app wheel and
installs dependencies into the portable interpreter's site-packages. Third-party
licenses, archive provenance, dependency records and payload hashes accompany the
installer. The compiler is build-time only. Signing, clean-Windows acceptance,
release publication and post-publication verification are separate recorded steps.

FFmpeg uses the upstream LGPL shared build, with replaceable DLLs and its LGPL
and GPL license texts. Exact FFmpeg source and matching build scripts are staged
as release assets alongside the installer. Python and uv retain their upstream
license notices. The Inno compiler is pinned to 6.7.3, available through winget.

Acceptance includes a Windows machine without developer tools; launch, initial
setup, save/change a source and restart; bundle/runtime/tool identity; opt-in
startup; graceful stop; same-version repair/reinstall and uninstall preserving profile; and reinstall
using retained settings. The root release task owns those installed checks and
signing; a successful build alone is not their evidence.

The installer enables `SignedUninstaller=yes` and retains Inno's stable signing
inputs outside the shipped payload. The first compiler pass emits an unsigned
internal PE and stops. After authorized external signing, the second pass reuses
that signed PE to sign the temporary setup copies and uninstaller. The final
outer setup is signed separately. This follows Inno's documented two-pass flow;
signing only the outer executable leaves temporary copies unsigned and failed
the observed clean-Windows Application Control policy. No policy is disabled.
Compiler-only passes verify and reuse the recorded application wheel and payload,
preserving their original source provenance while recording the packaging commit
and internal signature identities separately.

The clean Windows policy also rejected unsigned shipped entry executables.
The initial base payload contained 88 unsigned native PE files and two valid Microsoft
runtime DLLs. The unsigned files and internal Inno PE are signed in one hash-gated
batch through the existing Azure account on an isolated helper branch; the two
vendor signatures are preserved. The original payload manifest remains unchanged
and a separate signed overlay records before/after hashes, signer and timestamp
identities. Local return verification accepts only Authenticode metadata changes
to the original PE bytes. It never changes application source, dependency versions,
the wheel or machine security policy. Installed acceptance still has to verify
the final signed installer and actual native runtime under the unchanged policy.

The first batch signer accepted 87 of 89 inputs but rejected upstream
`tcl86t.dll` and `tk86t.dll` with `0x800700C1` (bad executable format).
TownRecord and its vendored QuorumRelay do not use Tkinter or a Tcl/Tk desktop
interface. Packaging therefore omits the optional Tcl/Tk files, Tkinter extension
and associated standard-library GUI demos. It preserves the CPython interpreter,
standard service libraries, `venv` and `ensurepip`; no binary headers or security
policy are modified. The resulting native payload contains 85 unsigned PE files
and two vendor-signed Microsoft DLLs, plus one separately prepared internal Inno
PE in the signing intake. The browser interface and application wheel are unchanged.

This is the first native installer. Reinstalling 0.2.0 verifies repair and profile
retention, not a version upgrade from an earlier native installer.
