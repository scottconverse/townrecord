# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Validate','Verify')][string]$Mode,
    [string]$Archive = 'intake/unsigned-native-payload.zip',
    [string]$ExpectedSha256,
    [string]$Directory = 'signing',
    [string]$Receipt = 'signing-receipt.json',
    [string]$RunId = $env:GITHUB_RUN_ID
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Safe-NativePath([string]$Path) {
    if ($Path -notmatch '^(payload/(python|tools|runtime-seeds/textflowkit/0\.1\.19)/[A-Za-z0-9_./+ -]+\.(exe|dll|pyd)|internal/uninst-[A-Za-z0-9_.-]+\.(exe|e32|e64))$' -or
        @($Path.Split('/') | Where-Object {$_ -in @('','..','.')}).Count) { throw "Invalid native signing path: $Path" }
}
function Assert-PE([string]$File) {
    $stream=[IO.File]::OpenRead($File)
    $reader=[IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { throw "Not an MZ executable: $File" }
        $stream.Position=0x3c
        $offset=$reader.ReadInt32()
        if ($offset -lt 64 -or $offset -gt $stream.Length-4) {throw "Invalid PE offset: $File"}
        $stream.Position=$offset
        if ($reader.ReadUInt32() -ne 0x4550) {throw "Not a PE image: $File"}
    } finally {$reader.Dispose();$stream.Dispose()}
}
if ($Mode -eq 'Validate') {
    if ($ExpectedSha256 -notmatch '^[a-f0-9]{64}$') {throw 'A lowercase SHA-256 is required.'}
    if ((Get-FileHash $Archive).Hash.ToLowerInvariant() -ne $ExpectedSha256) {throw 'Signing intake ZIP hash mismatch.'}
    if (Test-Path -LiteralPath $Directory) {throw 'Signing output directory must not already exist.'}
    $zip=[IO.Compression.ZipFile]::OpenRead((Resolve-Path $Archive).Path)
    try {
        $manifestEntries=@($zip.Entries | Where-Object FullName -eq 'manifest.json')
        if ($manifestEntries.Count -ne 1 -or $manifestEntries[0].Length -gt 2MB) {throw 'Exactly one bounded manifest is required.'}
        $reader=[IO.StreamReader]::new($manifestEntries[0].Open())
        try {$manifest=$reader.ReadToEnd() | ConvertFrom-Json} finally {$reader.Dispose()}
        if ($manifest.schema -ne 1 -or @($manifest.files).Count -eq 0 -or @($manifest.files).Count -gt 1000) {throw 'Invalid signing manifest.'}
        $allowed=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach($item in $manifest.files) {
            Safe-NativePath $item.path
            if ($item.sha256 -notmatch '^[a-f0-9]{64}$' -or $item.size -lt 64 -or $item.size -gt 512MB) {throw 'Invalid signing member metadata.'}
            if ($allowed.ContainsKey($item.path)) {throw 'Duplicate signing path.'}
            if ($item.kind -eq 'payload') {
                if ($item.path -ne ('payload/'+$item.target)) {throw 'Payload target mapping mismatch.'}
            } elseif ($item.kind -eq 'internal') {
                if ($item.target -notmatch '^uninst-[A-Za-z0-9_.-]+\.(e32|e64)$' -or
                    [IO.Path]::GetFileNameWithoutExtension($item.path) -ne [IO.Path]::GetFileNameWithoutExtension($item.target)) {throw 'Internal target mapping mismatch.'}
            } else {throw 'Unknown signing member kind.'}
            $allowed.Add($item.path,$item)
        }
        if ($zip.Entries.Count -ne $allowed.Count+1) {throw 'ZIP contains unexpected entries.'}
        $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $total=0L
        foreach($entry in $zip.Entries) {
            if (!$seen.Add($entry.FullName)) {throw 'ZIP contains duplicate entries.'}
            if ($entry.FullName -eq 'manifest.json') {continue}
            Safe-NativePath $entry.FullName
            if (!$allowed.ContainsKey($entry.FullName)) {throw 'ZIP member not allowlisted by manifest.'}
            if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {throw 'ZIP symlinks are forbidden.'}
            $expected=$allowed[$entry.FullName]
            if ($entry.Length -ne $expected.size) {throw 'ZIP member size mismatch.'}
            $total+=$entry.Length
            if ($total -gt 2GB) {throw 'Signing intake exceeds the size bound.'}
        }
        New-Item -ItemType Directory $Directory | Out-Null
        foreach($entry in $zip.Entries) {
            if ($entry.FullName -eq 'manifest.json') {continue}
            $target=Join-Path $Directory $entry.FullName
            New-Item -ItemType Directory (Split-Path $target) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$target,$false)
            if ((Get-FileHash $target).Hash.ToLowerInvariant() -ne $allowed[$entry.FullName].sha256) {throw 'Unsigned native member hash mismatch.'}
            Assert-PE $target
            if ((Get-AuthenticodeSignature $target).Status -ne 'NotSigned') {throw 'Intake member already has a signature; preserve vendor signatures instead.'}
        }
        $manifest | ConvertTo-Json -Depth 8 | Set-Content 'validated-manifest.json'
        [ordered]@{archive_sha256=$ExpectedSha256;member_count=$allowed.Count;status='verified_unsigned_intake'} | ConvertTo-Json | Set-Content 'intake-verification.json'
    } finally {$zip.Dispose()}
} else {
    $manifest=Get-Content 'validated-manifest.json' -Raw | ConvertFrom-Json
    $files=@()
    foreach($item in $manifest.files) {
        Safe-NativePath $item.path
        $file=Join-Path $Directory $item.path
        Assert-PE $file
        $sig=Get-AuthenticodeSignature $file
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '(^|, )CN=Scott Converse(,|$)' -or !$sig.TimeStamperCertificate) {
            throw "Native member lacks the required valid Scott Converse timestamped signature: $($item.path)"
        }
        $files+=[ordered]@{path=$item.path;kind=$item.kind;target=$item.target;unsigned_sha256=$item.sha256;signed_sha256=(Get-FileHash $file).Hash.ToLowerInvariant();signed_size=(Get-Item $file).Length;status='Valid';signer_subject=$sig.SignerCertificate.Subject;signer_thumbprint=$sig.SignerCertificate.Thumbprint;timestamp_subject=$sig.TimeStamperCertificate.Subject;timestamp_thumbprint=$sig.TimeStamperCertificate.Thumbprint}
    }
    if (@(Get-ChildItem $Directory -File -Recurse).Count -ne $files.Count) {throw 'Unexpected native output files.'}
    $intake=Get-Content 'intake-verification.json' -Raw | ConvertFrom-Json
    $result=[ordered]@{schema=1;status='verified_signed_native_payload';unsigned_archive_sha256=$intake.archive_sha256;workflow_run=$RunId;payload_source_commit=$manifest.payload_source_commit;packaging_commit=$manifest.packaging_commit;original_payload_manifest_sha256=$manifest.original_payload_manifest_sha256;files=$files}
    $result | ConvertTo-Json -Depth 8 | Set-Content $Receipt
    Copy-Item 'validated-manifest.json' (Join-Path $Directory 'manifest.json')
    Copy-Item $Receipt (Join-Path $Directory 'signing-receipt.json')
    [IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path $Directory).Path,(Join-Path (Get-Location).Path 'signed-native-payload.zip'))
}
