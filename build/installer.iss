#define MyAppName "C2K FPS Perception Test"
#define MyAppVersion "1.0.0"
#define MyAppExeName "C2K FPS Perception Test.exe"

[Setup]
AppId={{A5572697-5B02-456A-9B57-706B6D9925AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=cs2kitchen
VersionInfoCompany=cs2kitchen
VersionInfoDescription=Blind FPS perception testing for Counter-Strike 2
VersionInfoVersion=1.0.0.0
VersionInfoCopyright=Copyright (c) 2026 cs2kitchen
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=C2K FPS Perception Test Setup
SetupIconFile=..\data\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts"; Flags: unchecked

[Files]
Source: "..\dist\C2K FPS Perception Test.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\C2K FPS Perception Test"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\C2K FPS Perception Test"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open C2K FPS Perception Test"; Flags: nowait postinstall skipifsilent
