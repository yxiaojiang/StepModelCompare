# 模块上下文：多企业 STEP 相似度对比与检索平台（STEP-SIM-004 / 005）

## 模块职责
面向多企业服务端部署的正式 STEP 相似度对比与检索系统（单人独立开发，目录 `Platform/`），整合：
1. 可配置 `D1–D7` 多维特征引擎 + 独立安全门禁 + 动态有效权重上界剪枝 + `model_features` 版本隔离；
2. 注册登录（HMAC+`auth_version` 短缓存）、企业与邀请码、无邀请码 5 次 CAS 并发安全限额、跨企业 SQL 强隔离、五态待审入库台账与规则配置后台；
3. Three.js 三维图形化对比工作台与客户端混合同步双 API（批量上传待审 + 单件 STP 对比）。

## 输入 / 输出
- **输入**：客户端或服务端上传的 `.stp`/`.step` 零件模型、企业/用户/邀请码凭证、服务端 `D1–D7` 百分比与缺项规则配置。
- **输出**：版本化 `model_features` 与 GLB 预览缓存、百分制综合相似度与分项有效权重得分拆解、「待复核」分流结果、6 项必记模型台账、Web 三维并排/双色叠加视图及客户端混合同步 JSON 响应（`200` 或 `202 + job_id`）。

## 关键工程约束（已采纳，实施不可违背）
- **真实依赖与两层隔离**：`cadquery-ocp-novtk`（导入名 `OCP`）+ `numpy` + `scipy` + `libigl`（导入名 `igl`），不使用 `trimesh`/Redis/Celery。OCCT 解析/XDE/GLB 导出走常驻受控子进程池（硬超时强杀重建 + 每 $N$ 任务轮换）；至多 $K$ 次 ICP 在任务级 Worker 内复用查询件 `igl.AABB` 树顺序执行（受 `compare_job_timeout` 管控）；任务队列用 PostgreSQL `FOR UPDATE SKIP LOCKED`。
- **维度、缺项与版本隔离**：`D1–D7` 可独立设百分比 + 左右手性/对称歧义独立门禁；严格区分双方缺失与单方缺失（显式标未核实，默认 0 分保守计分），$V(q,m)=\emptyset$ 禁除零转「特征不足/待复核」；上界剪枝按重分配有效权重 $W'_{\text{D5}}(q,m)$ 计算，区分 `exact_pruned` 与 `truncated_by_k`；特征按 `model_features(model_id, feature_version)` 隔离，升级全库就绪并增量补齐后原子切换 `active_feature_version`。
- **并发配额与安全**：`reaper_ttl > queue_wait_timeout + compare_job_timeout + 30s`；写结果前同事务 CAS 更新 `trial_reservations` `RESERVED → COMMITTED`，`rowcount == 0` 严禁落库/返回结果并置 `FAILED (QUOTA_EXPIRED)`；有界队列满额预占前直返 `503 QUEUE_FULL`；鉴权短缓存仅缓成功项（有界 LRU，键 `HMAC-SHA256(pepper, uid:auth_version:pwd)`），状态变更递增 `auth_version` 即时失效，未命中前叠加 IP+账号双限流；严禁 URL 传密；首期不跨企业共享底层几何缓存；Web 工作台固定显示工程边界提示。

## 已验证事实
- `Demo/.venv` 实装 `cadquery-ocp-novtk 8.0.1.1.0` + `numpy` + `scipy` + `libigl`，包含 `STEPCAFControl_Reader`、全部 `XCAFDoc_*Tool`、`BRepAdaptor_Surface` 及 `igl.AABB`。
- `Demo/src/alignment.py` 不调用 OCCT，纯基于 `numpy`/`scipy`/`igl` 运算；`Demo/src/` 已验证毫米统一、24 初值右手系 ICP 与 GLB 矩阵 $C_{\text{新}} T C_{\text{旧}}^{-1}$。
- `STEP-PLAT-001` 已通过独立审核，首版迁移和队列行锁已在本机 PostgreSQL 复测。证据见 [审核归档](../../CRDocs/archive/2026-11-27_STEP-PLAT-001_后端骨架与OCCT解析池/INDEX.md)。
- `STEP-PLAT-002` 已独立审核通过。查询树复用、双层共用截止值与进程回收已复测，两项 P2 均关闭。证据与环境边界见 [审核归档](../../CRDocs/archive/2026-11-27_STEP-PLAT-002_任务级对比Worker与P2收口/INDEX.md)。
- `STEP-PLAT-003` 对应阶段 2.2，已独立审核通过。OCCT 子进程提取毫米制 D1–D4、D6–D7，导出 D5 采样网格与 GLB。全局指针由 `feature_catalog` 单例持有，入库与切换通过 PostgreSQL 行锁互斥，版本重提取先全量后增量。真实提取版本为 `1.0.0`，第二版本只在事务用例中模拟。详见 [独立审核归档](../../CRDocs/archive/2026-11-27_STEP-PLAT-003_特征提取与版本化存储/INDEX.md)。

- `STEP-PLAT-004`（2.3/2.4）已独立通过。门禁与 D5 权重独立，只有过门禁的候选能形成可靠榜尾。未知 PMI 与不可区分的零公差在评分侧处理，原特征版本保持兼容。见 [独立审核归档](../../CRDocs/archive/2026-11-28_STEP-PLAT-004_D5精排与动态评分剪枝/INDEX.md)。
- `STEP-PLAT-005` 阶段 3 首切片已独立通过。Basic/JWT 回查账号版本，短缓存和双限流按进程生效，模型与试用查询共用范围守卫。见 [审核归档](../../CRDocs/archive/2026-11-28_STEP-PLAT-005_鉴权隔离与试用配额/INDEX.md)。
- `STEP-PLAT-006` 已独立审核通过。入库按企业锁定容量和活跃态 MD5 去重，管理员审核后由后台索引。规则企业覆盖优先，入队固定规则、特征版本和身份快照。Web 默认异步，客户端默认混合等待，两者共享队列和 5 次试用预占。轮询按账号、来源和实时身份授权，候选读取和选中继续施加可见性守卫；常驻调度处理对比、索引和超时回收。本机 PostgreSQL 18.7 与 ASGI 复测 64 项通过（16 项真实数据库用例，自制 STEP 夹具）。见 [独立审核归档](../../CRDocs/archive/2026-11-28_STEP-PLAT-006_五态入库与对比调度/INDEX.md)。

- `STEP-PLAT-007`（4.1）已独立审核通过。React 工作台已联通上传、轮询、候选与分项、同步双视口和选中回传。成功 Web 任务的查询件 GLB 随原件存放，候选按任务冻结的特征版本及实时可见性读取。仅通过门禁的右手系刚体矩阵可用于叠加。Windows 并发路径校验与 Worker 等待已复测。见 [独立审核归档](../../CRDocs/archive/2026-11-28_STEP-PLAT-007_React三维对比工作台/INDEX.md)。

- `STEP-PLAT-008` 可视化管理后台独立通过。五态台账、逐件审核和直传审计已接通，企业台账按登录身份限定本企业，检索和预览继续使用已入库状态守卫。真实 PostgreSQL 18.7 的四角色 ASGI 回归通过。见 [审核归档](../../CRDocs/archive/2026-11-28_STEP-PLAT-008_可视化管理后台/INDEX.md)。

## 待推进与待验证
- 当前实施 STEP-PLAT-009（公开注册开关、本地 SVG 验证码、`init-admin` CLI），独立审核后进入 STEP-PLAT-010（客户端 SDK、仿真部署与 Chrome/Edge 冒烟）。
- 表单上传代理限流和 Starlette 升级兼容由 `PLAT-006-P2-1` 跟踪。目标环境 PostgreSQL、HTTPS/可信代理、多进程限流、原件保留和孤立文件处置见 [延期项](../../CRDocs/DEFERRED.md)。
- 客户 20–50 件真实 `.stp` 的协议/PMI/材料/闭合性分布，以及单件 OCCT 与单次 `igl` ICP 实测耗时（不可由本地闭环替代）；
- 唯一首期部署目标（Linux Docker / Windows Server）、单方缺失默认策略、审核角色粒度、注册防刷门槛、规则作用域、预登记正例集（20–30 组）及找回率/P95 门槛。

## 入口文件
- [当前代码审核状态](../../CRDocs/STATUS.md)
- [STEP 平台操作帮助](../../Docs/STEP平台操作帮助.md)：面向测试人员的环境配置、启动顺序、工作台操作、管理员 API 与边界说明
- [详细开发设计文档](../../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md)
- [详细开发计划](../../Docs/STEP模型相似度对比与检索系统详细开发计划.md) · [当前待推进](../Plan.md)
- [STEP-SIM-005 归档](../../PlanDocs/STEP-SIM-005/STATUS.md) · [STEP-SIM-004 归档](../../PlanDocs/STEP-SIM-004/STATUS.md)
- [相似度标准说明](../../Docs/STEP模型相似度评判逻辑与判断标准说明.md) · [开发报价单](../../Docs/STEP模型相似度对比与检索系统开发报价单.xlsx)
