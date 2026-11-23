; Installer for the engineer app. Installs for the current user: no administrator rights needed.
#define AppVersion GetEnv("APP_VERSION")
[Setup]
AppId={{6B6E2C1E-5B1E-4B7B-9B43-D3A1C0DE0001}
AppName=DexMentor
AppVersion={#AppVersion}
AppPublisher=Dexterity Design Services Pvt. Ltd.
DefaultDirName={localappdata}\Programs\DexMentor
DefaultGroupName=DexMentor
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DexMentor-Setup
SetupIconFile=..\desktop\dexmentor.ico
UninstallDisplayIcon={app}\DexMentor.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
[Files]
Source: "..\dist\DexMentor.exe"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\DexMentor"; Filename: "{app}\DexMentor.exe"
Name: "{userdesktop}\DexMentor"; Filename: "{app}\DexMentor.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\DexMentor.exe"; Description: "Start DexMentor"; Flags: nowait postinstall skipifsilent
Filename: "{app}\DexMentor.exe"; Parameters: "--no-window"; Flags: nowait; Check: WizardSilent
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  { The launcher runs in the background, so Windows cannot ask it to close and an update over it aborted.
    Stop it, and the model runner it started (only the one inside a DexMentor folder). }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM DexMentor.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -Command "Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''*\DexMentor\*'' } | Stop-Process -Force"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;
