[CmdletBinding()]
param(
    [string]$Compiler, [string]$Payload, [string]$Output,
    [string]$Version, [string]$CompilerVersion, [string]$SigningDir
)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$expected=[IO.Path]::GetFullPath((Join-Path $repo 'build/windows/payload'))
if([IO.Path]::GetFullPath($Payload) -ne $expected){throw 'Compiler requires the isolated payload.'}
# ISCC rejects extended paths. A short owned junction preserves every source
# byte while keeping the longest locked Torch filename within its path limit.
$alias=Join-Path $env:TEMP ('TownRecordPayload-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$receiptPath=Join-Path $repo 'build/windows/compiler-path-receipt.json'
$receipt=[ordered]@{owner_pid=$PID;started_utc=[DateTime]::UtcNow.ToString('o');alias=$alias;target=$expected;phase='creating_junction';cancellation='Verify this junction target, then remove only the junction after compiler stops'}
try {
    New-Item -ItemType Junction -Path $alias -Target $expected | Out-Null
    $link=Get-Item -LiteralPath $alias
    if($link.LinkType -ne 'Junction' -or @($link.Target)[0] -ne $expected){throw 'Unexpected compiler junction target.'}
    $receipt.phase='compiling';$receipt|ConvertTo-Json|Set-Content $receiptPath
    & $Compiler "/DPayloadDir=$alias" "/DOutputDir=$Output" "/DAppVersion=$Version" "/DExpectedCompilerVersion=$CompilerVersion" "/DInternalSigningDir=$SigningDir" (Join-Path $repo 'installer/townrecord.iss')
    $compilerExit=$LASTEXITCODE
} finally {
    if(Test-Path -LiteralPath $alias){
        $link=Get-Item -LiteralPath $alias
        if($link.LinkType -ne 'Junction' -or @($link.Target)[0] -ne $expected){throw 'Refusing to remove an unexpected compiler alias.'}
        [IO.Directory]::Delete($alias) # Removes the junction only; never recurse.
    }
    $receipt.phase='junction_removed';$receipt.completed_utc=[DateTime]::UtcNow.ToString('o');$receipt|ConvertTo-Json|Set-Content $receiptPath
}
$global:LASTEXITCODE=$compilerExit
