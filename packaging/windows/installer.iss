; Windows installer (Inno Setup 6). Built by .github/workflows/build.yml:
;   iscc /DAppVersion=1.0.42 packaging\windows\installer.iss
; Per-user install, no administrator rights. Running it again updates in place: it closes the app,
; removes the previous program files and installs the new ones. Models, settings, notes and logs live
; outside the program folder, so updates and uninstalls never touch them.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; Keep this AppId forever: it is how Windows recognises updates of the same app.
AppId={{6B0F3C1E-7A0B-4E59-9C5E-2D9B1E4F7A10}
AppName=Voice Notes
AppVersion={#AppVersion}
AppVerName=Voice Notes {#AppVersion}
DefaultDirName={localappdata}\Programs\Voice Notes
DefaultGroupName=Voice Notes
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\Voice Notes.exe
OutputDir=..\..\dist
OutputBaseFilename=VoiceNotes-Setup
Compression=lzma2/normal
SolidCompression=no
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Old program files only (never user data): avoids stale libraries mixing with the new version.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\Voice Notes\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Voice Notes"; Filename: "{app}\Voice Notes.exe"
Name: "{autodesktop}\Voice Notes"; Filename: "{app}\Voice Notes.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Voice Notes.exe"; Description: "Open Voice Notes"; Flags: nowait postinstall skipifsilent
