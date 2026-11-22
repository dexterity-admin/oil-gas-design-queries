; Installer for the Library program (server PC). Documents and the index live in
; %LOCALAPPDATA%\DexMentor Library and are never touched by install, update or uninstall.
#define AppVersion GetEnv("APP_VERSION")
[Setup]
AppId={{6B6E2C1E-5B1E-4B7B-9B43-D3A1C0DE0002}
AppName=DexMentor Library
AppVersion={#AppVersion}
AppPublisher=Dexterity International
DefaultDirName={localappdata}\Programs\DexMentor Library
DefaultGroupName=DexMentor Library
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DexMentorLibrary-Setup
SetupIconFile=..\desktop\dexmentor.ico
UninstallDisplayIcon={app}\DexMentorLibrary.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
[Tasks]
Name: "autostart"; Description: "Start the Library automatically when I sign in to Windows (recommended for the server PC)"
Name: "desktopicon"; Description: "Create a desktop shortcut"
[Files]
Source: "..\dist\DexMentorLibrary\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"
Name: "{userdesktop}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"; Tasks: desktopicon
Name: "{userstartup}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"; Parameters: "--no-window"; Tasks: autostart
[Run]
Filename: "{app}\DexMentorLibrary.exe"; Description: "Start DexMentor Library and open the Library desk"; Flags: nowait postinstall skipifsilent
