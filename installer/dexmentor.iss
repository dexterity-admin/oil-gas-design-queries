; Installer for the engineer app. Installs for the current user: no administrator rights needed.
#define AppVersion GetEnv("APP_VERSION")
[Setup]
AppId={{6B6E2C1E-5B1E-4B7B-9B43-D3A1C0DE0001}
AppName=DexMentor
AppVersion={#AppVersion}
AppPublisher=Dexterity International
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
