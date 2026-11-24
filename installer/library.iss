; Installer for DexMentor Library (server PC). Documents, the index and the saved chats live in
; %LOCALAPPDATA%\DexMentor Library and are never touched by install, update or uninstall.
#define AppVersion GetEnv("APP_VERSION")
[Setup]
AppId={{6B6E2C1E-5B1E-4B7B-9B43-D3A1C0DE0002}
AppName=DexMentor Library
AppVersion={#AppVersion}
AppPublisher=Dexterity Design Services Pvt. Ltd.
DefaultDirName={localappdata}\Programs\DexMentor Library
DefaultGroupName=DexMentor Library
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DexMentorLibrary-Setup
SetupIconFile=..\desktop\library.ico
UninstallDisplayIcon={app}\DexMentorLibrary.exe
UninstallDisplayName=DexMentor Library
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Our own wizard: DexMentor artwork (python brand/make_wizard.py), brand colours and wording (see the code section).
WizardImageFile=art\library-panel-100.bmp,art\library-panel-125.bmp,art\library-panel-150.bmp,art\library-panel-175.bmp,art\library-panel-200.bmp,art\library-panel-250.bmp
WizardSmallImageFile=art\library-small-100.bmp,art\library-small-125.bmp,art\library-small-150.bmp,art\library-small-175.bmp,art\library-small-200.bmp,art\library-small-250.bmp
WizardImageAlphaFormat=none
DisableWelcomePage=no
DisableReadyPage=no
; Never restart Windows: the running Library is closed by PrepareToInstall below, so no file is in use.
AlwaysRestart=no
RestartIfNeededByRun=no
CloseApplications=no
RestartApplications=no
[Messages]
SetupWindowTitle=Install DexMentor Library
WelcomeLabel1=Welcome to DexMentor Library
WelcomeLabel2=The Library holds your office's engineering standards and answers the engineers' DexMentor Ask apps on the office network.%n%nInstall it on the one PC that stays on during working hours. This installs DexMentor Library [ver] for you only; Windows is never restarted.
FinishedHeadingLabel=DexMentor Library is running
FinishedLabel=Add documents on the Library desk. The Library keeps serving the office from the tray when the desk is closed, and keeps itself up to date.
FinishedLabelNoIcons=Add documents on the Library desk. The Library keeps serving the office from the tray when the desk is closed, and keeps itself up to date.
[Tasks]
Name: "autostart"; Description: "Start the Library automatically when I sign in to Windows (recommended for the server PC)"
Name: "desktopicon"; Description: "Create a desktop shortcut"
[InstallDelete]
; The earlier one-folder Python build kept its files next to the program; the engine now has its own folder.
Type: filesandordirs; Name: "{app}\_internal"
[Files]
Source: "..\dist\DexMentorLibrary\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
[Icons]
Name: "{group}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"
Name: "{userdesktop}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"; Tasks: desktopicon
Name: "{userstartup}\DexMentor Library"; Filename: "{app}\DexMentorLibrary.exe"; Parameters: "--no-window"; Tasks: autostart
[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2…"; Flags: waituntilterminated; Check: NeedsWebView2
Filename: "{app}\DexMentorLibrary.exe"; Description: "Start DexMentor Library and open the Library desk"; Flags: nowait postinstall skipifsilent
; A silent update (pushed by IT, or run from a script) must not leave the office without its Library.
Filename: "{app}\DexMentorLibrary.exe"; Parameters: "--no-window"; Flags: nowait; Check: (WizardSilent and not IsUpdate) or (IsUpdate and QuietStart)
Filename: "{app}\DexMentorLibrary.exe"; Flags: nowait; Check: IsUpdate and not QuietStart
[Code]
// ---------- our own look: ink header and welcome/finish pages, brand wording ----------
const
  INK = $2E2B13;   // #132b2e as Inno's $BBGGRR
  MIST = $BEB98F;  // #8fb9be

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';  // started by the app's "Restart and update"
end;

function QuietStart: Boolean;
begin
  Result := ExpandConstant('{param:QUIETSTART|0}') = '1';
end;

procedure PaintInk(Page: TNewNotebookPage);
begin
  Page.Color := INK;
end;

procedure InitializeWizard;
begin
  WizardForm.MainPanel.Color := INK;
  WizardForm.PageNameLabel.Font.Color := clWhite;
  WizardForm.PageNameLabel.Font.Name := 'Segoe UI Semibold';
  WizardForm.PageDescriptionLabel.Font.Color := MIST;
  PaintInk(WizardForm.WelcomePage);
  WizardForm.WelcomeLabel1.Font.Color := clWhite;
  WizardForm.WelcomeLabel1.Font.Name := 'Segoe UI Semibold';
  WizardForm.WelcomeLabel1.Font.Size := 16;
  WizardForm.WelcomeLabel2.Font.Color := MIST;
  PaintInk(WizardForm.FinishedPage);
  WizardForm.FinishedHeadingLabel.Font.Color := clWhite;
  WizardForm.FinishedHeadingLabel.Font.Name := 'Segoe UI Semibold';
  WizardForm.FinishedHeadingLabel.Font.Size := 16;
  WizardForm.FinishedLabel.Font.Color := MIST;
  WizardForm.RunList.Color := INK;
  WizardForm.RunList.Font.Color := clWhite;
  CreateOutputMsgPage(wpWelcome, 'Your library stays on this PC', 'Installing, updating and uninstalling never touch it', 'Documents, the search index and saved chats are kept in %LOCALAPPDATA%\DexMentor Library. They stay there through every update, and even if the Library is uninstalled.' + #13#10#13#10 + 'Updates arrive inside the app. A ready update installs when you choose, or by itself at night when the desk is closed.');
end;

function NeedsWebView2: Boolean;
var V: String;
begin
  Result := not (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'))
        and not (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  { The Library usually runs in the tray, so stop it first (the engine is in a job object and stops with it).
    Also the engine on its own, and the earlier Python build. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM DexMentorLibrary.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM DexMentorLibraryEngine.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
  NeedsRestart := False;
  Result := '';
end;
