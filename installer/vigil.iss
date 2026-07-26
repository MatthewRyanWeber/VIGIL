; Vigil — Inno Setup script
;
; Builds Vigil-<version>-setup.exe from a PyInstaller onedir build in dist\Vigil.
;
;   pyinstaller --noconfirm --onedir --windowed --name Vigil ^
;       --icon static\vigil.ico ^
;       --add-data "index.html;." --add-data "core;core" --add-data "static;static" ^
;       vigil.py
;   iscc /DAppVersion=2.0 installer\vigil.iss
;
; Installs per-user into %LOCALAPPDATA%\Programs\Vigil. That is deliberate:
; Vigil keeps config.json, status.json, the HTTPS keypair and its log next to
; the exe, and a standard user cannot write to Program Files. Per-user also
; means no UAC prompt.

#ifndef AppVersion
  #define AppVersion "2.0"
#endif

#define AppName    "Vigil"
#define AppExeName "Vigil.exe"
#define AppPublisher "Matthew Weber"
#define AppURL     "https://github.com/MatthewRyanWeber/VIGIL"

[Setup]
AppId={{7B4D911F-25AD-43AD-A95A-6B8E518C2444}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\static\vigil.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Offer to shut Vigil down instead of failing on locked files during an upgrade.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup";     Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"

[Files]
; Everything PyInstaller produced. User data (config.json, status.json,
; *.pem, vigil.log) is never shipped, so an upgrade cannot clobber it.
Source: "..\dist\Vigil\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\docs\WINDOWS.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "..\LICENSE";        DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "Network device monitor"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Same value name and command shape core\autostart.py writes, so the tray
; menu's "Start Vigil when I sign in" checkbox reflects this immediately.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Vigil"; \
    ValueData: """{app}\{#AppExeName}"" --tray --no-browser"; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Regenerated on next launch; safe to remove. config.json is handled in [Code].
Type: files;      Name: "{app}\vigil.log"
Type: files;      Name: "{app}\vigil.log.*"
Type: files;      Name: "{app}\status.json"
Type: files;      Name: "{app}\vigil-cert.pem"
Type: files;      Name: "{app}\vigil-key.pem"
Type: files;      Name: "{app}\.vigil-trust-installed"
Type: dirifempty; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  ConfigPath: String;
begin
  if CurStep = usPostUninstall then
  begin
    // The tray toggle can write this after install, in which case no
    // uninsdeletevalue record exists — remove it unconditionally.
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'Vigil');

    ConfigPath := ExpandConstant('{app}\config.json');
    if FileExists(ConfigPath) then
    begin
      if SuppressibleMsgBox('Also delete your Vigil configuration (rooms and devices)?',
                            mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DeleteFile(ConfigPath);
        DeleteFile(ExpandConstant('{app}\config.backup.json'));
        RemoveDir(ExpandConstant('{app}'));
      end;
    end;
  end;
end;
