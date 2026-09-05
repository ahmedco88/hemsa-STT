; Inno Setup script for Hemsa.
; Build:  .venv\Scripts\pyinstaller.exe hemsa.spec --noconfirm
;         "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\hemsa.iss
; Output: installer\out\HemsaSetup-<version>.exe
;
; Per-USER install on purpose (PrivilegesRequired=lowest):
;   - no UAC prompt, which removes one of the two scary dialogs a first-time
;     user meets (SmartScreen is the other, see README)
;   - the app can update itself later without elevation
;   - autostart is already HKCU-scoped, so a per-machine install would be
;     inconsistent with it anyway

#define AppName "Hemsa"
#define AppVersion "0.5.2"
#define AppPublisher "Ahmed Al-Obaidi"
#define AppURL "https://github.com/ahmedco88/hemsa-STT"

[Setup]
AppId={{7C5C2B1E-9E1D-4E2A-9E3B-1B6F1E4A2D77}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Hemsa.exe is locked while running; without this the upgrade half-installs and
; leaves a broken mix of old and new files. The name must match winutil exactly.
AppMutex=Global\HemsaSingleInstance
OutputDir=out
OutputBaseFilename=HemsaSetup-{#AppVersion}
SetupIconFile=..\assets\hemsa.ico
UninstallDisplayIcon={app}\Hemsa.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; unchecked: Hemsa lives in the tray and is rarely launched by hand
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; recursesubdirs is mandatory - without it _internal\ is silently skipped and
; the installed 7 MB exe dies instantly on launch.
; "Start Hemsa.bat" is deliberately NOT shipped: it runs .venv\Scripts\pythonw.exe,
; which does not exist in an installed build.
Source: "..\dist\Hemsa\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
; The bundle carries other people's libraries (ffmpeg, PortAudio, sherpa-onnx and
; the rest), several under licences that require the notice to travel WITH the
; binary. A link in the README does not do that for someone who only ran the exe.
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Hemsa.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Hemsa.exe"; Tasks: desktopicon

[Registry]
; UNINSTALL-only, and the flag list is the whole point (fixed 2026-09-03).
; The app owns this value (winutil.set_autostart); without uninsdeletevalue an
; uninstall leaves the Run key pointing at a deleted exe, which then fails
; silently on every login forever. No ValueData here, so this never CREATES it.
; `deletevalue` must NOT come back: it fires on INSTALL, so every upgrade
; silently switched autostart off while the Settings toggle still read "on".
; Found on Ahmed's own PC 2026-09-03, where Hemsa had quietly stopped starting
; at login. tests/test_autostart.py fails the build if it returns.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: none; ValueName: "Hemsa"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Hemsa.exe"; Description: "Start Hemsa now"; \
  Flags: nowait postinstall skipifsilent

; NOTE: there is deliberately no [UninstallDelete] for {localappdata}\Hemsa.
; That folder holds the user's settings, dictionary, history AND the 661 MB
; speech model. Silently deleting a download the user would have to fetch again
; is the worst possible default; the uninstaller asks instead (see below).

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Hemsa');
    if DirExists(DataDir) then
      if MsgBox('Also delete your Hemsa settings, dictionary, history and the '
                + 'downloaded speech model (about 660 MB)?' + #13#10#13#10
                + 'Choose No if you plan to reinstall Hemsa.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
