# 当前项目状态

## 项目目标
面向客户已有的千件级 STEP 历史库，判断某个新设计是否可由满足本次工程要求的已发布旧件修订直接替代。几何相似度负责缩小和排序候选，不直接等同于可替代；最终建议必须有几何、功能接口、工况、版本和成熟资料证据，并由工程负责人确认。

## 当前已定口径
- **近期交付。** STEP-SIM-003 Demo 已更新为简体中文 Windows Studio 0.3.1。候选和待复核模型可点击分数旁的「详情」查看六项误差、得分及标准。源码集中于 `Demo/src`，`Demo/Tools` 提供编译和打包入口。基础件从 `Data/Base` 读取，完成查询写入 `Data/History`。当前工作分支为 `test1`（相对 Demo 合并点 `d626c196bb3de4d45ca994ac261ba418e027004e`，含安装 `Data/test` 种子与比对进度修复）。PR #1 已合并进 Demo；旧分支 `cursor/result-list-layout-e93d` 已删除，不要恢复。
- **安装包中的 Data/test。** 用户已确认用先前安装包全新安装后，`Data/test` 存在且可用。种子来自已入库的 `Demo/samples/rehearsal/queries` 的 5 个 STEP（与桌面 queries 哈希一致），不作为 Base 自动导入，也不写入 History。升级时已有同名文件不覆盖（Inno `onlyifdoesntexist`）。Compile 只向缺失的 `Data/test` 文件补种，不覆盖已有 Data，也不把运行中的 History/.cache 打进安装包。
- **SHOW-HOLE 比对失败已修，待用户用新包确认。** 用户实测先前安装包（2026-11-19 17:09）时，用 `Data/test/SHOW-HOLE.step` 比对，界面显示「比对失败 0%」和 JSON 解析错误。只读日志显示后台比对已完成并写入 History，但轮询 `GET /api/queries/{id}` 因 `AttributeError: 'StudioData' object has no attribute 'query_directory'` 返回纯文本 500。该方法在 `14d1150` 重写 `studio_data.py` 时被漏掉。源码已恢复 `query_directory`；未处理异常改为 JSON 错误（不泄露路径或堆栈）；前端对非 JSON / 非 2xx 显示可理解错误。临时 Data 上真实上传 SHOW-HOLE 并轮询至完成的 HTTP 测试已通过。含此修复的安装包为 `Demo/release/installer/StepModelCompare-Studio-Setup-0.3.1-x64.exe`（0.3.1，2026-11-19 17:24:23，Asia/Singapore UTC+8）。用户尚未明确确认安装该新包后比对通过，不能写成已实机验收。`SHOW-DAMAGED.stp` 仍是故意损坏样例，不应要求比对成功。
- **正式项目。** STEP-SIM-002 仍是业务与试点基线。正式方案及汇报材料位于 Docs/。
- **讨论状态。** PlanDocs 已收口至 IDLE，最近采纳决定为 STEP-SIM-003。

## 未决前提
- **Demo。** 客户 STEP 尚未提供，现场响应和整机断网复跑待验证。缺少 WebView2 的目标机补装流程待验证。修复后的比对与先前界面改动，待用户关闭当前 Studio 并安装 17:24 的新包后确认。
- **正式试点。** 待确认需求单真实试填、设计时点证据和盲测正例规模，以及预计建议率、可接受误推荐风险上界、待核定和待复核合计人工核查上限。

## 最近交付索引
- [当前计划](Plan.md) · [Demo 模块记忆](contexts/07-customer-demo.md)
- [Demo 启动与操作](../Demo/README.md) · [冻结彩排](../Demo/evidence/03-rehearsal.md)
- [Studio 中文安装与评分验证](../Demo/evidence/05-studio-similarity.md)
- [Studio 评分详情核验](../Demo/evidence/06-score-details.md)
- [GitHub Demo 分支](https://github.com/yxiaojiang/StepModelCompare/tree/Demo)
- [决定记录](../PlanDocs/DECISIONS.md) · [Demo 定稿与审核](../PlanDocs/STEP-SIM-003/STATUS.md)
- [正式实施方案](../Docs/STEP模型相似检索与历史数据复用实施方案.md) · [正式客户汇报](../Docs/STEP模型检索与历史数据复用_客户汇报.pptx)

## 下一步

请用户自行关闭正在运行的 Studio 后，安装 `Demo/release/installer/StepModelCompare-Studio-Setup-0.3.1-x64.exe`（2026-11-19 17:24:23），再用 `Data/test` 中的 SHOW-HOLE 等正常模型复核比对。不要把尚未确认的比对或视觉写成已通过。再按 [当前计划](Plan.md) 做现场环境复核。
