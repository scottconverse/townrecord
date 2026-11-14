[CmdletBinding()]
param(
    [switch]$PrepareUninstaller,
    [string]$Compiler = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$work = Join-Path $repo 'build/windows'
$payload = Join-Path $work 'payload'
$output = Join-Path $repo 'dist/windows'
$signingDir = Join-Path $work 'signed-uninstaller'
$baseReceiptPath = Join-Path $work 'build-receipt.json'
$base = Get-Content $baseReceiptPath -Raw | ConvertFrom-Json
$pins = Get-Content (Join-Path $PSScriptRoot 'dependencies.json') -Raw | ConvertFrom-Json
$manifestPath = Join-Path $payload 'payload-manifest.json'
$receiptPath = Join-Path $work 'repack-receipt.json'
$receipt = [ordered]@{
    schema=1; phase='verifying_reused_payload'; started_utc=[DateTime]::UtcNow.ToString('o'); owner_pid=$PID
    source_commit=(& git -C $repo rev-parse HEAD).Trim(); source_dirty=[bool](& git -C $repo status --porcelain)
    payload_source_commit=$base.source_commit; payload_manifest_sha256=$base.payload_manifest_sha256
    original_build_receipt_sha256=(Get-FileHash $baseReceiptPath).Hash.ToLowerInvariant()
    wheel_sha256=$base.wheel_sha256; cancellation="Stop repack PowerShell PID $PID after checking its identity"
    next_action='Sign stable internal PE files, repack, then sign outer installer and verify installed behavior'
}
try {
    if ($receipt.source_dirty) { throw 'Compiler-only packaging requires a committed clean candidate.' }
    $changedRuntime = & git -C $repo diff --name-only $base.source_commit HEAD -- core frontend ':(exclude)core/tests/**'
    if ($changedRuntime) { throw 'Runtime/frontend inputs changed; compiler-only reuse is not valid.' }
    if ((Get-FileHash $manifestPath).Hash.ToLowerInvariant() -ne $base.payload_manifest_sha256) {
        throw 'Payload manifest changed since the recorded full build.'
    }
    $python=Join-Path $work "managed-python/cpython-$($pins.python.version)-windows-x86_64-none/python.exe"
    & $python -I (Join-Path $PSScriptRoot 'native_signing.py') verify --allow-overlay
    if ($LASTEXITCODE -ne 0) {throw 'Original payload or explicitly signed overlay verification failed.'}
    $overlayPath=Join-Path $payload 'payload-signing-overlay.json'
    if (Test-Path $overlayPath) {
        $overlay=Get-Content $overlayPath -Raw | ConvertFrom-Json
        foreach($item in $overlay.files | Where-Object kind -eq 'payload') {
            $signature=Get-AuthenticodeSignature (Join-Path $payload $item.target)
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )CN=Scott Converse(,|$)' -or !$signature.TimeStamperCertificate) {
                throw "Signed overlay lacks a valid timestamped Scott Converse signature: $($item.target)"
            }
        }
        $receipt.payload_signing_overlay_sha256=(Get-FileHash $overlayPath).Hash.ToLowerInvariant()
        $receipt.payload_signing_workflow_run=$overlay.workflow_run
        $receipt.derived_seed_manifest=$overlay.derived_seed_manifest
        if($overlay.PSObject.Properties.Name -contains 'reused_signing'){$receipt.native_signing_reuse=$overlay.reused_signing}
    } elseif (!$PrepareUninstaller) {throw 'Final compiler-only repack requires the verified signed native payload overlay.'}
    $wheel = @(Get-ChildItem (Join-Path $work 'wheels') -Filter '*.whl')
    if ($wheel.Count -ne 1 -or (Get-FileHash $wheel[0].FullName).Hash.ToLowerInvariant() -ne $base.wheel_sha256) {
        throw 'Application wheel changed since the recorded full build.'
    }
    New-Item -ItemType Directory $signingDir -Force | Out-Null
    $version = [regex]::Match((Get-Content (Join-Path $repo 'core/pyproject.toml') -Raw), '(?m)^version = "([^"]+)"').Groups[1].Value
    if (!$version) { throw 'Application version is missing.' }
    $receipt.phase='compiling'; $receipt.compiler_version=$pins.compiler.version
    $receipt.compiler_sha256=(Get-FileHash $Compiler).Hash.ToLowerInvariant()
    $receipt | ConvertTo-Json -Depth 7 | Set-Content $receiptPath
    # With no SignTool callback, Inno emits a stable unsigned PE and exits with
    # a signature-needed error. After external signing it reuses that file.
    & (Join-Path $PSScriptRoot 'compile-installer.ps1') -Compiler $Compiler -Payload $payload -Output $output -Version $version -CompilerVersion $pins.compiler.version -SigningDir $signingDir
    $compilerExit = $LASTEXITCODE
    $internal = @(Get-ChildItem $signingDir -File | Where-Object Extension -in @('.e32','.e64','.exe'))
    $receipt.internal_files = @($internal | ForEach-Object {
        $signature = Get-AuthenticodeSignature $_.FullName
        [ordered]@{path=$_.FullName;sha256=(Get-FileHash $_.FullName).Hash.ToLowerInvariant();signature=$signature.Status.ToString();signer=if($signature.SignerCertificate){$signature.SignerCertificate.Subject}else{''}}
    })
    $receipt.compiler_exit_code=$compilerExit
    if ($PrepareUninstaller -and $compilerExit -ne 0 -and $internal.Count -gt 0 -and
        @($receipt.internal_files | Where-Object signature -eq 'NotSigned').Count -gt 0) {
        $receipt.phase='awaiting_internal_signature'
        Write-Output 'Stable internal PE files prepared; sign them externally, preserving their exact filenames, then run -CompileOnly.'
    } elseif ($compilerExit -ne 0) {
        throw "Inno compilation exited $compilerExit; inspect the log and internal signing files."
    } else {
        if (@($receipt.internal_files | Where-Object { $_.signature -ne 'Valid' -or $_.signer -notmatch 'Scott Converse' }).Count -gt 0) {
            throw 'Embedded internal files must carry a valid Scott Converse signature.'
        }
        $installer = Join-Path $output "TownRecord-$version-windows-x64-setup.exe"
        $receipt.phase='repacked_unsigned_outer'; $receipt.installer=$installer
        $receipt.installer_sha256=(Get-FileHash $installer).Hash.ToLowerInvariant()
    }
    $receipt.completed_utc=[DateTime]::UtcNow.ToString('o')
} catch {
    $receipt.phase='failed'; $receipt.error=$_.Exception.Message
    throw
} finally {
    $receipt | ConvertTo-Json -Depth 7 | Set-Content $receiptPath
}
