# 当前待推进工作（1–3 项）

完整四阶段计划见 [详细开发计划](../Docs/STEP模型相似度对比与检索系统详细开发计划.md)。`STEP-SIM-004` 与 `STEP-SIM-005` 均已采纳归档，`PlanDocs` 已回 `IDLE`，当前在 `feat/platform-v1` 分支（`Demo/` 保持冻结不变）。

1. **开发第一步：搭建 `Platform/backend/` 基础骨架、数据表迁移与 PostgreSQL `SKIP LOCKED` 队列**（下一步立即执行）
   - 先确认前端框架（Vue 3 / React 二选一）与唯一首期部署目标（Linux Docker / Windows Server 二选一）；
   - 创建 `Platform/backend/` FastAPI + SQLAlchemy 2.0 + Alembic + `config.py`（所有待冻结参数配置化）+ 完整数据表（含 `users.auth_version`、`trial_reservations`、`model_features`、`compare_jobs`、`compare_results` 等）+ `FOR UPDATE SKIP LOCKED` 任务队列及常驻受控 OCCT Worker 进程池骨架，配齐基础健康检查与单测。
2. **阶段 1 客户样本盘点脚本与参数冻结**（与第一步并行）
   - 基于 `Demo/src/geometry.py` 编写独立样本盘点脚本，待客户 20–50 件真实 `.stp` 到位后实测协议/PMI/材料/闭合性/单次 `igl` ICP 耗时，冻结 `K`、`compare_job_timeout`、`reaper_ttl`、`sync_wait_timeout`、预登记正例集及验收门槛。
3. **对客报价单版式复查与 Demo 遗留确认**
   - 在桌面 Excel 预览确认 [报价单](../Docs/STEP模型相似度对比与检索系统开发报价单.xlsx) 版式后发给客户；Demo 0.3.1 的 17:24 修复包如仍需演示，待实机确认。
