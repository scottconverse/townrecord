[CmdletBinding()]
param([switch]$PrepareOnly,[string]$Payload)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$work=Join-Path $repo 'build/windows'
$pins=Get-Content (Join-Path $PSScriptRoot 'dependencies.json') -Raw|ConvertFrom-Json
$python=Join-Path $work "managed-python/cpython-$($pins.python.version)-windows-x86_64-none/python.exe"
$uv=Join-Path $work 'cache/uv/uv.exe'
$venv=Join-Path $work 'textflowkit-seed-venv'
$requirements=Join-Path $PSScriptRoot 'textflowkit-seed-requirements.txt'
$constraints=Join-Path $PSScriptRoot 'seed-build-constraints.txt'
$receiptPath=Join-Path $work 'seed-preparation-receipt.json'
$requirementsHash=(Get-FileHash $requirements).Hash.ToLowerInvariant()
$constraintsHash=(Get-FileHash $constraints).Hash.ToLowerInvariant()
$reuse=$false
if(Test-Path $receiptPath) {
    $old=Get-Content $receiptPath -Raw|ConvertFrom-Json
    $reuse=$old.phase -eq 'prepared' -and $old.requirements_sha256 -eq $requirementsHash -and $old.build_constraints_sha256 -eq $constraintsHash -and $old.python_version -eq $pins.python.version
}
function Checked([string]$File,[string[]]$Arguments){& $File @Arguments;if($LASTEXITCODE -ne 0){throw "$File exited $LASTEXITCODE"}}
if(!$reuse) {
    $memory=Get-CimInstance Win32_OperatingSystem
    if($memory.FreePhysicalMemory -lt 3GB/1KB -or (Get-PSDrive ([IO.Path]::GetPathRoot($work).TrimEnd('\').TrimEnd(':'))).Free -lt 8GB){throw 'Seed preparation requires 3 GB available RAM and 8 GB disk.'}
    if(Test-Path $venv){throw 'Existing unverified seed venv must be preserved and inspected before another preparation.'}
    $receipt=[ordered]@{schema=1;phase='preparing';owner_pid=$PID;started_utc=[DateTime]::UtcNow.ToString('o');requirements_sha256=$requirementsHash;build_constraints_sha256=$constraintsHash;python_version=$pins.python.version;next_action='Verify closure, sign native seed files with the base payload, derive final seed manifest';cancellation="Stop seed preparation PowerShell PID $PID after checking its identity"}
    $receipt|ConvertTo-Json|Set-Content $receiptPath
    try {
        Checked $uv @('venv',$venv,'--python',$python,'--no-config')
        Checked $uv @('pip','install','--python',(Join-Path $venv 'Scripts/python.exe'),'--no-config','--no-deps','--require-hashes','--build-constraints',$constraints,'--requirements',$requirements)
        Checked $python @('-I',(Join-Path $PSScriptRoot 'seed_payload.py'),'record','--venv',$venv)
        $receipt.phase='prepared';$receipt.completed_utc=[DateTime]::UtcNow.ToString('o')
    } catch {$receipt.phase='failed';$receipt.error=$_.Exception.Message;throw}
    finally {$receipt|ConvertTo-Json|Set-Content $receiptPath}
}
if(!$PrepareOnly) {
    if(!$Payload){throw 'Staging the seed requires the explicit isolated payload directory.'}
    Checked $python @('-I',(Join-Path $PSScriptRoot 'seed_payload.py'),'stage','--venv',$venv,'--payload',$Payload,'--python',$python)
}
