; Inno Setup 6 script. Build with: ISCC /DAppVersion=0.2.0 packaging\installer.iss
; (scripts\build-desktop.ps1 passes AppVersion from backend\pyproject.toml.)
; Run PyInstaller first: this packs dist\QABuddy\.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppName=QABuddy
AppVersion={#AppVersion}
AppPublisher=QABuddy
DefaultDirName={localappdata}\Programs\QABuddy
DefaultGroupName=QABuddy
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=QABuddy-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\QABuddy.exe
DisableProgramGroupPage=yes

[Files]
Source: "..\dist\QABuddy\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\QABuddy"; Filename: "{app}\QABuddy.exe"
Name: "{autodesktop}\QABuddy"; Filename: "{app}\QABuddy.exe"

[Run]
Filename: "{app}\QABuddy.exe"; Description: "Launch QABuddy"; Flags: nowait postinstall skipifsilent
