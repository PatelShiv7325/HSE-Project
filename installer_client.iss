; Builds "Global HSE Associates - Client.exe" (the Setup file). Needs Inno Setup 6.
#define AppName "Global HSE Associates - Client"
#define AppExe  "GlobalHSE-Client.exe"

[Setup]
AppId={{2C7D9B40-91E5-4F6B-8A3D-5E1B7C0A9202}
AppName={#AppName}
AppVersion=1.0.0
AppPublisher=Global HSE Associates
DefaultDirName={autopf}\Global HSE Associates Client
DefaultGroupName=Global HSE Associates
OutputDir=installer_output
OutputBaseFilename=HSE Main App - Client Setup
SetupIconFile=hse.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\GlobalHSE-Client\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
Type: files; Name: "{autodesktop}\Global HSE Associates.lnk"
Type: files; Name: "{group}\Global HSE Associates.lnk"

[Icons]
Name: "{autodesktop}\HSE Main App (Client)"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{group}\HSE Main App (Client)"; Filename: "{app}\{#AppExe}"
Name: "{group}\Change server address"; Filename: "{app}\{#AppExe}"; Parameters: "--change-ip"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start Global HSE Associates now"; Flags: nowait postinstall skipifsilent
