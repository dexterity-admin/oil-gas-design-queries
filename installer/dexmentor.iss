; Installer for DexMentor Ask, the engineers' app. Installs for the current user: no administrator rights needed.
; The same AppId as the earlier "DexMentor" app, so this is an update over it: the downloaded model, the settings
; and the saved chats in %LOCALAPPDATA%\DexMentor are kept.
#define AppVersion GetEnv("APP_VERSION")
[Setup]
AppId={{6B6E2C1E-5B1E-4B7B-9B43-D3A1C0DE0001}
AppName=DexMentor Ask
AppVersion={#AppVersion}
AppPublisher=Dexterity Design Services Pvt. Ltd.
DefaultDirName={localappdata}\Programs\DexMentor Ask
DefaultGroupName=DexMentor Ask
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DexMentorAsk-Setup
SetupIconFile=..\desktop\dexmentor.ico
UninstallDisplayIcon={app}\DexMentorAsk.exe
UninstallDisplayName=DexMentor Ask
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Our own wizard: DexMentor artwork (python brand/make_wizard.py), brand colours and wording (see the code section).
WizardImageFile=art\ask-panel-100.bmp,art\ask-panel-125.bmp,art\ask-panel-150.bmp,art\ask-panel-175.bmp,art\ask-panel-200.bmp,art\ask-panel-250.bmp
WizardSmallImageFile=art\ask-small-100.bmp,art\ask-small-125.bmp,art\ask-small-150.bmp,art\ask-small-175.bmp,art\ask-small-200.bmp,art\ask-small-250.bmp
WizardImageAlphaFormat=none
DisableWelcomePage=no
DisableReadyPage=no
; Never restart Windows. The running app is closed by PrepareToInstall below, so no file is ever in use and
; nothing needs replacing at the next start-up.
AlwaysRestart=no
RestartIfNeededByRun=no
CloseApplications=no
RestartApplications=no
[Messages]
SetupWindowTitle=Install DexMentor Ask
WelcomeLabel1=Welcome to DexMentor Ask
WelcomeLabel2=Ask your office's engineering standards a question and get the answer with the page it came from.%n%nThis installs [name/ver] for you only. No administrator rights are needed, and Windows is never restarted.
FinishedHeadingLabel=DexMentor Ask is ready
FinishedLabel=DexMentor Ask finds the office Library on the network by itself, and keeps itself up to date.
FinishedLabelNoIcons=DexMentor Ask finds the office Library on the network by itself, and keeps itself up to date.
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
[Files]
Source: "..\dist\DexMentorAsk.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
[InstallDelete]
; The earlier Python launcher and its shortcuts
Type: files; Name: "{app}\DexMentor.exe"
Type: files; Name: "{userprograms}\DexMentor.lnk"
Type: files; Name: "{userprograms}\DexMentor\DexMentor.lnk"
Type: dirifempty; Name: "{userprograms}\DexMentor"
Type: files; Name: "{userdesktop}\DexMentor.lnk"
[Icons]
Name: "{group}\DexMentor Ask"; Filename: "{app}\DexMentorAsk.exe"
Name: "{userdesktop}\DexMentor Ask"; Filename: "{app}\DexMentorAsk.exe"; Tasks: desktopicon
[Run]
; The app window is Microsoft Edge WebView2, part of Windows 10/11 on most PCs; installed here only where missing.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2…"; Flags: waituntilterminated; Check: NeedsWebView2
Filename: "{app}\DexMentorAsk.exe"; Description: "Start DexMentor Ask"; Flags: nowait postinstall skipifsilent
Filename: "{app}\DexMentorAsk.exe"; Flags: nowait; Check: IsUpdate
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
  CreateOutputMsgPage(wpWelcome, 'Your work stays on this PC', 'Installing, updating and uninstalling never touch it', 'Your saved chats, the language model you downloaded and your settings are kept in your own Windows folder (%LOCALAPPDATA%\DexMentor). They stay there through every update, and even if DexMentor Ask is uninstalled.' + #13#10#13#10 + 'Updates arrive inside the app: when one is ready it asks you, closes for a few seconds, installs and opens again.');
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
  { Close the running app first: politely (it saves nothing late: every chat is already on disk), then for sure.
    The model runner it started is in a job object and stops with it. Also the earlier Python launcher, and the
    model runner that launcher may have left behind (only the one inside a DexMentor folder). }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM DexMentorAsk.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM DexMentorAsk.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM DexMentor.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -Command "Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''*\DexMentor\*'' } | Stop-Process -Force"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
  NeedsRestart := False;
  Result := '';
end;
