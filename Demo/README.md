# STEP 相似检索客户 Demo

上传一个 STEP，查看历史库中的几何候选、整体双向表面偏差和三维并排或叠加。预置的 37 件闭合单实体安装板及彩排文件均为「演示样例」。

**结果仅供几何比对。旧件能否采用，由工程负责人依据工程资料确认。**

## 启动

在项目根目录打开 PowerShell。Demo 分支附带 Three.js、冻结历史库和演示 STEP。新检出的副本先按「初始化或重建」创建本地 Python 环境，再启动服务。

```powershell
& Demo/.venv/Scripts/python.exe Demo/loopback_server.py --port 8765
```

打开 [本地 Demo](http://127.0.0.1:8765/)；端口被占用时可更改上面的端口和浏览器 URL。主窗口关闭服务使用 Ctrl+C。上传、导入和结果文件保留在 `Demo/data`。启动脚本限制 Python socket 到本机回环。

## 演示操作

在页面点「历史库」查看 37 件演示样例和修订。关闭历史库后，按下列顺序通过「上传 STEP」选择 `Demo/samples/rehearsal/queries` 中的文件。每轮自动打开排第一的几何候选，待复核候选从「待复核」展开查看。

| 演示样例 | 页面操作和检查 |
| --- | --- |
| `SHOW-POSE-INCH.step` | 选择 `SHOW-P01`。并排拖动、滚轮缩放，两个视图联动。切到双色叠加并改变历史件透明度，检查两个模型重合。切到另一候选再返回，检查模型与摘要同步更新。 |
| `SHOW-HOLE.step` | 选择 `SHOW-H01`，点击「样例标注 · 定位孔区」。在「仅查询件」和「仅历史件」之间切换，比较标注框内的通孔。读数为同一次双向网格采样的孔区最大表面偏差，单位为 mm。 |
| `SHOW-SYMMETRIC.step` | 在「待复核」中选择 `SHOW-S01`，保留联动并排。页面应说明存在多个不同的合格姿态。 |
| `SHOW-DAMAGED.stp` | 确认失败提示和候选清空。继续上传 `SHOW-POSE-RETRY.stp`，验证 .stp 和错误后的查询恢复。 |

全库粗排依据主轴尺寸、面积和体积。入围件用几何生成的 24 个初值做刚体拟合，精排取双向 RMS 的较大值。页面最多显示 5 个通过对齐检查的几何候选。尺寸差是查询件减历史件的主轴尺寸差。孔区读数是网格估算。参考点在比对完成后仅参与验收。

## 初始化或重建

在已安装 Python 3.13 的 Windows x64 上，首次准备依赖时执行以下命令。安装完成后可离线运行。

```powershell
python -m venv Demo/.venv
& Demo/.venv/Scripts/python.exe -m pip install -r Demo/requirements.txt
```

默认使用随分支提供的 `Demo/data/library.sqlite3`。该文件与冻结清单一并校验。建新库时用 `--db` 指定独立路径，再在启动前设置 `STEP_DEMO_LIBRARY`。

```powershell
& Demo/.venv/Scripts/python.exe Demo/library.py Demo/samples/catalog.json --db Demo/data/library-local.sqlite3 --report Demo/data/library-local-import.json
$env:STEP_DEMO_LIBRARY = (Resolve-Path Demo/data/library-local.sqlite3).Path
```

同一料号的不同发布修订应使用各自稳定的模型标识。清单条目包含 `id`、`file`、`source`，以及可选的 `part_number`、`revision`。缺修订显示「未知」。导入异常保存在库内和导入报告。32 MB 内的闭合单实体 STEP 可走普通上传流程。

需要从 OCCT 重新生成演示文件时，执行 `make_samples.py development` 和 `make_samples.py catalogs`。STEP 文件头含生成时间，重新生成或重建库后应为新产物保留独立冻结记录和彩排结果。

## 实测记录

- [开发样本](evidence/01-development.md)
- [SQLite 与排序联调](evidence/02-integration.md)
- [冻结与断网彩排](evidence/03-rehearsal.md)

自动彩排在本机对服务和 Edge 禁止外连后通过，两个预登记找回目标均排第 1。上传到结果的实际等待时间及现场尚待核验事项见彩排记录。
