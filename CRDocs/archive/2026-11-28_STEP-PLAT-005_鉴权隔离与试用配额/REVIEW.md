# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核与整改交接。[归档与交接规则](../../README.md)

## 第 1 轮独立审核 — STEP-PLAT-005（2026-11-28）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，2 个 P2 非阻断建议）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `ecc9474`，覆盖阶段 3 首切片全部改动与工作树新增文件（`app/config.py`、`app/main.py`、`app/api.py`、`app/enterprise.py`、`app/auth/{cache,rate_limit,service,tokens}.py`、`app/repository/{visibility,quota}.py`、`app/compare/trial_job.py`、`requirements.txt`、`tests/test_database.py`、`tests/test_stage3_first_slice.py`）。审核者未修改任何业务代码。

### 重点复核结论

1. **`auth_version` 即时失效与慢哈希并发守卫（通过）**
   - `auth/service.py:authenticate` 每次均回查数据库账号状态，成功缓存键为 `HMAC-SHA256(pepper, f"{user_id}:{auth_version}:{password}")`，仅缓存成功项，且由有界 LRU/TTL（`AuthSuccessCache`）管控容量。
   - 改密（`set_password`）、启禁用（`set_active`）、改角色/企业（`set_role_or_enterprise`）、邀请码绑定（`bind_invite`）及第 5 次连续失败锁定均原子递增 `auth_version`；Bearer JWT 在 `resolve_token_user` 每次回查 `id + auth_version + is_active + locked_until`，旧令牌与旧缓存均即时失效。
   - Argon2id 慢哈希期间不持数据库锁；回写失败计数或复位时以 `WHERE id AND auth_version AND is_active AND (locked_until IS NULL OR locked_until <= now)` 复核，防止并发旧请求覆盖较新的锁定或改权；失败事务先提交再抛 `AuthenticationError`，确保 5 次失败锁定持久化不被回滚。
   - `main.py:_invalid_request` 统一将 422 校验错误收敛为 `{"error_code": "INVALID_REQUEST"}`，杜绝密码与邀请码在错误响应中回显。
2. **双维度限流（通过）**
   - `auth/rate_limit.py:AuthRateLimiter` 在单进程内以有界 `OrderedDict` 维护 `ip:{client_ip}` 与 `user:{username.casefold()}` 双维度滑动窗口；原子检查两维度，任一超限即拒绝且不写入命中；容量满且无过期桶时拒绝新增维度而非驱逐活跃攻击桶，防内存膨胀与限额重置。
   - 未知账号或不可用账号在未命中缓存时同样经过限流与 `_dummy_hash` 校验，缓解枚举与时序侧信道；`api.py:_client_ip` 不盲信可伪造的 `X-Forwarded-For`。
3. **邀请码并发核销（通过）**
   - `enterprise.py:bind_invite` 在同一事务内先以条件更新 `UPDATE invite_codes SET used_count = used_count + 1 WHERE code AND is_active AND used_count < max_uses AND expires_at > now AND EXISTS(ACTIVE enterprise)` 核销用量，再条件更新 `users`（校验 `TRIAL_USER`、`enterprise_id IS NULL`、`is_trial`、`is_active`、未锁定及可选 `auth_version`）并递增 `auth_version`。
   - 同一试用账号并发绑定两枚不同邀请码时，PostgreSQL 行锁保证仅胜出事务更新 `users`，败北事务 `changed != 1` 抛 `EnterpriseError` 并自动回滚已加 1 的邀请码用量（真实 PostgreSQL 18.7 复测通过）。列表与审计日志均不返回邀请码明文。
4. **`apply_visibility_scope` 全路径调用链（通过）**
   - `repository/visibility.py:apply_visibility_scope` 遍历 `get_final_froms()` 解包 `Join`，强制要求查询直接使用 `Model.__table__` 并显式拒绝别名/子查询（防基表谓词错绑生成笛卡尔积）。
   - 复核全部 5 条调用链均经此守卫：模型列表（`GET /api/v1/models`）、模型详情（`GET /api/v1/models/{model_id}`）、预览下载（`GET /api/v1/models/{model_id}/glb` + `vault_path` + `Cache-Control: private, no-store`）、试用候选生成（`trial_job.py` → `visible_candidate_query`）及结果发布终检（`quota.py:_check_result_visibility` 持 `FOR SHARE` 行锁复核每个结果 `model_id` 仍在试用公开已审范围内，越界则整笔回滚）。
   - `ENT_USER` / `ENT_ADMIN` 忽略越权 `target_enterprise_id`，`TRIAL_USER` 仅见 `is_public_demo=True AND status='APPROVED'`，越权详情与 GLB 统一返回 404 不泄露存在性。
5. **PostgreSQL 10 路预占、损坏件释放、回收与迟到完成竞争（通过）**
   - `enqueue_trial_compare` 在 PostgreSQL 显式使用 `READ COMMITTED` + `pg_advisory_xact_lock(_QUEUE_ADMISSION_LOCK)` 序列化队列深度检查（满队列在预占前直返 `QUEUE_FULL`），同事务原子校验 `used_count + in_flight_count < 5` 与 `auth_version` 后写入 `CompareJob` 与 `TrialReservation`。
   - `run_trial_compare_job` 捕获 `StepError`/`CompareTaskError` 及意外异常（含发布期可见性撤回抛出的 `QuotaError`）均调用 `release_trial_quota` 幂等释放预占。
   - `commit_trial_results`、`release_trial_quota` 与 `reap_expired_trial_quota` 严格采用统一锁序（`CompareJob FOR UPDATE` → `TrialReservation` CAS → `Model FOR SHARE` → `User` → `CompareResult`），无死锁环。无论回收先获锁还是迟到完成先获锁，已过期任务均零写入 `CompareResult`、置 `FAILED (QUOTA_EXPIRED)`、`used_count` 不增加且 `in_flight_count` 精确扣减一次。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议，后续切片跟进）**：
  1. **`PLAT-005-P2-1`（OPEN｜邀请码绑定限流与分页 `after_id` 上界校验）**
     - **位置**：`Platform/backend/app/api.py:124-160`
     - **说明**：`POST /api/v1/auth/bind-invite` 虽为已鉴权接口，但未单独限制邀请码试错频率；`list_enterprises`、`list_invites`、`list_models` 的 `after_id` 参数未限定 int64 上界（超大整数可能在 PostgreSQL 触发驱动异常而非 422）。不阻断本切片，建议在公开注册与完整业务 API 切片补齐。
  2. **`PLAT-005-P2-2`（OPEN｜多进程部署下的共享限流与可信代理真实 IP）**
     - **位置**：`Platform/backend/app/auth/rate_limit.py`、`Platform/backend/app/api.py:187-188`
     - **说明**：当前双限流为单进程内存窗口，`_client_ip` 读取 `request.client.host`；首期单机/单进程合理且符合不引入 Redis 的约束，但在多 Worker 或反向代理目标环境需配置可信代理透传并评估跨进程共享限流（归入 `PLAT-DEF-002` / `PLAT-DEF-003` 跟踪）。

### 独立验证结果与证据分级

审核者独立复跑以下验证（Windows，Python 3.13.14，`argon2-cffi` + `PyJWT`，本机临时 PostgreSQL 18.7 端口 `56432`，测毕即停库）：

- **本机真实 PostgreSQL 18.7 证据（11/11 通过；全量 59/59 通过，60.474s）**：独立复跑 `test_trial_ten_concurrent_admissions_and_late_completion`（10 路并发仅 5 笔入队、真实损坏 STEP 经 `OcctPool` 拒绝并释放预占、模拟过期后回收与迟到发布并发零落库）与 `test_concurrent_invite_binding_consumes_only_winning_code`（双邀请码并发绑定仅核销胜出项），外加 9 项既有 PostgreSQL 迁移/队列/版本切换回归，Alembic `check` 无漂移。
- **SQLite / ASGI 路由证据（`test_stage3_first_slice.py` 9/9 通过）**：覆盖有界 LRU/TTL、改密/5 次锁定/绑定邀请码即时失效、双限流与满容量拒绝、`apply_visibility_scope` 四角色与别名拦截、5 次预占与越界结果回滚、以及真实 FastAPI ASGI 路由上的 Basic/Bearer 鉴权、422 脱敏、管理权限与列表/详情/GLB 隔离（SQLite 不用于并发结论）。
- **工程门禁证据**：`pip check`、`compileall`、`git diff --check ecc9474`、`git diff --exit-code 1e0fc76 -- Demo` 全过；手写源码最大 560 行（`tests/test_database.py`），生产文件最大 326 行（`compare/scoring.py`），1000/1500 行门禁与全中文注释规范合规。
- **目标环境证据（未实测）**：尚未在冻结的目标部署环境验证多进程鉴权限流、HTTPS 终止与可信代理真实 IP、常驻调度/定时回收以及全链路 HTTP 并发压测。

### 未验证边界

- 公开注册开关与防刷验证码、企业对比完整入队/混合同步双 API（200/202 轮询）、五态审核上传入库、规则配置后台、常驻任务消费与定时回收调度器均待后续切片交付。
- 目标部署环境整机并发压测、多进程限流、HTTPS/代理实测及客户 20–50 件真实 STEP 样本验收继续由 `PLAT-DEF-001/002/003` 承接。
