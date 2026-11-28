# 当前项目状态

## 项目目标
当前正式开发主线为 `STEP-SIM-004` + `STEP-SIM-005`（均已 ADOPTED，单人独立开发）：在 `Platform/` 下交付支持多企业数据隔离、可配置 `D1–D7` 多维相似度规则、五态人工确认防刷入库、Web 三维图形化对比检查及客户端混合同步双 API 的 STEP 模型相似度对比与检索服务平台。相似度分数仅用于检索排序与差异定位，不单独等同于工程直接替代放行。

## 当前已定口径
- **讨论已全部收口**：`STEP-SIM-004` 与 `STEP-SIM-005` 均已 `ADOPTED` 归档，`PlanDocs/STATUS.md` 为 `IDLE`。
- **正式开发基线（v1.1）**：[详细开发设计文档](../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md) + [详细开发计划](../Docs/STEP模型相似度对比与检索系统详细开发计划.md) + [当前待推进](Plan.md)。
- **已锁定的核心架构与代码逻辑（STEP-SIM-005）**：
  - 几何依赖：`cadquery-ocp-novtk`（8.0.1+，导入名 `OCP`）+ `numpy` + `scipy` + `libigl`（导入名 `igl`），移除 `pythonocc-core` 与 `trimesh`。
  - 两层进程隔离 + 轻量队列：OCCT 解析/XDE/GLB 走常驻受控子进程池（硬超时强杀重建 + 每 $N$ 任务轮换防 C++ 泄漏）；至多 $K$ 次 ICP 在任务级 Worker 进程内复用查询件 `igl.AABB` 树顺序执行（受 `compare_job_timeout` 管控）；队列直接用 PostgreSQL `FOR UPDATE SKIP LOCKED`（不引入 Redis/Celery）。
  - 并发配额与迟到拦截：`reaper_ttl > queue_wait_timeout + compare_job_timeout + 30s`；结果落库前同事务 CAS 更新 `trial_reservations` `RESERVED → COMMITTED`，失败则严禁落库/返回结果并置 `FAILED (QUOTA_EXPIRED)`。
  - 鉴权缓存与即时失效：`users.auth_version` + 有界 LRU/TTL 内存缓存（仅缓成功项，键 `HMAC-SHA256(pepper, uid:auth_version:pwd)`）+ 改密/禁用/锁定/改权递增 `auth_version` + 未命中前 IP+账号双限流。
  - 版本化特征：`model_features(model_id, feature_version, ...)` + `active_feature_version` 全局指针，升级全库就绪并增量补齐后再原子切换；首期不跨企业共享底层几何缓存。
- **Git 现状**：当前在 `feat/platform-v1` 分支，`Demo/` 与标签 `demo-v0.3.1` 冻结保留。
- **代码交接**：阶段 2 里程碑 `ecc9474`，阶段 3 首切片 `STEP-PLAT-005` 里程碑 `6f88188`。`STEP-PLAT-006` 独立审核通过并归档，覆盖五态入库、规则快照、企业/试用双入口对比与常驻调度。独立复测 64 项通过，含 16 项本机 PostgreSQL 用例。见 [STEP-PLAT-006 归档](../CRDocs/archive/2026-11-28_STEP-PLAT-006_五态入库与对比调度/INDEX.md) · [活动审核状态](../CRDocs/STATUS.md) · [有效延期项](../CRDocs/DEFERRED.md)。

## 未决前提（阶段 1 签认冻结）
- 客户 20–50 件真实 `.stp` 样本盘点（协议、语义 PMI 可读率、材料填写率、闭合/开壳/多实体占比、单件 OCCT 与单次 `igl` ICP 耗时）。
- 前端框架（Vue 3 / React）与唯一首期部署目标（Linux Docker / Windows Server）二选一；单方缺失默认策略、审核角色粒度、注册防刷门槛、公共演示库来源、规则作用域、预登记正例集（20–30 组）及找回率/P95 门槛。
- 报价单发客前桌面 Excel 预览分页；Demo 17:24 修复包如仍需演示待实机确认。

## 最近交付索引
- [当前待推进](Plan.md) · [详细开发计划](../Docs/STEP模型相似度对比与检索系统详细开发计划.md) · [详细开发设计文档](../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md)
- [多企业平台模块记忆](contexts/08-similarity-platform.md) · [Demo 模块记忆](contexts/07-customer-demo.md)
- [决定记录](../PlanDocs/DECISIONS.md) · [STEP-SIM-005 归档](../PlanDocs/STEP-SIM-005/STATUS.md) · [STEP-SIM-004 归档](../PlanDocs/STEP-SIM-004/STATUS.md)

## 下一步
建议在前端框架签认后优先完成 Web 三维对比最小闭环：上传与轮询、双视口、门禁后叠加、待复核分流和选中回传。可视化管理后台、公开注册防刷仍待推进。客户真实样本、部署目标、可信代理与存储策略继续并行签认。
