; Builds "Global HSE Associates - Server.exe" (the Setup file). Needs Inno Setup 6.
#define AppName "Global HSE Associates - Server"
#define AppExe  "GlobalHSE-Server.exe"

[Setup]
AppId={{8F3A6C1E-4B7D-4E1A-9C55-2A9E0D5B7101}
AppName={#AppName}
AppVersion=1.0.0
AppPublisher=Global HSE Associates
DefaultDirName={autopf}\Global HSE Associates Server
DefaultGroupName=Global HSE Associates
OutputDir=installer_output
OutputBaseFilename=HSE Main App - Server Setup
SetupIconFile=hse.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\GlobalHSE-Server\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
Type: files; Name: "{autodesktop}\Global HSE Associates (Server).lnk"
Type: files; Name: "{group}\Global HSE Associates (Server).lnk"

[Icons]
Name: "{autodesktop}\HSE Main App (Server)"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{group}\HSE Main App (Server)"; Filename: "{app}\{#AppExe}"
Name: "{group}\Show server address"; Filename: "{app}\{#AppExe}"; Parameters: "--show-ip"

[Run]
; let other PCs on the office network (Private / Domain) reach port 5052
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Global HSE Server"" dir=in action=allow protocol=TCP localport=5052 profile=private,domain"; Flags: runhidden
Filename: "{app}\{#AppExe}"; Description: "Start Global HSE Associates now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Global HSE Server"""; Flags: runhidden
