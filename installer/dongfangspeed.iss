; -*- 东方神速（East Speed）Inno Setup 安装脚本 -*-
; 编译前先用 Python 3.11/3.12 执行：python tools/build_exe.py portable
; 然后用 Inno Setup 6 编译本脚本：ISCC.exe installer\dongfangspeed.iss

#define MyAppName "东方神速"
#define MyAppNameEn "DongFangSpeed"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "东方神速开源项目 (GPL-3.0)"
#define MyAppExeName "DongFangSpeed.exe"

[Setup]
AppId={{6E1A4C92-7D3B-4F85-9C21-3A8E0D5B7F12}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=DongFangSpeed-Setup-1.0.0
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
ShowLanguageDialog=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 仅清理程序释放出的浏览器扩展目录；用户下载与 %APPDATA% 配置默认保留
Type: filesandordirs; Name: "{app}\browser_extension"
