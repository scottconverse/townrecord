[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install','Verify','Stop','Recovery','Reinstall','Uninstall')][string]$Phase,
    [string]$Installer = 'C:\TownRecordInput\TownRecord-0.2.0-windows-x64-setup.exe',
    [string]$Evidence = 'C:\TownRecordEvidence',
    [switch]$Startup
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$app = Join-Path $env:LOCALAPPDATA 'Programs\TownRecord'
$profile = Join-Path $env:LOCALAPPDATA 'TownRecord'
$python = Join-Path $app 'python\python.exe'
$pythonw = Join-Path $app 'python\pythonw.exe'
New-Item -ItemType Directory $Evidence -Force | Out-Null
$result = [ordered]@{ phase=$Phase; started_utc=[DateTime]::UtcNow.ToString('o'); status='running'; profile=$profile }
function Invoke-Checked([string]$Executable,[string[]]$Arguments) {
    $text = & $Executable @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed: $text" }
    return $text.Trim()
}
function Install-Candidate {
    if (!(Test-Path -LiteralPath $Installer)) { throw 'Mapped installer is missing' }
    $signature = Get-AuthenticodeSignature -LiteralPath $Installer
    if ($signature.Status -ne 'Valid') { throw "Installer signature is not valid: $($signature.Status)" }
    $script:result.installer_sha256 = (Get-FileHash $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $script:result.signer = $signature.SignerCertificate.Subject
    $task = if ($Startup) { '/TASKS=startup' } else { '/TASKS=' }
    $job = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',$task,"/LOG=`"$Evidence\$Phase-inno.log`"") -PassThru -Wait -WindowStyle Hidden
    if ($job.ExitCode -ne 0) { throw "Installer exited $($job.ExitCode)" }
}
function Stop-Installed {
    $null = Invoke-Checked $python @('-I','-m','townrecord.windows','stop')
}
try {
    switch ($Phase) {
        'Install' {
            if (Test-Path -LiteralPath $app) { throw 'Install phase requires a fresh app directory; use Reinstall for an existing installation' }
            $result.preexisting_commands = @(Get-Command python,python3,uv,ffmpeg -ErrorAction SilentlyContinue | Select-Object Name,Source)
            Install-Candidate
        }
        'Reinstall' {
            Stop-Installed
            $config = Join-Path $profile 'config.json'
            $before = (Get-FileHash $config -Algorithm SHA256).Hash
            Install-Candidate
            if ((Get-FileHash $config -Algorithm SHA256).Hash -ne $before) { throw 'Reinstall changed the saved profile configuration' }
            $result.configuration_preserved = $true
        }
        'Verify' {
            $result.python_version = Invoke-Checked $python @('-I','-c','import sys; print(sys.version)')
            $result.application = Invoke-Checked $python @('-I','-c',"import json, sys, townrecord, quorumrelay; from importlib.metadata import version; print(json.dumps({'version': townrecord.__version__, 'quorumrelay': version('quorumrelay'), 'executable': sys.executable, 'module': townrecord.__file__}))")
            $result.ffmpeg = (Invoke-Checked (Join-Path $app 'tools\ffmpeg.exe') @('-version')).Split("`n")[0]
            $result.ffprobe = (Invoke-Checked (Join-Path $app 'tools\ffprobe.exe') @('-version')).Split("`n")[0]
            $result.uv = Invoke-Checked (Join-Path $app 'tools\uv.exe') @('--version')
            $shell = New-Object -ComObject WScript.Shell
            $openShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'TownRecord\Open TownRecord.lnk'
            $stopShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'TownRecord\Stop TownRecord.lnk'
            foreach ($shortcut in @($openShortcut,$stopShortcut)) {
                if (!(Test-Path $shortcut)) { throw "Missing Start menu shortcut: $shortcut" }
                $link = $shell.CreateShortcut($shortcut)
                if ($link.TargetPath -ne $pythonw -or !$link.Arguments.StartsWith('-I -m townrecord.windows ')) { throw "Wrong bundled launcher shortcut: $shortcut" }
            }
            $startupShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'TownRecord.lnk'
            $result.startup_shortcut = Test-Path $startupShortcut
            if ($result.startup_shortcut -ne [bool]$Startup) { throw 'Startup shortcut differs from the requested installer option' }
            $job = Start-Process -FilePath $pythonw -ArgumentList @('-I','-m','townrecord.windows','start') -PassThru -WindowStyle Hidden
            $result.launch_pid = $job.Id
            $result.launch_started_utc = $job.StartTime.ToUniversalTime().ToString('o')
            $result.cancellation = 'Run Stop phase; never kill an unverified PID'
            $deadline = [DateTime]::UtcNow.AddSeconds(60)
            $response = $null
            do {
                try {
                    $response = Invoke-RestMethod 'http://127.0.0.1:8190/v1/desktop/info' -TimeoutSec 2
                    if ($response.desktop) { break }
                } catch { }
                Start-Sleep -Milliseconds 300
            } while ([DateTime]::UtcNow -lt $deadline)
            if (!$response -or !$response.desktop) { throw 'Installed desktop service did not become ready' }
            $result.desktop_ready = $true
        }
        'Stop' { Stop-Installed; $result.stopped = $true }
        'Recovery' {
            Stop-Installed
            $code = @'
import json, sys, sqlite3
from pathlib import Path
from townrecord.windows import load_settings, desktop_root
from townrecord.recovery import backup, restore
root = Path(sys.argv[1])
backup_path = root / 'profile-backup'
restored_path = root / 'restored-profile'
settings = load_settings(desktop_root())
manifest = backup(settings, backup_path)
restored = restore(backup_path, restored_path)
with sqlite3.connect(restored_path / 'townrecord.db') as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert manifest['files'] == restored['files']
print(json.dumps({'backup': str(backup_path), 'restored': str(restored_path), 'files': len(manifest['files']), 'integrity': 'ok'}))
'@
            $result.recovery = Invoke-Checked $python @('-I','-c',$code,$Evidence)
        }
        'Uninstall' {
            Stop-Installed
            $config = Join-Path $profile 'config.json'
            $before = (Get-FileHash $config -Algorithm SHA256).Hash
            $job = Start-Process (Join-Path $app 'unins000.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LOG=`"$Evidence\uninstall-inno.log`"") -Wait -PassThru -WindowStyle Hidden
            if ($job.ExitCode -ne 0) { throw "Uninstaller exited $($job.ExitCode)" }
            if (Test-Path $python) { throw 'Application interpreter remains installed' }
            if ((Get-FileHash $config -Algorithm SHA256).Hash -ne $before) { throw 'Uninstall changed profile configuration' }
            $result.configuration_preserved = $true
        }
    }
    $result.status = 'passed'
} catch {
    $result.status = 'failed'; $result.error = $_.Exception.Message
    throw
} finally {
    $result.finished_utc = [DateTime]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence "$Phase-result.json")
}
