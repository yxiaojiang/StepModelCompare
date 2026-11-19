# STEP 相似检索客户 Demo

上传一个 STEP，查看历史库中的几何候选、百分制相似度、整体双向表面偏差和三维并排或叠加。预置的 37 件闭合单实体安装板及彩排文件均为「演示样例」。

**结果仅供几何比对。旧件能否采用，由工程负责人依据工程资料确认。**

## Windows 安装版

Windows 10/11 x64 上运行 `StepModelCompare-Studio-Setup-0.3.1-x64.exe`，按简体中文安装向导操作。默认安装到当前用户的 `%LOCALAPPDATA%\Programs\StepModelCompare Studio`。安装后双击 **Studio.exe**，在应用窗口内上传 STEP。窗口关闭时，随窗口启动的本机服务会退出。安装包包含 Python 应用和 WebView2 离线运行时安装程序，检测到缺少运行时时会调用该安装程序。

| 安装目录 | 用法 |
| --- | --- |
| `Data/Base` | 放入 `.step` 或 `.stp` 基础模型，可用子目录分类。打开「基础模型库」或上传查询时，Studio 会索引新增、修改和移除的模型。「模型库文件夹」按钮可直接打开此目录。安装时可选装 37 件演示模型，位于 `Data/Base/Demo`。 |
| `Data/.cache` | 保存基础模型索引 `base.sqlite3` 和模型库清单 `library-manifest.json`（清单版本、解析指纹、各文件相对路径、大小和 `mtime_ns`）。此目录位于 `Data` 下，再次编译时随 `Data` 一并保留，不会被下次 Compile 清空。 |
| `Data/test` | 安装时放入供用户试用的查询模型（与仓库 `Demo/samples/rehearsal/queries` 中的 5 个 STEP 相同）。该目录不会作为基础模型库自动导入，也不会写入 History。升级安装若已有同名文件则不覆盖。 |
| `Data/History` | 每次完成比对后生成一个查询编号目录，包含原始文件、查询结果 JSON、预览模型及所用候选 GLB。解析失败的文件不会写入 History。 |

上传后的居中进度框会显示解析、基础库更新、候选比对或结果保存阶段。候选阶段按实际完成数量更新百分比，结果归档后到达 100%。安装升级和卸载会保留 Base、History 与 test 数据；其中已有的 `Data/test` 同名文件不会被覆盖。

## 开发版启动

在项目根目录打开 PowerShell。Demo 分支附带 Three.js、冻结历史库和演示 STEP。新检出的副本先按「初始化或重建」创建本地 Python 环境，再启动服务。

```powershell
& Demo/.venv/Scripts/python.exe Demo/src/loopback_server.py --port 8765
```

打开 [本地 Demo](http://127.0.0.1:8765/)；端口被占用时可更改上面的端口和浏览器 URL。主窗口关闭服务使用 Ctrl+C。上传、导入和结果文件保留在 `Demo/data`。启动脚本限制 Python socket 到本机回环。

## 演示操作

在页面点「基础模型库」查看 37 件演示样例。只有第一次实际建库，或 `Data/Base` 中模型新增、删除、同名文件被修改，或清单/索引缺失、损坏、版本不匹配时，才会弹出与「正在比对 STEP」同结构的简洁加载框：左上为标题「正在加载基础模型库」，右上为真实百分比或扫描中的 `--`，中间为当前文件，下方为绿色条纹进度条，不含多余状态计数。无变化时（包括关闭后重开和程序重启）根据 `Data/.cache/library-manifest.json` 与实际目录的快速扫描结果直接列出模型，不出现加载框，也不重复解析。点击「模型库文件夹」可直接打开基础模型目录。关闭基础模型库后，按下列顺序通过「上传 STEP」选择安装目录 `Data/test`（或开发树中的 `Demo/samples/rehearsal/queries`）里的文件。每轮自动打开排第一的几何候选，待复核候选从「待复核」展开查看。

| 演示样例 | 页面操作和检查 |
| --- | --- |
| `SHOW-POSE-INCH.step` | 选择 `SHOW-P01`。并排拖动、滚轮缩放，两个视图联动。切到双色叠加并改变历史件透明度，检查两个模型重合。切到另一候选再返回，检查模型与摘要同步更新。 |
| `SHOW-HOLE.step` | 选择 `SHOW-H01`，点击「样例标注 · 定位孔区」。在「仅查询件」和「仅历史件」之间切换，比较标注框内的通孔。读数为同一次双向网格采样的孔区最大表面偏差，单位为 mm。 |
| `SHOW-SYMMETRIC.step` | 在「待复核」中选择 `SHOW-S01`，保留联动并排。页面应说明存在多个不同的合格姿态。 |
| `SHOW-DAMAGED.stp` | 确认失败提示和候选清空。继续上传 `SHOW-POSE-RETRY.stp`，验证 .stp 和错误后的查询恢复。 |

全库粗排依据主轴尺寸、面积和体积。入围件用几何生成的 24 个初值做刚体拟合，精排取双向 RMS 的较大值。页面最多显示 5 个通过对齐检查的几何候选。结果列表显示模型名称、百分制相似度和必要的「待复核」状态。六项误差、得分、零分阈值和对齐原因在「详情」中查看。孔区读数是网格估算。参考点在比对完成后仅参与验收。

## 几何相似度

候选和待复核模型均显示百分制相似度。需要复核时，卡片标明「待复核」。点击分数旁的「详情」查看模型、总分、各项实测误差、实得分、满分及零分阈值。可点击「关闭」或按 Esc 返回当前三维视图。查询结果 JSON 保存总分、分项和误差。排序按双向 RMS，姿态歧义或对齐失败的模型保留待复核状态与并排限制。

| 现有度量 | 满分 | 该项降为 0 分的误差 |
| --- | ---: | ---: |
| 双向表面 RMS | 40 | 2.0 mm |
| 双向表面 P95 | 20 | 2.0 mm |
| 双向最大表面偏差 | 10 | 8.0 mm |
| 主轴尺寸 | 20 | 10% |
| 表面积 | 5 | 15% |
| 体积 | 5 | 15% |

每项从满分线性扣减，扣到 0 为止。表面项取两个方向中的较大误差，尺寸项取三个主轴相对误差中的最大值。相对误差是两值之差的绝对值除以两值中的较大者。阈值当前用于演示评分，适用性待客户样本验证。

## 初始化或重建

在已安装 Python 3.13 的 Windows x64 上，首次准备依赖时执行以下命令。安装完成后可离线运行。

```powershell
python -m venv Demo/.venv
& Demo/.venv/Scripts/python.exe -m pip install -r Demo/src/requirements.txt
```

默认使用随分支提供的 `Demo/data/library.sqlite3`。该文件与冻结清单一并校验。建新库时用 `--db` 指定独立路径，再在启动前设置 `STEP_DEMO_LIBRARY`。

```powershell
& Demo/.venv/Scripts/python.exe Demo/src/library.py Demo/samples/catalog.json --db Demo/data/library-local.sqlite3 --report Demo/data/library-local-import.json
$env:STEP_DEMO_LIBRARY = (Resolve-Path Demo/data/library-local.sqlite3).Path
```

同一料号的不同发布修订应使用各自稳定的模型标识。清单条目包含 `id`、`file`、`source`，以及可选的 `part_number`、`revision`。缺修订显示「未知」。导入异常保存在库内和导入报告。32 MB 内的闭合单实体 STEP 可走普通上传流程。

需要从 OCCT 重新生成演示文件时，运行 `Demo/src/make_samples.py` 的 `development` 和 `catalogs`。STEP 文件头含生成时间，重新生成或重建库后应为新产物保留独立冻结记录和彩排结果。

## 实测记录

- [开发样本](evidence/01-development.md)
- [SQLite 与排序联调](evidence/02-integration.md)
- [冻结与断网彩排](evidence/03-rehearsal.md)
- [Windows 安装版与进度回归](evidence/04-studio.md)
- [中文安装与相似度核验](evidence/05-studio-similarity.md)
- [评分详情核验](evidence/06-score-details.md)

自动彩排在本机对服务和 Edge 禁止外连后通过，两个预登记找回目标均排第 1。上传到结果的实际等待时间及现场尚待核验事项见彩排记录。

## 构建 Windows 安装包

Python、网页资源、配置和安装器定义集中在 `Demo/src`。构建机需要 64 位 Python 3.13 和 Inno Setup 6，本次使用 6.7.3。可直接运行 `Package.bat` 生成安装包，也可以单独运行 `Compile.bat`。两个工具脚本均支持从任意工作目录执行。

```powershell
& Demo/Tools/Compile.bat
& Demo/Tools/Package.bat
```

- `Compile.bat` 检查 Python 环境及构建依赖，编译到 `Demo/release/bundle/Studio/Studio.exe`。再次编译会更新程序文件，并完整保留已有的 `Studio/Data`（不覆盖其中任何已有文件，包括 `Base`、`History`、`test` 及用户自行放入的内容）。若还没有 `Data/test`，编译结束后会从 `Demo/samples/rehearsal/queries` 补入默认试用模型，已有同名文件不覆盖。若还没有 `Data`，首次启动 Studio 会创建 `Base`、`History`、`test` 等工作目录。
- `Package.bat` 先构建当前代码，再生成 `Demo/release/installer/StepModelCompare-Studio-Setup-0.3.1-x64.exe`。安装包会创建 `Data/test` 并写入上述试用模型（`onlyifdoesntexist`，升级不覆盖），不会把运行中的整份 `Data`（History 或缓存）打进安装包。首次缺少 WebView2 离线运行时缓存时，包脚本会从微软下载。[Microsoft WebView2 下载](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)

简体中文资源来自 Inno Setup 6.7.3 的翻译文件，许可证随源码保留。[Inno Setup 翻译](https://jrsoftware.org/inno-setup/translations)
