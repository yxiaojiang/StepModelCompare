# 计划第 1 项 · 开发样本实测

实测日期 2026-11-18。工作区没有客户 STEP 或现成几何比对程序。以下五件均为 OCCT 自制「演示样例」。

按当前源码路径，在根目录运行 `Demo/.venv/Scripts/python.exe Demo/src/make_samples.py development` 和 `Demo/.venv/Scripts/python.exe Demo/src/development.py`。在独立 PowerShell 执行下列命令启用 JS MIME，再运行 `Demo/.venv/Scripts/python.exe Demo/src/check_browser.py`。

```powershell
& Demo/.venv/Scripts/python.exe -c "import mimetypes,runpy; mimetypes.add_type('text/javascript','.js'); runpy.run_module('http.server',run_name='__main__')" 8766 --bind 127.0.0.1 --directory Demo/src/web
```

本机 Python 3.13、OCP 8.0.1.1（OCCT 8.0.1）、Edge 157.0.4341.60。直接使用 Windows 静态服务器的首次浏览器检查因注册表返回 `text/plain` 被拒绝，改用 `text/javascript` 后复跑通过。读取使用 [OCCT STEPControl_Reader](https://occt3d.com/dev/doc/refman/html/class_s_t_e_p_control___reader.html)，从 OCCT 网格写 GLB 2.0，由 [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html) 验证。

| 样本 | STEP 内的单位 | BRep 检查 | 三角形 | 解析、离散、GLB 和采样 |
| --- | --- | --- | ---: | ---: |
| DEV-BASE | millimetre | 有效闭合单实体 | 1296 | 0.063 s |
| DEV-POSE-INCH | INCH | 有效闭合单实体 | 1296 | 0.059 s |
| DEV-HOLE | millimetre | 有效闭合单实体 | 1296 | 0.059 s |
| DEV-SYMMETRIC | millimetre | 有效闭合单实体 | 652 | 0.031 s |
| DEV-SYMMETRIC-POSE | millimetre | 有效闭合单实体 | 652 | 0.030 s |

| 检查 | 样本组合与实测 |
| --- | --- |
| 同件变姿态 | DEV-BASE → DEV-POSE-INCH，62° 旋转和 (83, −37, 109) mm 平移。24 初值、19 去重姿态、1 个合格，7.261 s。两方向最大 RMS 为 1.18×10⁻¹¹ mm。GLTFLoader 加载后的三个非共线点重合误差 0.00000413、0.00000583、0.00000287 mm；查询件和旧件已知 130 mm 边长各为 130.00000035、129.99999523 mm。 |
| 孔位改动 | DEV-BASE → DEV-HOLE，孔中心移动 6 mm。24 初值、18 去重姿态、1 个合格，5.612 s。孔区旧→新 718 点、最大 5.995256383362113 mm，新→旧 757 点、最大 5.989748610876230 mm。页面显示 5.995 mm，自动验收按页面未舍入值与同一份采样逐方向核对，落在预登记的 5.5–6.3 mm 内。固定俯视定位及模型轮换通过浏览器射线检查和截图。 |
| 对齐歧义 | DEV-SYMMETRIC → DEV-SYMMETRIC-POSE。24 初值、17 去重姿态、4 个合格，3.847 s。合格解之间出现 180° 旋转差。去重 0.25° / 0.12 mm，歧义 6° / 0.85 mm。浏览器进入待复核且叠加不可用。 |

证据为 [后端实测 JSON](../src/web/development/results.json)、[孔区双向采样](../src/web/development/hole-samples.npz)、[Edge 检查 JSON](development-browser.json)及本目录下的实测截图。参考点仅在对齐结果返回后核验。第 1 项通过，进入历史库和查询界面联调。客户文件兼容性和全库耗时待验证。
