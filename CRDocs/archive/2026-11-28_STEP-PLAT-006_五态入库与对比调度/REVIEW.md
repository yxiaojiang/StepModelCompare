# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核与整改交接。[归档与交接规则](../../README.md)

## 第 1 轮独立审核 — STEP-PLAT-006（2026-11-28）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，1 个 P2 非阻断建议；`PLAT-005-P2-1` 核销为 `RESOLVED`）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `6f88188`，覆盖工作树全部改动与新增文件（`alembic/versions/0003_compare_source.py`、`app/uploads.py`、`app/workflow_api.py`、`app/queue_runtime.py`、`app/repository/{model_intake,rules,compare_history,quota}.py`、`app/compare/trial_job.py`、`app/db/{models,queue}.py`、`app/{api,config,main}.py`、`requirements.txt`、`tests/test_workflow_api.py`）。审核者未修改任何业务代码。

### 重点复核结论

1. **五态流转与同企 MD5 去重（通过）**
   - `model_intake.py:upload_scope` 严格收口权限：试用账号返回 403；企业用户仅能入库本企业且禁止直传（403）；系统管理员直传须显式指定 `enterprise_id`。
   - `model_intake.py:_admit` 持 `Enterprise FOR UPDATE` 行锁串行化同企活跃态（`PENDING`/`INDEXING`/`APPROVED`）MD5 去重、存储配额统计与台账写入；并发重复上传仅建 1 条台账并清理重复暂存文件；历史 `REJECTED`/`PARSE_FAILED` 允许同企重提并附带 `previous_status`/`previous_reason`；跨企业同件各自落盘与建特征缓存。
   - `review_model` 原子更新 `PENDING → INDEXING / REJECTED` 并写 `AuditLog`，驳回即时清理原件；常驻 `_index_one` 经 `FOR UPDATE SKIP LOCKED` 领取待索引件并由 `build_model_features` 终结为 `APPROVED` 或 `PARSE_FAILED`（失败删原件）。
2. **规则激活与任务快照（通过）**
   - `rules.py:save_rule` 写入不可变规则版本并记 `RULE_CREATED` 审计；`activate_rule` 在 PostgreSQL 持事务咨询锁 `pg_advisory_xact_lock(781_340_022)` 原子切换同作用域生效指针并写 `RULE_ACTIVATED` 审计（全局作用域 `enterprise_id IS NULL` 与企业覆盖各自受数据库部分唯一索引约束）。
   - `active_rule_snapshot` 按 `ORDER BY enterprise_id IS NULL` 优先企业覆盖、缺省回退全局，并合并 `feature_catalog.active_feature_version`，缺失返回 503；`enqueue_compare` 将规则、版本、特征版本、身份快照与 `top_n` 冻结进 `compare_jobs.rule_version_snapshot`，入队后切换全局规则不影响在途任务。
3. **企业和试用的 200／202 与轮询隔离（通过）**
   - Web `/api/v1/compare/upload` 默认 `async` 返回 202 + `Location` + `Retry-After: 1` + `Cache-Control: private, no-store`，支持显式 `hybrid`；客户端 `/api/v1/client/compare` 默认 `hybrid`，窗口内完成返 200、超时返 202，损坏件经后台失败回滚配额后返 422 `INVALID_STEP_FILE`。
   - `compare_history.py:_owned_job` 强制校验 `user_id + source + 实时用户状态/auth_version + 角色/企业快照一致`，跨账号与跨 Web/客户端入口轮询均返回 404；结果查询与选中写入均复用 `apply_visibility_scope` 与 `Model.status == 'APPROVED'`，公开演示撤回后即时不可见；选中统计按 `is_selected IS FALSE` 幂等累计。
4. **双入口 10 路共享配额（通过）**
   - `quota.py:enqueue_compare` 在 PostgreSQL `READ COMMITTED` 下持统一队列咨询锁（`_QUEUE_ADMISSION_LOCK = 781_340_021`），企业、管理员与试用共享 `max_queue_depth`（满额返回 503 `QUEUE_FULL`）；试用账号同事务执行 `used_count + in_flight_count < 5` CAS 预占并写 `TrialReservation`。
   - 独立复跑双入口 10 路 HTTP 并发（`/api/v1/compare/upload` 与 `/api/v1/client/compare` 交替）实测精确返回 5 个 202 与 5 个 403（`TRIAL_QUOTA_EXCEEDED`），数据库 `used_count=0, in_flight_count=5`。
5. **常驻消费、定时回收与迟到零落库（通过）**
   - `queue_runtime.py:queue_workers` 随 FastAPI lifespan 启停 `compare`/`index`/`reaper` 三条常驻循环与共享 `OcctPool`；对比与索引领取均使用 PostgreSQL `FOR UPDATE SKIP LOCKED`（`stored_only=True` 仅领含存储键任务）。
   - `reap_queue` 按数据库时钟 `clock_timestamp()` 依次回收过期试用预占、排队超时（`QUEUE_WAIT_TIMEOUT`）、运行超时（`COMPARE_TIMEOUT`）与中断索引（`INDEX_WORKER_TIMEOUT` 并删原件）。
   - 全链路锁序严格一致（`CompareJob FOR UPDATE → TrialReservation → Model FOR SHARE (ORDER BY id) → User`），无死锁环；事务及 ASGI 回归证实过期回收与迟到发布竞争均零写入 `compare_results`、`used_count` 不增、`in_flight_count` 精确归零。
6. **`PLAT-005-P2-1` 整改复核（`RESOLVED`）**
   - `POST /api/v1/auth/bind-invite` 已接入独立 `invite_limiter`（IP + `uid` 双维度，超限返 429）。
   - 既有列表（`/admin/enterprises`、`/admin/invite-codes`、`/models`）及新增列表/路径参数均在数据库查询前校验 int64 上界（`2**63 - 1`），越界经 `_invalid_request` 固定返回 `422 {"error_code": "INVALID_REQUEST"}`。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议，后续切片或运维期跟进）**：
  1. **`PLAT-006-P2-1`（OPEN｜管理员直传审计、表单鉴权前置防大包与 Starlette 私有属性兼容）**
     - **位置**：`Platform/backend/app/repository/model_intake.py:121-130`、`Platform/backend/app/workflow_api.py:78-90`、`Platform/backend/app/uploads.py:63-67`
     - **说明**：① 管理员直传（`POST /api/v1/models/batch-upload`）已写 `reviewer_id`/`reviewed_at` 但未落 `AuditLog`，后续管理后台审计页接入时可补记；② 无 `Authorization` Header 走表单 `username`/`password` 鉴权时，未认证请求在验密前最多可流式上传 `max_upload_request_bytes`（默认 256 MB），上线前需在反向代理层收紧未认证上传限流/超时（随 `PLAT-DEF-003` 跟进）；③ `uploads.py:65` 异常清理依赖 Starlette 私有属性 `parser._files_to_close_on_error`（当前 Starlette 1.10.0 实测存在），升级依赖时需回归校验。
- **已关闭问题**：
  - **`PLAT-005-P2-1`（`RESOLVED`）**：邀请码独立限流 429 与分页游标 int64 越界固定 422 已独立复测通过。

### 独立验证结果与证据分级

审核者独立复跑以下验证（Windows，Python 3.13.14，Starlette 1.10.0，本机临时 PostgreSQL 18.7 端口 `56435`，测毕即停库清理）：

- **本机真实 PostgreSQL 18.7 + ASGI 全量测试（64/64 通过，107.180s）**：含 16 项真实 PostgreSQL 用例（`test_workflow_api.py` 5 项 + `test_database.py` 11 项），完整复测五态入库、同企并发 MD5 去重、跨企隔离、规则覆盖与任务快照、200/202 混合响应与损坏件 422、轮询越权与公开撤回、选中幂等、双入口 10 路并发 5/5 门禁、混合身份队列满 503、定时回收与中断索引清理、迟到零落库，以及 `PLAT-005-P2-1` 429/422 回归；Alembic 升降级与 `check` 无元数据漂移。
- **工程门禁证据**：`pip check`、`compileall`、`git diff --check 6f88188`、`git diff --exit-code 1e0fc76 -- Demo` 全过；手写源码最大 560 行（`tests/test_database.py`），生产文件最大 318 行（`app/compare/scoring.py`），`workflow_api.py` 313 行、`quota.py` 282 行，均远低于 1000/1500 行门禁，中文注释规范合规。
- **未实测边界**：客户 20–50 件真实 STEP 样本实测、目标部署环境 HTTPS/可信代理/多进程限流、存储卷查询原件保留与孤立文件清理、以及可视化管理后台与公开注册防刷，继续由 `DEFERRED.md`（`PLAT-DEF-001/002/003`、`PLAT-005-P2-2`、`PLAT-006-P2-1`）跟踪。
