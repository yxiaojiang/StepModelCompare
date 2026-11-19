# 当前项目状态

## 项目目标
面向客户已有的千件级 STEP 历史库，判断某个新设计是否可由满足本次工程要求的已发布旧件修订直接替代。几何相似度负责缩小和排序候选，不直接等同于可替代；最终建议必须有几何、功能接口、工况、版本和成熟资料证据，并由工程负责人确认。

## 当前已定口径
- **近期交付。** STEP-SIM-003 Demo 已更新为简体中文 Windows Studio 0.3.1。候选和待复核模型可点击分数旁的「详情」查看六项误差、得分及标准。源码集中于 `Demo/src`，`Demo/Tools` 提供编译和打包入口。基础件从 `Data/Base` 读取，完成查询写入 `Data/History`。
- **Demo 源码待编译进安装包。** 0.3.1 安装包之后的源码还有未打包改动，均未做 Windows Studio / WebView2 实机视觉复核，也未由用户确认为通过：
  - 结果列表为两行卡片（名称、相似度、必要的「待复核」）；主轴尺寸差、双向 RMS 和对齐原因只在「详情」中查看。Linux Chrome 曾核对约 310px 侧栏，不是 Studio 安装版复核。
  - 四处界面调整：打开「模型库文件夹」成功后不再提示（失败仍提示）；展开的「待复核」标题间距与「几何候选」一致；主界面去掉底部几何比对用途说明（口径仍写在操作说明中）；「相似度评分详情」约缩小 20%，去掉末段表面/主轴误差计算说明，保留按比例扣分说明。评分、阈值和分组未改。
  - Compile.bat 先在 `Demo/release/bundle-staging` 生成程序，再只更新 `bundle/Studio` 中的程序文件，完整保留已有 `Data` 且不覆盖其中文件；首次无 `Data` 时由 Studio 启动初始化。该逻辑已用独立临时目录测试验证，未跑会替换当前 exe 的完整编译。
  - 「基础模型库」仅在首次实际建库，或 `Data/Base` 发生增删、同名修改，或 `Data/.cache/library-manifest.json` / SQLite 索引不可安全复用时，才弹出与「正在比对 STEP」同结构的简洁加载框（标题、真实百分比或扫描中的 `--`、当前文件、绿色条纹进度条），不含多余状态计数。无变化时（含关闭重开和程序重启）对照清单与实际目录的相对路径、大小、`mtime_ns` 及现有索引直接列出，不调用 `refresh_base`，也不闪加载框。清单仅在刷新成功后原子写入；失败不标记成功。JSON 损坏但索引与目录仍一致时，重写清单并复用，不重复解析。已用临时 Data 完成 22 项进度单元测试和 7 项 HTTP/前端显示条件测试；本轮未做浏览器或 Studio 窗口视觉复核。当前 bundle 早于这些改动，需再次 Compile.bat 后才能在 Studio 中看到。
- **正式项目。** STEP-SIM-002 仍是业务与试点基线，STEP-SIM-001 的字段复用主目标已被部分替代。正式采用须核对本次需求、几何与接口、工况、确切修订和成熟资料，并由工程负责人确认。正式方案及汇报材料位于 Docs/。
- **讨论状态。** PlanDocs 已收口至 IDLE，最近采纳决定为 STEP-SIM-003。

## 未决前提
- **Demo。** 客户 STEP 尚未提供，客户文件兼容性、现场响应和整机断网复跑待验证。缺少 WebView2 的目标机补装流程待验证。当前源码改动待再编译后由用户在 Studio 中视觉确认。
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

先用当前源码在 Windows 上再编译 Studio，请用户复核界面与模型库加载行为并确认已有 Data 被保留；再按 [当前计划](Plan.md) 做现场环境复核。收到客户资料后，分别验证真实 STEP 的 Demo 链路和正式试点的阶段 1 前提。
