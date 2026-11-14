param([Parameter(Mandatory)][string]$Payload)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$work=Join-Path $repo 'build/windows'
$target=[IO.Path]::GetFullPath($Payload)
if(!$target.StartsWith($work+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'CRT staging requires an isolated build directory.'}
$pin=(Get-Content (Join-Path $PSScriptRoot 'dependencies.json') -Raw|ConvertFrom-Json).'vc-runtime'
$archive=Join-Path $work 'cache/vc-runtime.vsix'
if((Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant() -ne $pin.sha256){throw 'CRT source archive hash mismatch'}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip=[IO.Compression.ZipFile]::OpenRead($archive)
$files=@()
try {
 foreach($item in $pin.files){
  $entry=$zip.GetEntry($item.entry)
  if(!$entry -or $item.entry -notmatch '^Contents/VC/Redist/MSVC/14\.44\.35112/x64/Microsoft\.VC143\.(CRT|OpenMP)/[^/]+\.dll$' -or [IO.Path]::GetFileName($item.entry) -ne $item.name){throw 'Unexpected CRT release archive member'}
  $path=Join-Path $target ('python/'+$item.name)
  if(Test-Path -LiteralPath $path){
   if((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $item.sha256){throw 'Existing Python CRT differs from pinned coherent version'}
  }else{[IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$path,$false)}
  if((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $item.sha256){throw 'Staged CRT hash mismatch'}
  $signature=Get-AuthenticodeSignature -LiteralPath $path
  if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation' -or !$signature.TimeStamperCertificate -or (Get-Item -LiteralPath $path).VersionInfo.FileVersion -ne $pin.version){throw 'CRT requires original valid timestamped Microsoft signature and pinned version'}
  $files += [ordered]@{name=$item.name;sha256=$item.sha256;version=$pin.version;publisher=$signature.SignerCertificate.Subject}
 }
}finally{$zip.Dispose()}
New-Item -ItemType Directory (Join-Path $target 'notices') -Force|Out-Null
Copy-Item (Join-Path $PSScriptRoot 'Microsoft-VC-Runtime-LICENSE.txt') (Join-Path $target 'notices')
[ordered]@{package=$pin.package;version=$pin.version;url=$pin.url;archive_sha256=$pin.sha256;files=$files}|ConvertTo-Json -Depth 6|Set-Content (Join-Path $target 'notices/vc-runtime-provenance.json')
