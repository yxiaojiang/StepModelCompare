#define AppName "STEP 模型比对工作台"
#define AppVersion "0.3.1"
#define WebViewName "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

[Setup]
AppId={{15A62028-EEA3-4DA9-9F9F-E8801ABA6E02}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\StepModelCompare Studio
DefaultGroupName={#AppName}
OutputBaseFilename=StepModelCompare-Studio-Setup-{#AppVersion}-x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
AppMutex=StepModelCompareStudio.Instance
UninstallDisplayIcon={app}\Studio.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
ShowLanguageDialog=no
LanguageDetectionMethod=none

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "demos"; Description: "安装 37 个 STEP 演示模型"
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Dirs]
Name: "{app}\Data\Base"; Flags: uninsneveruninstall
Name: "{app}\Data\History"; Flags: uninsneveruninstall

[Files]
Source: "{#WebViewInstaller}"; DestName: "{#WebViewName}"; Flags: dontcopy nocompression
Source: "{#BundleDir}\Studio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs
Source: "{#BaseDir}\*"; DestDir: "{app}\Data\Base\Demo"; Tasks: demos; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Studio.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Studio.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Studio.exe"; Description: "启动 {#AppName}"; Flags: postinstall nowait skipifsilent

[Code]
const
  WebViewKey = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView(root: Integer): Boolean;
var
  version: String;
begin
  Result := RegQueryStringValue(root, WebViewKey, 'pv', version)
    and (version <> '') and (version <> '0.0.0.0');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  exitCode: Integer;
begin
  Result := '';
  if HasWebView(HKLM32) or HasWebView(HKCU) then
    Exit;
  ExtractTemporaryFile('{#WebViewName}');
  if not Exec(ExpandConstant('{tmp}\{#WebViewName}'), '/silent /install', '',
    SW_HIDE, ewWaitUntilTerminated, exitCode) then
    Result := '无法启动 Microsoft Edge WebView2 运行时安装程序。错误代码：' + IntToStr(exitCode)
  else if not (HasWebView(HKLM32) or HasWebView(HKCU)) then
    Result := 'Microsoft Edge WebView2 运行时安装失败。错误代码：' + IntToStr(exitCode);
end;
