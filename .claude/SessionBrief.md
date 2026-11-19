# 当前项目状态

## 项目目标
面向客户已有的千件级 STEP 历史库，判断某个新设计是否可由满足本次工程要求的已发布旧件修订直接替代。几何相似度负责缩小和排序候选，不直接等同于可替代；最终建议必须有几何、功能接口、工况、版本和成熟资料证据，并由工程负责人确认。

## 当前已定口径
- **近期交付。** STEP-SIM-003 Demo 已更新为简体中文 Windows Studio 0.3.1。候选和待复核模型可点击分数旁的「详情」查看六项误差、得分及标准。源码集中于 `Demo/src`，`Demo/Tools` 提供编译和打包入口。基础件从 `Data/Base` 读取，完成查询写入 `Data/History`。
- **Demo 界面调整。** 已按用户截图修改 Studio 源码：模型库隐藏修订/来源列并增加打开 `Data/Base` 的按钮，主界面移除样例来源和修订标签，评分详情窗口扩大，比对进度条增加绿色滚动效果。当前 0.3.1 安装包尚未包含这些改动；源码变更尚未构建和复核。
- **正式项目。** STEP-SIM-002 仍是业务与试点基线，STEP-SIM-001 的字段复用主目标已被部分替代。正式采用须核对本次需求、几何与接口、工况、确切修订和成熟资料，并由工程负责人确认。正式方案及汇报材料位于 Docs/。
- **讨论状态。** PlanDocs 已收口至 IDLE，最近采纳决定为 STEP-SIM-003。

## 未决前提
- **Demo。** 客户 STEP 尚未提供，客户文件兼容性、现场响应和整机断网复跑待验证。缺少 WebView2 的目标机补装流程待验证。
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

先用更新后的 Demo 源码构建并复核 Studio 界面，再按 [当前计划](Plan.md)在演示现场复核环境。收到客户资料后，分别验证真实 STEP 的 Demo 链路和正式试点的阶段 1 前提。
