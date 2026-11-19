# 计划第 2 项 · 历史库、排序与页面联调

实测日期 2026-11-18。下列命令使用当前源码路径，在项目根目录运行。

```powershell
& Demo/.venv/Scripts/python.exe Demo/src/make_samples.py catalogs
& Demo/.venv/Scripts/python.exe Demo/src/library.py Demo/samples/development-catalog.json --db Demo/data/development.sqlite3 --report Demo/evidence/development-library.json
& Demo/.venv/Scripts/python.exe Demo/src/library.py Demo/samples/catalog.json --report Demo/evidence/library-import.json
& Demo/.venv/Scripts/python.exe Demo/src/calibrate.py Demo/data/development.sqlite3 Demo/evidence/k-calibration.json
& Demo/.venv/Scripts/python.exe Demo/src/calibrate.py Demo/data/development.sqlite3 Demo/evidence/k5-calibration.json --k 5
```

开发库导入 34/34，客户演示库导入 37/37，均为「演示样例」。后者的 SQLite 文件为 3,035,136 字节，保存原始 STEP、标识、来源、料号、修订及尺寸、面积、体积、三角网格和 GLB。含一个有效 STEP 和一个故意损坏 STEP 的独立临时库验证了保留导入错误并继续入库。

开发查询只使用 DEV-POSE-INCH、DEV-HOLE 与 DEV-SYMMETRIC-POSE。彩排查询及其同源历史件均不在开发检索或调参中。

| K | 同件目标 | 孔位目标 | 对称目标 |
| ---: | --- | --- | --- |
| 4 | 第 1，15.190 s | 第 1，11.058 s | 待复核，9.303 s |
| 8 | 第 1，31.257 s | 第 1，20.785 s | 待复核，19.394 s |
| 5 | 第 1，19.717 s | 第 1，14.260 s | 待复核，12.918 s |

选 K=5，为通过检查的 5 个结果提供名额。对这两件开发查询，三种 K 均显示 4 条有效候选，开发集未测得 K=8 带来的找回提升。记录保留 [K=4/8](k-calibration.json)与 [K=5](k5-calibration.json)的实测排序、待复核分类和用时。

在独立 PowerShell 启动开发库服务，然后另起终端执行 `Demo/.venv/Scripts/python.exe -X utf8 Demo/src/integration.py`。

```powershell
$env:STEP_DEMO_LIBRARY = (Resolve-Path Demo/data/development.sqlite3).Path
$env:STEP_DEMO_QUERIES = Join-Path (Resolve-Path Demo/data).Path 'development-queries'
& Demo/.venv/Scripts/python.exe Demo/src/loopback_server.py --port 8765
```

Edge 通过普通上传入口提交 DEV-POSE-INCH，15.716 s 后完成。依序显示 DEV-BASE、DEV-VARIANT-01、DEV-VARIANT-02、DEV-VARIANT-03，两视图联动旋转缩放通过，切换至 DEV-VARIANT-01 后 0.112 s 可操作。首次浏览器核验脚本误用相对 API URL，服务端该轮实际已正常返回，修正检查脚本后复跑通过。详见 [联调记录](integration-dev.json)、[页面截图](integration-dev.png)和 [SQLite 导入记录](library-import.json)。

开发联调通过。下一步冻结演示库和参数，按预登记的独立样例进行断网彩排。
