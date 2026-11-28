# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核与整改交接。[归档与交接规则](../../README.md) · [上一任务 STEP-PLAT-007 归档](../2026-11-28_STEP-PLAT-007_React三维对比工作台/REVIEW.md)

## 第 1 轮独立审核 — STEP-PLAT-008（2026-11-28）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，0 个新增 P2）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `c874ae5`，覆盖本轮全部已跟踪改动与 `Platform/` 下未跟踪新增源码（后端 `app/api.py`、`app/workflow_api.py`、`app/repository/model_intake.py`、`tests/test_workflow_api.py`；前端 `index.html`、`src/{main,Workbench,api,ModelLedger,RulesPage,Administration,PlatformControls,platform-forms,platform-forms.test}.{js,jsx}` 与 `src/platform.css`；以及 `CRDocs/DEFERRED.md` 的延期核销）。已排除不属于本轮的 `.claude/Rules.md` 与 `Tools/`。审核者未修改业务代码，未提交 Git，未改动 `Demo/`。

### 重点复核结论

1. **权限与多企业隔离（通过）**
   - **四角色接口矩阵**：`GET /api/v1/admin/models`、`POST /api/v1/admin/models/batch-review`、`GET /api/v1/admin/audit-logs` 及既有管理端路由均强制依赖 `require_admin`（`api.py:102-105,179-206`、`workflow_api.py:152-168`），非 `SYS_ADMIN` 一律返回 `403`；`POST /api/v1/auth/bind-invite` 入口前置校验 `actor.role != "TRIAL_USER"` 返回 `403`（`api.py:146-147`）；`POST /api/v1/models/batch-upload` 经 `upload_scope` 严格限定仅 `SYS_ADMIN` 可直传（`model_intake.py:18-28`）。
   - **企业五态台账与 `enterprise_id` 防提权**：`GET /api/v1/enterprise/models` 仅允许 `ENT_ADMIN`/`ENT_USER` 且 `enterprise_id` 非空，不接收 `enterprise_id` 查询参数，强制 `Model.enterprise_id == actor.enterprise_id`（`api.py:188-197`）；`GET /api/v1/models`、`GET /api/v1/rules/active`、`POST /api/v1/client/models/batch-upload` 及 `POST /api/v1/compare/upload`（`visibility.py:53-62`、`workflow_api.py:186,241`、`quota.py:51-58`）对非 `SYS_ADMIN` 均忽略请求体/查询串中的 `enterprise_id`，无法扩大租户权限。
   - **`PENDING` / `PARSE_FAILED` 零命中**：`visible_candidate_query`（`visibility.py:77-82`）与 `compare_history.py:75,108` 硬性限定 `Model.status == "APPROVED"`。`test_four_role_permissions_and_candidate_exclusion`（`test_workflow_api.py:210-290`）向 `PENDING` 和 `PARSE_FAILED` 注入 `READY` 特征并置 `is_public_demo=True` 后，经真实 PostgreSQL + Worker 对比验证四角色下两者在 `candidates` 与 `needs_review_items` 中均零命中，且非管理员访问其详情/GLB 返回 `404`。
2. **事务原子性与审计脱敏（通过）**
   - **直传审计同事务与去重**：`model_intake.py:101-143` 的 `_admit` 在 `with engine.begin()` 事务内锁企业行并校验实时账号与存储配额，重复件在写入前即返回 `skipped_duplicates`，不产生 `MODEL_DIRECT_UPLOADED`；新件建档与 `AuditLog(action="MODEL_DIRECT_UPLOADED", target_type="model", target_id=model_id)` 同事务提交。
   - **批量审核原子性与并发**：`workflow_api.py:52-72,152-168` 校验 `1..100` 件去重 int64 `model_ids`、`extra="forbid"`、驳回必填非空 `reason` 且禁止 `public_demo=True`、通过禁止携带 `reason`。路由按提交顺序逐件复用 `review_model`（`model_intake.py:61-92`），每件独立原子事务更新 `Model.status == "PENDING"` 并实时复核管理员 `auth_version`/启用/未锁状态，同事务写入 `MODEL_APPROVED_FOR_INDEXING` 或 `MODEL_REJECTED`；并发审核同一模型在 PostgreSQL 行锁下仅一路成功（`200`），另一路返回 `409 MODEL_NOT_PENDING`。驳回提交后原件清理失败时捕获 `OSError`/`ValueError`，返回已提交状态并附 `warning_code="MODEL_SOURCE_CLEANUP_FAILED"`（前端 `ModelLedger.jsx:104` 准确展示）。
   - **审计与邀请码脱敏**：`GET /api/v1/admin/audit-logs`（`api.py:199-206`）在 SQL 投影层仅选取 `id/actor_id/action/target_type/target_id/created_at`，彻底排除 `detail_json` 与 `ip_address`，历史自由文本中的密码、Token、邀请码明文无法回显；`GET /api/v1/admin/invite-codes`（`api.py:166-172`）仅投影状态与计数，不返回 `code` 明文。
3. **API 契约与前端交互（通过）**
   - **五态台账与 int64 游标**：`_ledger_page`（`api.py:281-296`）完整返回六项必记字段（`md5`、`model_name`、`uploaded_at`/`approved_at`、`uploader_id`/`uploader_name`、剔除 `cache_refs` 的 `feature_payload`、`selected_count`）及 `reject_or_error_reason`、`is_public_demo`，支持五态、企业、名称（转义 `icontains`）、MD5、上传人与 int64 `after_id`（超界返回 `422`）。`api.js:3-6,25-36` 通过 `JSON.parse` `context.source` 将全部 int64 字段保留为十进制字符串，避免浏览器 53 位精度截断。
   - **角色门禁、规则校验与试用绑定**：`platform-forms.js:2-12` 与 `Workbench.jsx:115-116,228-241` 的四角色视图门禁与后端一致，导航切换通过 `hidden` 保留当前工作台对比任务；`RulesPage.jsx` 与 `checkWeights` 实时校验 D1–D7 合计 `100%`（容差 `1e-8`）及正数阈值，按全局或企业作用域保存不可变新版本并支持一键激活，载入旧版本时保留不在页面编辑的 D4 子权重与安全门禁字段；试用账号绑定邀请码后，后端同事务递增 `auth_version` 使旧令牌即时失效（`enterprise.py:104-107`），前端随即调用 `signOut` 清除内存会话（`Workbench.jsx:240-241`）。
4. **历史延期项核销拆分核对（通过）**
   - `PLAT-006-P2-1` 的「管理员直传补记审计」已闭环并经实测验证；其剩余的「反向代理层无 Header 表单限流/超时」与「Starlette 升级时 `uploads.py:65` 私有属性兼容」继续在 [`DEFERRED.md`](../../DEFERRED.md) 独立保留，拆分口径准确。
   - `PLAT-007-P2-1` 的 `WorkflowTests._poll` 硬超时已由 25 秒放宽至 45 秒（`test_workflow_api.py:114`），并在本次全量回归中零抖动通过，核销成立。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议）**：无新增

### 独立验证结果与证据边界

审核者于 2026-11-28 在 Windows 环境（Python 3.13 虚拟环境 `step-plat-001-venv`、Node.js v24.18.0 / npm 11.16.0、本机独立临时 PostgreSQL 18.7 端口 `56439`，测毕即停库清理）独立执行以下验证：

- **本机真实 PostgreSQL 18.7 + ASGI 后端全量测试**：
  - `SHOW server_version` 返回 `18.7`；`python -m pip check` 无依赖冲突；`python -m compileall -q Platform/backend` 通过。
  - `python -m unittest discover -s Platform/backend/tests -v`：**69/69 通过**（耗时 `145.576s`，0 跳过、0 失败），含新增 `test_admin_ledger_and_batch_review`、`test_four_role_permissions_and_candidate_exclusion` 及全部并发、配额、隔离、预览与 Alembic 元数据一致性检查（`No new upgrade operations detected.`）。
- **前端测试与生产构建**：
  - `npm.cmd test --prefix Platform/frontend`：**6/6 通过**（`147.99ms`），覆盖七维 100% 权重校验、四角色视图门禁、批量审核载荷与 int64 编号传输。
  - `npm.cmd run build --prefix Platform/frontend`：Vite 8.4.0 构建成功（`225ms`，产出 `dist/`）。
- **工程门禁**：
  - `git diff --check` 无空白错误；`git diff --exit-code demo-v0.3.1 -- Demo/` 退出码 `0`（`Demo/` 严格冻结）。
  - 手写源码行数统计（排除 `node_modules`/`dist`/`.venv`/`__pycache__`/`migrations/versions`）：共 74 个文件，全工程最长 585 行（`tests/test_database.py`），生产源码最长 381 行（`app/workflow_api.py`），前端最长 344 行（`src/workbench.css`），均远低于 1000/1500 行门禁；全中文注释与轻量依赖约束合规。
- **未验证边界**：
  - 本轮独立审核未亲自启动实时浏览器复跑页面点击（执行者已在本地 Vite preview + 临时 Uvicorn 夹具下完成管理员/企业普通用户/试用用户冒烟，`ENT_ADMIN` 由真实 PostgreSQL + ASGI 权限矩阵覆盖）。
  - 客户真实 STEP 样本、目标部署环境整机并发/反向代理限流与超时、HTTPS、目标浏览器/GPU WebGL 兼容及 Starlette 升级兼容，继续由 [`DEFERRED.md`](../../DEFERRED.md) 跟踪。
