; Inno Setup script. Build with: ./build_installer.ps1  (it stages build\stage first).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "AI Note Taker"
; The signed, bundled Python interpreter runs the app (see build_installer.ps1 for why).
#define AppExe "runtime\pythonw.exe"
#define AppArgs "-m ai_note_taker.desktop"

[Setup]
AppId={{79C43603-AD75-4081-A810-132A23D6E025}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SaiKrishna Perugu
AppComments=Record meetings and get AI transcripts and notes
; Per-user install: no administrator rights needed. Lands in %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=AI-Note-Taker-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Close a running copy (GUI and its background recorder) before upgrading or uninstalling.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Remove files from earlier layouts so upgrades never leave stale binaries behind.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\site-packages"
Type: files; Name: "{app}\AI Note Taker.exe"

[Files]
Source: "..\build\package\app\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Comment: "Record meetings with AI transcripts and notes"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Compiled .pyc files are created after install, so Inno does not track them.
Type: filesandordirs; Name: "{app}"

; Recordings (Documents\AI Note Taker) and settings (%APPDATA%\AI Note Taker) are user data and are kept on uninstall.
