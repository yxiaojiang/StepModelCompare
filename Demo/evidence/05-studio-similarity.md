# Studio 0.3.0 中文安装与评分核验

2026-11-19，Windows x64。Python、网页资源、配置和安装器定义集中在 `Demo/src`。现行入口为两个纯英文批处理脚本。

```powershell
& Demo/Tools/Compile.bat
& Demo/Tools/Package.bat
```

已分别执行编译与打包。打包从项目目录外启动成功，生成 `Demo/release/installer/StepModelCompare-Studio-Setup-0.3.0-x64.exe`。文件大小 308,178,090 字节，SHA-256 为 `F9C6FF2C81DBADA8E90392CF469836C0AE7956801E1D20F9D346987E6E2B473C`。

使用 Inno Setup 6.7.3 的简体中文资源，核对实际向导的欢迎、附加任务、准备安装、完成页及应用自定义文案。选装演示模型后，中文测试路径中生成 Studio.exe、Base 中的 37 件模型和 History 目录。[中文安装向导](studio-installer-zh.png) · [安装完成页](studio-installer-zh-finish.png)

## 评分

评分方法和分项扣减尺度见 [几何相似度](../README.md#几何相似度)。对照普通上传、API、保存的查询结果逐项检查每个候选与待复核模型的显示和总分，分数及子项均在定义范围内。

| 样例 | 页面结果 |
| --- | --- |
| 英寸 STEP 与同件匹配 | SHOW-P01 第 1，100.0 分 |
| 安装板孔位改动 | SHOW-H01 第 1，81.6 分，孔区读数仍为 4.996 mm |
| 同形对称件 | SHOW-S01 100.0 分，姿态歧义，待复核且仅并排 |
| 损坏 STEP 和随后重试 | 失败提示明确，后续有效查询恢复并正常评分 |

浏览器回归从 `Demo/src/loopback_server.py` 启动，使用 `Demo/src/rehearse.py` 对冻结演示库复跑。已有查询兼容测试从旧版结果读取五条评分，历史 JSON 哈希保持一致。[评分回归数据](score-rehearsal.json)

另从安装后的 Studio.exe 复跑了孔位、损坏文件与中文子目录基础模型。已核对软件界面、API 与 History JSON 中每条评分一致。中文基础件对自身评分为 100.0，关闭 Studio 后本机服务正常退出。[安装版评分截图](studio-score.png) · [源码与参数冻结清单](freeze-v3-studio.json)

中文向导与评分已在当前机器验证。评分权重用于 Demo，真实客户件的分数解释及目标机缺少 WebView2 时的补装流程待验证。
