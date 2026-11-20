; Job Hunt Agent - Windows installer (Inno Setup 6). Built by .github/workflows/windows-installer.yml:
;   ISCC.exe /DAppVersion=1.2.3 installer\JobHuntAgent.iss   ->  installer\Output\JobHuntAgentSetup.exe
; Installs for the current user only (no admin), into %LOCALAPPDATA%\JobHuntAgent. Installing a newer version
; on top keeps your data (data\ folder) and settings (.env); uninstalling keeps them too.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{6F1C2A0E-7B9D-4C55-9C1E-3A1B5D7E9F20}
AppName=Job Hunt Agent
AppVersion={#AppVersion}
AppPublisher=Job Hunt Agent (open source)
AppPublisherURL=https://github.com/Anindya-Arnob/job-hunt-agent
AppSupportURL=https://github.com/Anindya-Arnob/job-hunt-agent/issues
DefaultDirName={localappdata}\JobHuntAgent
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=JobHuntAgentSetup
SetupIconFile=..\scripts\job-hunt-agent.ico
UninstallDisplayIcon={app}\scripts\job-hunt-agent.ico
UninstallDisplayName=Job Hunt Agent
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "A ""Job Hunt Agent"" icon on my Desktop"; GroupDescription: "Icons:"

[Files]
Source: "..\backend\*"; DestDir: "{app}\backend"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,.pytest_cache,data"
Source: "..\frontend\dist\*"; DestDir: "{app}\frontend\dist"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CLAUDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"; Flags: uninsneveruninstall

[Icons]
Name: "{userprograms}\Job Hunt Agent"; Filename: "{app}\scripts\start.bat"; WorkingDir: "{app}\scripts"; IconFilename: "{app}\scripts\job-hunt-agent.ico"; Comment: "Start Job Hunt Agent (close its window to stop it)"; Flags: runminimized
Name: "{userprograms}\Job Hunt Agent - Chrome helper folder"; Filename: "{app}\extension"; Comment: "Load this folder in chrome://extensions (Developer mode, Load unpacked)"
Name: "{userdesktop}\Job Hunt Agent"; Filename: "{app}\scripts\start.bat"; WorkingDir: "{app}\scripts"; IconFilename: "{app}\scripts\job-hunt-agent.ico"; Comment: "Start Job Hunt Agent (close its window to stop it)"; Flags: runminimized; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows-setup.ps1"""; StatusMsg: "Installing Python, the app's packages and Claude Code (5-10 minutes the first time)..."; Flags: waituntilterminated; Check: not WizardSilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows-setup.ps1"" -Unattended"; StatusMsg: "Installing Python and the app's packages..."; Flags: waituntilterminated runhidden; Check: WizardSilent
Filename: "{app}\scripts\start.bat"; WorkingDir: "{app}\scripts"; Description: "Start Job Hunt Agent now"; Flags: postinstall nowait skipifsilent runminimized

[UninstallDelete]
; what the setup made (Python environment, caches); your data\ folder and .env stay
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\backend"

[Messages]
FinishedLabel=Job Hunt Agent is installed. Start it with the icon on your Desktop or in the Start menu.%n%nThe Chrome helper (for filling application forms): open chrome://extensions, switch on Developer mode, Load unpacked, and choose the "extension" folder (Start menu: "Job Hunt Agent - Chrome helper folder").

[Code]
// A running copy of the app (python from this app's .venv) is closed before files are replaced or removed.
procedure StopRunningApp();
var
  Code: Integer;
begin
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process python*,pythonw* -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''' +
       ExpandConstant('{app}') + '\.venv\*'' } | Stop-Process -Force"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Job Hunt Agent is removed. Your data and settings are kept in' + #13#10 + ExpandConstant('{app}') +
           #13#10 + '(folders "data" and the file ".env"). Delete that folder yourself if you no longer need them.',
           mbInformation, MB_OK);
end;
