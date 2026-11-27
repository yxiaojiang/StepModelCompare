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
- `STEP-PLAT-002` 已独立审核通过。查询树复用、双层共用截止值与进程回收已复测，两项 P2 均关闭。证据与环境边界见 [审核归档](../../CRDocs/archive/2026-11-27_STEP-PLAT-002_任务级对比Worker与P2收口/INDEX.md)。阶段 2.2 待开发。

## 待验证事项（阶段 1 冻结）
- 客户 20–50 件真实 `.stp` 的协议/PMI/材料/闭合性分布，以及单件 OCCT 与单次 `igl` ICP 实测耗时；
- 前端框架（Vue 3 / React）、唯一首期部署目标（Linux Docker / Windows Server）、单方缺失默认策略、审核角色粒度、注册防刷门槛、规则作用域、预登记正例集（20–30 组）及找回率/P95 门槛。

## 入口文件
- [当前代码审核状态](../../CRDocs/STATUS.md)
- [详细开发设计文档](../../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md)
- [详细开发计划](../../Docs/STEP模型相似度对比与检索系统详细开发计划.md) · [当前待推进](../Plan.md)
- [STEP-SIM-005 归档](../../PlanDocs/STEP-SIM-005/STATUS.md) · [STEP-SIM-004 归档](../../PlanDocs/STEP-SIM-004/STATUS.md)
- [相似度标准说明](../../Docs/STEP模型相似度评判逻辑与判断标准说明.md) · [开发报价单](../../Docs/STEP模型相似度对比与检索系统开发报价单.xlsx)
