[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare','Apply','Reuse')][string]$Mode,
    [string]$Archive,
    [string]$ExpectedSha256,
    [string]$PreviousIntake
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$work=Join-Path $repo 'build/windows'
$payload=Join-Path $work 'payload'
$pins=Get-Content (Join-Path $PSScriptRoot 'dependencies.json') -Raw | ConvertFrom-Json
$python=Join-Path $work "managed-python/cpython-$($pins.python.version)-windows-x86_64-none/python.exe"
$helper=Join-Path $PSScriptRoot 'native_signing.py'
function Invoke-Python([string[]]$Arguments) {
    & $python -I $helper @Arguments
    if ($LASTEXITCODE -ne 0) {throw 'Native signing verification failed.'}
}
if ($Mode -eq 'Prepare') {
    Invoke-Python @('verify')
    $inventory=@(Get-ChildItem $payload -File -Recurse | Where-Object Extension -in @('.exe','.dll','.pyd') | ForEach-Object {
        $sig=Get-AuthenticodeSignature $_.FullName
        if ($sig.Status -notin @('Valid','NotSigned')) {throw "Unacceptable existing signature: $($_.FullName)"}
        [ordered]@{path=$_.FullName.Substring($payload.Length+1).Replace('\','/');sha256=(Get-FileHash $_.FullName).Hash.ToLowerInvariant();status=$sig.Status.ToString();signer=if($sig.SignerCertificate){$sig.SignerCertificate.Subject}else{''}}
    })
    $inventory | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $work 'native-pe-inventory.json')
    Invoke-Python @('prepare')
} else {
    if (!$Archive -or $ExpectedSha256 -notmatch '^[a-f0-9]{64}$') {throw 'Apply requires the returned ZIP and its independently captured lowercase SHA-256.'}
    $arguments=@('validate-return','--archive',$Archive,'--expected-sha256',$ExpectedSha256)
    if($Mode -eq 'Reuse'){
        if(!$PreviousIntake){throw 'Reuse requires the preserved original intake manifest.'}
        $arguments+=@('--previous-intake',$PreviousIntake)
    }
    Invoke-Python $arguments
    $stage=Join-Path $work 'signed-native-return'
    $receipt=Get-Content (Join-Path $stage 'signing-receipt.json') -Raw | ConvertFrom-Json
    foreach($item in $receipt.files) {
        $file=Join-Path $stage $item.path
        $sig=Get-AuthenticodeSignature $file
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '(^|, )CN=Scott Converse(,|$)' -or !$sig.TimeStamperCertificate) {
            throw "Return file does not carry the required valid timestamped Scott Converse signature: $($item.path)"
        }
        if ((Get-FileHash $file).Hash.ToLowerInvariant() -ne $item.signed_sha256) {throw 'Signed file changed during verification.'}
    }
    Invoke-Python @('apply')
    Copy-Item (Join-Path $stage 'signing-receipt.json') (Join-Path $work 'payload-signing-receipt.json')
}
