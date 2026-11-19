# 冻结版本断网彩排

2026-11-18 在本机 Windows x64、Python 3.13.14、OCP 8.0.1.1 和 Edge 157.0.4341.60 上实跑。全部文件均为「演示样例」。

采用 [freeze-v2.json](freeze-v2.json) 所列 SHA-256，程序快照 `30a15c96e064dfb9602b7a7d3e421e096b67c3ccd63a7c11ad201f812f3543f7`，37 件库、K=5。冻结清单和校验涵盖 SQLite、历史 STEP、[预登记查询](../samples/rehearsal/cases.json)、程序、参数、前端本地依赖。拟合门槛为两方向分别 RMS ≤ 0.95 mm 且 P95 ≤ 0.65 mm。去重 0.25° / 0.12 mm，歧义 6° / 0.85 mm，孔区最低采样数量为双向各 35 点。

## 复跑命令

工作目录为项目根目录。先验证冻结快照并在一个终端启动服务。

```powershell
& Demo/.venv/Scripts/python.exe Demo/freeze.py Demo/evidence/freeze-v2.json --verify
& Demo/.venv/Scripts/python.exe -X utf8 Demo/loopback_server.py --port 8765
```

在另一个 PowerShell 启动 Edge 普通上传流程的核验脚本。复跑前归档本轮截图，并指定独立报告路径。

```powershell
& Demo/.venv/Scripts/python.exe -X utf8 Demo/rehearse.py --report Demo/evidence/rehearsal-rerun.json
& Demo/.venv/Scripts/python.exe Demo/freeze.py Demo/evidence/freeze-v2.json --verify
```

本次报告为 [rehearsal.json](rehearsal.json)。彩排前禁止 Edge 外连、保留 127.0.0.1，外网探针返回 `ERR_PROXY_CONNECTION_FAILED`。Python 服务用 socket 审计阻断非回环连接，启动的非回环 TCP 探针被拒绝。页面记录 229 次回环请求和 0 次外部请求。验证范围为本机 Demo 进程的网络隔离，整机网卡断开仍待现场验证。[首次启动记录](rehearsal-attempt-1.md)

## 普通上传实测

| 样例 | 预登记目标与结果 | 上传到页面结果 | 选中目标到视图可操作 |
| --- | --- | ---: | ---: |
| SHOW-POSE-INCH.step | SHOW-P01 粗排 1、页面第 1，共 4 个几何候选 | 18.216 s | 0.113 s |
| SHOW-HOLE.step | SHOW-H01 粗排 1、页面第 1，共 2 个几何候选 | 14.593 s | 0.113 s |
| SHOW-SYMMETRIC.step | SHOW-S01 进入待复核，0 个可靠几何候选 | 15.165 s | 0.121 s |
| SHOW-DAMAGED.stp | 解析失败，清空上轮候选和视图，显示 OCCT 无法读取 STEP | 0.028 s |  |
| SHOW-POSE-RETRY.stp | 损坏文件之后恢复，SHOW-P01 回到第 1 | 19.245 s | 0.112 s |

- **找回。** 两个事先登记前五目标的查询均找回，2/2。粗筛漏出、精排掉出、目标对齐失败、参考点错配均为 0。歧义和故障注入各走预登记分支，复传为恢复检查。成功的上传都经过解析中、比对中、完成。历史库导入 37/37。
- **同件坐标和尺度。** 三个非共线参考点在 Edge 的 GLB 世界坐标下重合误差为 0.00000346、0.00000627、0.00000243 mm，已知 152 mm 长度测得 152.00000259 和 151.99999511 mm。验证了英寸 STEP、旋转和平移、浏览器轴向及米制场景换算。通过联动旋转、缩放、透明度和切换候选检查。[同件叠加截图](rehearsal-pose.png)
- **孔区。** 通孔已知沿 +X 平移 5 mm。固定视角下的射线检查在新旧两个孔的非交叠区分别命中实面和孔，模型轮换可见孔位变化。旧→新 736 点、最大 4.996046279669968 mm，新→旧 737 点、最大 4.956868155105195 mm。页面取两方向最大值显示 4.996 mm。彩排从同一次保存的 [双向采样](rehearsal-hole-samples.npz)复算，页面未舍入值完全一致，落在预登记的 4.5–5.4 mm 范围内。[查询件](rehearsal-hole-new.png) · [历史件](rehearsal-hole-old.png)
- **歧义。** 24 初值、17 个去重姿态、5 个拟合合格姿态，至少一对的相对旋转为 180°。两视图可操作，叠加不可用。其他未达到拟合条件的候选也进入待复核。[待复核截图](rehearsal-ambiguity.png)

冻结快照彩排后重新校验一致。程序、库和启动说明现可交付。结果覆盖本批演示样例，待真实客户 STEP 到位后验证格式兼容性与耗时。工程采用由工程负责人按 STEP-SIM-002 确认。
