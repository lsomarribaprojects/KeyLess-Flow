; installer.iss — Inno Setup script for KeyLess Flow (Windows installer)
;
; Build prerequisite: dist\KeyLessFlow\ must exist (produced by build.ps1 / PyInstaller).
; Compile with: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; Output:       dist\KeyLessFlow-Setup.exe (single-file installer ~135 MB)

#define MyAppName "KeyLess Flow"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Sinsajo Creators"
#define MyAppURL "https://github.com/lsomarribaprojects/KeyLess-Flow"
#define MyAppExeName "KeyLessFlow.exe"
#define MyAppId "{{B7E8C0E4-6F2A-4B1C-9C8E-1A2B3C4D5E6F}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=KeyLessFlow-Setup
SetupIconFile=keylessflow.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Iniciar KeyLess Flow con Windows"; GroupDescription: "Inicio del sistema:"; Flags: unchecked

[Files]
; Include everything PyInstaller produced. {#KeyLessFlow.exe} ships at the root,
; _internal\ has all bundled Qt/Python/numpy/etc.
Source: "dist\KeyLessFlow\KeyLessFlow.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\KeyLessFlow\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Startup-folder shortcut — auto-start with Windows when user opted in
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Offer to launch the app on the final wizard screen.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill any running instance before uninstall so files aren't locked.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillKeyLessFlow"

[UninstallDelete]
; The %LOCALAPPDATA%\KeyLessFlow\ data dir (db, .env, audio cache) is intentionally
; left in place on uninstall so reinstalls preserve user state. Add the line below
; if you ever want a "clean uninstall" option:
; Type: filesandordirs; Name: "{localappdata}\KeyLessFlow"

[Code]
// Show a friendly first-run reminder after install.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // No-op for now. Future: open the landing page or first-run helper.
  end;
end;
