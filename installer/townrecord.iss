#ifndef PayloadDir
  #error PayloadDir must name the staged payload
#endif
#ifndef OutputDir
  #error OutputDir must name the output directory
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{46ECEE68-85B7-4D93-B8A0-49BF6E61958F}
AppName=TownRecord
AppVersion={#AppVersion}
AppPublisher=Scott Converse
AppPublisherURL=https://github.com/scottconverse/TownRecord
DefaultDirName={localappdata}\Programs\TownRecord
DefaultGroupName=TownRecord
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=TownRecord-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName=TownRecord
UninstallDisplayIcon={app}\python\pythonw.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
LicenseFile=..\LICENSE

[Tasks]
Name: startup; Description: "Start TownRecord when I sign in to Windows"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Open TownRecord"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m townrecord.windows open"; WorkingDir: "{app}"
Name: "{group}\Stop TownRecord"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m townrecord.windows stop"; WorkingDir: "{app}"
Name: "{group}\Uninstall TownRecord"; Filename: "{uninstallexe}"
Name: "{userstartup}\TownRecord"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m townrecord.windows start"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m townrecord.windows open"; Description: "Open TownRecord"; Flags: nowait postinstall skipifsilent

[Code]
function StopTownRecord(): Boolean;
var
  PythonPath: String;
  ResultCode: Integer;
begin
  PythonPath := ExpandConstant('{app}\python\python.exe');
  Result := True;
  if FileExists(PythonPath) then
  begin
    Result := Exec(PythonPath, '-I -m townrecord.windows stop', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if Result then Result := ResultCode = 0;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopTownRecord() then
    Result := 'TownRecord could not stop safely. Use Stop TownRecord from the Start menu, then try again. Your saved data has not been removed.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := StopTownRecord();
  if not Result then
    MsgBox('TownRecord could not stop safely. Use Stop TownRecord from the Start menu, then try uninstalling again. Your saved data has not been removed.', mbError, MB_OK);
end;
