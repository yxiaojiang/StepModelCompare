# Studio 0.2.0 安装版验证

2026-11-19，Windows x64。参考 Fighter97 的目录式 PyInstaller 打包、Inno Setup 安装和 WebView2 宿主。本项目将现有 FastAPI 随桌面窗口启停，保留 HTML、Three.js、OCCT 比对和上传 API。安装器使用当前用户可写的目录，以支持在安装文件夹内管理 Base 与 History。[PyInstaller 资源路径](https://pyinstaller.org/en/stable/runtime-information.html) · [Microsoft WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

## 构建与安装

0.2.0 当时使用 `Demo/.venv/Scripts/python.exe Demo/packaging/build_studio.py`。现行脚本见 [构建说明](../README.md)。本轮产物位于本地 `Demo/release/installer/StepModelCompare-Studio-Setup-0.2.0-x64.exe`，308,177,395 字节，SHA-256 为 `4A4C313EC89777A965AB685DBD2A63BC6749A24896095542972B6F65B75B73EA`。

安装包内嵌的 Microsoft x64 WebView2 离线运行时安装器的 Authenticode 校验为 Valid，签署者为 Microsoft Corporation。Studio 安装包为未签名构建。测试机已有 WebView2 157.0.4341.60，缺少 WebView2 的电脑上自动补装仍待运行验证。

在包含中文和空格的独立路径运行安装器，使用 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /TASKS=demos /DIR="…\安装 验证"`。实际安装生成 `Studio.exe`、`Data/Base`、`Data/History`，其中 `Data/Base/Demo` 有 37 件演示 STEP。覆盖安装最终构建后，已有自定义基础模型的 SHA-256 及 26 个 History 文件的 SHA-256 保持一致。

卸载实测移除了程序，Base 与 History 共 81 个文件的哈希保持一致。另以 `/MERGETASKS=!demos,!desktopicon` 在全新目录安装，验证空 Base 和 History 均创建成功。

## 安装版检查

- 从独立工作目录启动已安装的 `Studio.exe`，进程 PATH 仅保留 Windows 系统目录。程序自行启动回环服务并打开内嵌 WebView2，关闭窗口后服务退出。
- 普通上传 `SHOW-HOLE.step`，中央进度框按候选完成数推进。最终构建记录到 0%、8%、30%、42%、54%、78%、100%，归档后自动显示结果。SHOW-H01 排第一，孔区仍显示 4.996 mm。[比对进度](studio-progress.png) · [比对结果](studio-result.png) · [核验数据](studio-verification.json)
- 上传 `SHOW-DAMAGED.stp`，中央提示显示解析失败，历史目录没有形成该查询的归档。随后上传有效文件成功。
- 放入带中文子目录和大写 `.STP` 扩展名的安装板，上传入口自动索引并返回该基础模型为第一候选。完成查询在 `Data/History/<查询编号>` 中保留原名和原始字节。移除基础件后目录索引更新，而旧查询仍通过归档的 GLB 加载同一个候选。
- 设置内嵌 WebView2 的外网代理为不可用端口后，测试期间页面只访问本机回环。另使用原浏览器入口回归，同件、孔位、歧义、损坏与重试流程通过。

真实客户 STEP、其他 Windows 环境和企业分发的数字签名待现场验证。几何比对和正式采用的边界沿用 STEP-SIM-002。
