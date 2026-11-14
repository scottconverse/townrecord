[CmdletBinding()]
param(
    [switch]$PrepareOnly,
    [switch]$CompileOnly,
    [switch]$PrepareUninstaller,
    [string]$Compiler = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$work = Join-Path $repo 'build/windows'
$cache = Join-Path $work 'cache'
$payload = Join-Path $work 'payload'
$output = Join-Path $repo 'dist/windows'
$pins = Get-Content (Join-Path $PSScriptRoot 'dependencies.json') -Raw | ConvertFrom-Json
if ($CompileOnly -or $PrepareUninstaller) {
    if ($PrepareOnly) { throw 'PrepareOnly cannot be combined with compiler-only modes.' }
    & (Join-Path $PSScriptRoot 'repack.ps1') -Compiler $Compiler -PrepareUninstaller:$PrepareUninstaller
    return
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}
function Fetch-Verified($Pin, [string]$Destination) {
    if (!(Test-Path -LiteralPath $Destination)) {
        Invoke-WebRequest $Pin.url -OutFile $Destination
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Pin.sha256) {
        throw "Archive hash mismatch: $Destination. Remove this archive and investigate its origin."
    }
}
function Remove-BuildTree([string]$Target) {
    $resolved = [IO.Path]::GetFullPath($Target)
    if (!$resolved.StartsWith($work + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove outside isolated build directory: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}

New-Item -ItemType Directory $cache,$output -Force | Out-Null
$memory = Get-CimInstance Win32_OperatingSystem
if ($memory.FreePhysicalMemory -lt 2GB / 1KB) { throw 'At least 2 GB available memory is required for packaging.' }
if ((Get-PSDrive ([IO.Path]::GetPathRoot($work).TrimEnd('\').TrimEnd(':'))).Free -lt 4GB) {
    throw 'At least 4 GB free build disk space is required.'
}
$receipt = [ordered]@{
    schema = 1; started_utc = [DateTime]::UtcNow.ToString('o'); owner_pid = $PID
    operation = 'prepare/build Windows installer'; terminal_result = 'dist/windows installer and build-receipt.json'
    next_action = 'verify receipt, then sign and test exact installer'; cancellation = "Stop build PowerShell PID $PID after checking its identity"
    source_commit = (& git -C $repo rev-parse HEAD).Trim(); source_dirty = [bool](& git -C $repo status --porcelain)
    dependencies = $pins; phase = 'preparing'
}
$receiptPath = Join-Path $work 'build-receipt.json'
$receipt | ConvertTo-Json -Depth 8 | Set-Content $receiptPath
try {
    foreach ($name in @('uv','ffmpeg','python')) {
        $extension = if ($name -eq 'python') { '.tar.gz' } else { '.zip' }
        Fetch-Verified $pins.$name (Join-Path $cache ($name + $extension))
    }
    foreach ($name in @('ffmpeg-source','ffmpeg-build-source')) {
        $sourceArchive = Join-Path $cache "$name.tar.gz"
        Fetch-Verified $pins.$name $sourceArchive
        Copy-Item $sourceArchive (Join-Path $output "$name.tar.gz") -Force
    }
    foreach ($name in @('uv','ffmpeg')) {
        Remove-BuildTree (Join-Path $cache $name)
        Expand-Archive (Join-Path $cache "$name.zip") (Join-Path $cache $name)
    }
    $uv = Join-Path $cache 'uv/uv.exe'
    Invoke-Checked $uv @('--version')
    # Install the verified archive through uv's managed-Python installer, using a
    # local mirror. No global Python links or registry entries are created.
    $mirror = Join-Path $cache 'python-mirror'
    $mirrorRelease = Join-Path $mirror $pins.python.build
    New-Item -ItemType Directory $mirrorRelease -Force | Out-Null
    $archiveName = [Uri]::UnescapeDataString(([Uri]$pins.python.url).Segments[-1])
    Copy-Item (Join-Path $cache 'python.tar.gz') (Join-Path $mirrorRelease $archiveName) -Force
    $pythonStore = Join-Path $work 'managed-python'
    Invoke-Checked $uv @('python','install',$pins.python.version,'--reinstall','--install-dir',$pythonStore,'--no-bin','--no-registry','--mirror',([Uri]($mirror + '/')).AbsoluteUri)
    $managedPython = Join-Path $pythonStore "cpython-$($pins.python.version)-windows-x86_64-none"
    if (!(Test-Path (Join-Path $managedPython 'pythonw.exe'))) { throw 'Portable Python lacks pythonw.exe' }
    if ($PrepareOnly) {
        $receipt.phase = 'prepared'; $receipt.completed_utc = [DateTime]::UtcNow.ToString('o')
        return
    }
    if (!(Test-Path -LiteralPath $Compiler)) { throw 'Install pinned Inno Setup compiler documented in dependencies.json, or pass -Compiler.' }
    Remove-BuildTree $payload
    New-Item -ItemType Directory $payload,(Join-Path $payload 'tools'),(Join-Path $payload 'notices') -Force | Out-Null
    Copy-Item $managedPython (Join-Path $payload 'python') -Recurse
    # TownRecord uses a browser UI, never Tk. The optional Tcl/Tk desktop
    # toolkit's tcl86t.dll and tk86t.dll fail Authenticode signing with
    # ERROR_BAD_EXE_FORMAT in this upstream Python build. Omit that unused
    # component rather than modifying vendor binaries or machine policy.
    foreach ($component in @('python/tcl','python/Lib/tkinter','python/Lib/turtledemo','python/Lib/turtle.py','python/DLLs/_tkinter.pyd','python/DLLs/tcl86t.dll','python/DLLs/tk86t.dll')) {
        Remove-BuildTree (Join-Path $payload $component)
    }
    $python = Join-Path $payload 'python/python.exe'
    # Prevent ambient packages and index credentials from becoming build inputs.
    Invoke-Checked npm.cmd @('--prefix',$repo,'run','build:ui')
    $requirements = Join-Path $work 'requirements.txt'
    Invoke-Checked $uv @('export','--project',(Join-Path $repo 'core'),'--frozen','--no-dev','--no-emit-project','--no-editable','--quiet','--format','requirements-txt','--output-file',$requirements)
    Push-Location (Join-Path $repo 'core')
    try {
        # This is the disposable payload copy, never the managed source or a
        # global interpreter. uv's external-management marker is retained.
        Invoke-Checked $uv @('pip','install','--python',$python,'--break-system-packages','--no-config','--no-deps','--require-hashes','--only-binary',':all:','--requirements',$requirements)
        $wheelDir = Join-Path $work 'wheels'
        Remove-BuildTree $wheelDir
        Invoke-Checked $uv @('build','--wheel','--python',$python,'--build-constraint',(Join-Path $PSScriptRoot 'build-constraints.txt'),'--out-dir',$wheelDir)
        $wheel = @(Get-ChildItem $wheelDir -Filter '*.whl')
        if ($wheel.Count -ne 1) { throw 'Expected exactly one application wheel' }
        Invoke-Checked $uv @('pip','install','--python',$python,'--break-system-packages','--no-deps',$wheel[0].FullName)
        $receipt.wheel_sha256 = (Get-FileHash $wheel[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally { Pop-Location }
    $ffmpeg = @(Get-ChildItem (Join-Path $cache 'ffmpeg') -Directory)[0].FullName
    foreach ($name in @('ffmpeg.exe','ffprobe.exe')) { Copy-Item (Join-Path $ffmpeg "bin/$name") (Join-Path $payload 'tools') }
    Copy-Item (Join-Path $ffmpeg 'bin/*.dll') (Join-Path $payload 'tools')
    Copy-Item $uv (Join-Path $payload 'tools/uv.exe')
    Copy-Item (Join-Path $ffmpeg 'LICENSE.txt') (Join-Path $payload 'notices/FFmpeg-LGPL-3.0.txt')
    Copy-Item (Join-Path $PSScriptRoot 'FFmpeg-GPL-3.0.txt') (Join-Path $payload 'notices')
    Copy-Item (Join-Path $ffmpeg 'doc') (Join-Path $payload 'notices/ffmpeg-doc') -Recurse
    Copy-Item (Join-Path $repo 'LICENSE') (Join-Path $payload 'LICENSE.txt')
    Copy-Item (Join-Path $PSScriptRoot 'dependencies.json') (Join-Path $payload 'notices/dependencies.json')
    Copy-Item $requirements (Join-Path $payload 'notices/python-requirements.txt')
    Copy-Item (Join-Path $repo 'core/uv.lock') (Join-Path $payload 'notices/uv.lock')
    Copy-Item (Join-Path $PSScriptRoot 'THIRD-PARTY-NOTICES.txt') (Join-Path $payload 'notices/THIRD-PARTY-NOTICES.txt')
    Copy-Item (Join-Path $PSScriptRoot 'uv-LICENSE-*.txt') (Join-Path $payload 'notices')
    Copy-Item (Join-Path $PSScriptRoot 'build-constraints.txt') (Join-Path $payload 'notices')
    & (Join-Path $PSScriptRoot 'build-seed.ps1') -Payload $payload
    $receipt.seed_manifest_sha256=(Get-FileHash (Join-Path $payload 'runtime-seeds/textflowkit/0.1.19/manifest.json')).Hash.ToLowerInvariant()
    $receipt.seed_requirements_sha256=(Get-FileHash (Join-Path $PSScriptRoot 'textflowkit-seed-requirements.txt')).Hash.ToLowerInvariant()
    Copy-Item (Join-Path $PSScriptRoot 'textflowkit-seed-requirements.txt') (Join-Path $payload 'notices')
    Copy-Item (Join-Path $PSScriptRoot 'seed-build-constraints.txt') (Join-Path $payload 'notices')
    Invoke-Checked $python @('-I','-c',"import townrecord, townrecord.windows, quorumrelay, fastapi; from importlib.metadata import version; assert townrecord.__version__ == '0.2.0'; assert version('quorumrelay') == '0.1.0.dev11'; print('Bundled imports and versions verified')")
    Invoke-Checked (Join-Path $payload 'tools/ffmpeg.exe') @('-version')
    Invoke-Checked (Join-Path $payload 'tools/ffprobe.exe') @('-version')
    Invoke-Checked $python @('-I',(Join-Path $PSScriptRoot 'verify_payload.py'),$payload)
    $manifest = Get-ChildItem $payload -File -Recurse | Sort-Object FullName | ForEach-Object {
        # Windows PowerShell 5's path resolver rejects long Torch filenames.
        $stream = [IO.File]::OpenRead('\\?\' + $_.FullName)
        try {
            [ordered]@{ path = $_.FullName.Substring($payload.Length + 1).Replace('\','/'); size = $_.Length; sha256 = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() }
        } finally { $stream.Dispose() }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $payload 'payload-manifest.json')
    $receipt.payload_manifest_sha256 = (Get-FileHash (Join-Path $payload 'payload-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    $version = (& $python -I -c 'import townrecord; print(townrecord.__version__)').Trim()
    # ISCC file metadata can be 0.0.0.0. The script verifies the compiler's
    # actual ISPP VER value against this pin before compiling any files.
    $compilerVersion = $pins.compiler.version
    Invoke-Checked $Compiler @("/DPayloadDir=$payload","/DOutputDir=$output","/DAppVersion=$version","/DExpectedCompilerVersion=$compilerVersion","/DInternalSigningDir=$(Join-Path $work 'signed-uninstaller')",(Join-Path $repo 'installer/townrecord.iss'))
    $installer = Join-Path $output "TownRecord-$version-windows-x64-setup.exe"
    $receipt.phase = 'built_unsigned'; $receipt.installer = $installer
    $receipt.installer_sha256 = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $receipt.compiler_version = $compilerVersion
    $receipt.compiler_sha256 = (Get-FileHash $Compiler -Algorithm SHA256).Hash.ToLowerInvariant()
    $receipt.lock_sha256 = (Get-FileHash (Join-Path $repo 'core/uv.lock') -Algorithm SHA256).Hash.ToLowerInvariant()
    $receipt.completed_utc = [DateTime]::UtcNow.ToString('o')
} catch {
    $receipt.phase = 'failed'; $receipt.error = $_.Exception.Message
    throw
} finally {
    $receipt | ConvertTo-Json -Depth 8 | Set-Content $receiptPath
}
