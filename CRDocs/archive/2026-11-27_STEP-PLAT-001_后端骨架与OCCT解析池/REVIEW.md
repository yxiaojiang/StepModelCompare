# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核结论（最新在前）。超过 3 轮或切换任务前须先按 [`README.md`](README.md) 归档至 `archive/`。

## 第 1 轮独立审核 — STEP-PLAT-001（2026-11-27）

- **审核结论**：`APPROVED`（仅针对本轮 `STEP-PLAT-001` 送审范围：`Platform/backend/` 骨架、核心表迁移、`SKIP LOCKED` 队列、HTTP 凭证拦截与日志脱敏、常驻 OCCT 解析池与阶段 1 盘点脚本；不代表客户真实 STEP 样本集、完整并发配额业务或生产部署环境已验收）
- **审核分支与范围**：`feat/platform-v1` 工作区 `Platform/backend/`（含 Git 未跟踪新文件）及 `CRDocs/IMPLEMENTATION.md`、`CRDocs/STATUS.md`
- **问题汇总**：P0 `0` 项，P1 `0` 项，P2 `2` 项（不阻断本轮通过，供后续切片顺手处理）

### 逐项核对结论

1. **启动配置与十张核心表 / Alembic 迁移（设计第 4.1 节）**：**通过**
   - `Platform/backend/app/config.py:48-53` 严格校验 `reaper_ttl > queue_wait_timeout + compare_job_timeout + 30`（等于或小于边界均抛 `ValueError`），`Platform/backend/app/main.py:29` 在 FastAPI `_lifespan` 启动时实例化 `Settings()`，非法配置阻断启动。
   - `Platform/backend/app/db/models.py` 与 `Platform/backend/alembic/versions/0001_core_tables.py` 完整落地设计第 4.1 节十张核心表（`enterprises`、`invite_codes`、`users`、`trial_reservations`、`models`、`model_features`、`similarity_rules`、`compare_jobs`、`compare_results`、`audit_logs`）、全部外键、状态/计数 `CheckConstraint`、唯一约束（含 `model_features(model_id, feature_version)`、`compare_results(job_id, model_id)` 及 `similarity_rules` 全局/企业双部分唯一索引）与核心索引。为区分队列领取态补充的 `compare_jobs.started_at` 与 `models.indexing_started_at` 保持五态/四态原状，合理可逆。
   - 真实 PostgreSQL 18.7 与 SQLite 均独立复测 `upgrade head` → `check`（`No new upgrade operations detected`）→ `downgrade base` → `upgrade head`，离线 `--sql` 含 `JSONB`。

2. **PostgreSQL `FOR UPDATE SKIP LOCKED` 对比与入库队列**：**通过**
   - `Platform/backend/app/db/queue.py:41-49` 用单条 `UPDATE ... WHERE id = (SELECT id ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id` 在短事务 `with engine.begin()` 内完成抢占并立即提交释放锁，且显式拒绝非 PostgreSQL 方言（抛 `RuntimeError`）。
   - `claim_compare_job` 按 `(created_at, id)` 领用 `QUEUED` 且 `created_at > statement_timestamp() - queue_wait_timeout` 的记录，超时记录保留待配额回收流程处置；`claim_indexing_model` 保持 `status = 'INDEXING'` 并以 `indexing_started_at` 区分抢占态。
   - 独立复测：在新建临时 PostgreSQL 18.7 实例上，用独立引擎持有首行 `FOR UPDATE` 行锁，10 路并发线程竞争 6 条有效解锁任务，结果精确领走 6 条且无重复、跳过首行锁、未触碰 65s 与 60.05s 两类超时边界记录；释放首行锁后立即领到首行，且 `compare_jobs` 无残留活跃元组锁。

3. **URL 凭证拦截与 Uvicorn 日志脱敏**：**通过**
   - `Platform/backend/app/http_security.py:22-59` 的 `RejectUrlCredentials` 中间件在路由前拦截 Query 凭证（覆盖 URL 编码、大小写、无值、分号分隔、嵌套键及超 1024 字段），返回 `400 CREDENTIALS_IN_URL_FORBIDDEN` 且不回显凭证值。
   - `CredentialLogFilter` 对 `uvicorn.access` 剥离查询串与片段并保留路径/状态码，直接丢弃 `uvicorn.asgi` 原始作用域跟踪日志，对 `step_platform`、`uvicorn`、`uvicorn.error` 抹除异常堆栈/原文并掩码凭证关键字。
   - 独立实测：以 `python -m uvicorn app.main:app`（默认级别与 `--log-level trace`）及 `uvicorn.run(app)`（含触发未捕获异常的测试路由）启动真实服务，发送 `/health?%74oken=...`、`/nonexistent?password=...` 及携带 `Authorization` / `Cookie` 头请求，响应与日志均验证 `0` 泄露，未捕获异常仅输出 `请求或服务处理异常（详情已脱敏）`。

4. **常驻受控 OCCT 子进程池**：**通过**
   - `Platform/backend/app/geometry_engine/worker_runner.py` 采用 `spawn` 独立子进程 + 单字节管道信号 + 槽位临时文件传递；超时后 `process.kill()` 强杀并 `join()/close()` 回收，后续请求自动拉起新 PID；任务结束后 `finally` 清理槽位 `mesh.npz`，网格发布先写同目录临时目录再原子 `replace()`，杜绝半成品可见。
   - 已复测任务轮换（`recycle_after`）、崩溃/异常恢复、Windows Job Object 128 MiB 上限拦截 256 MiB 分配、真实 OCCT 原始 B-Rep 面数上限（`ERR_MODEL_TOO_COMPLEX`）、网格三角片上限（`mesh_error=ERR_MODEL_TOO_COMPLEX` 且不落盘目标网格），以及 `step_reader.py:53-59` 在 `Transfer()` 前通过 `STEPConstruct_ExternRefs` 拦截外部文件引用（`ERR_STEP_EXTERNAL_REFERENCE`）。

5. **阶段 1 样本盘点脚本（`Demo/.venv` 独立运行与统计口径）**：**通过**
   - `Platform/backend/scripts/step_inventory.py` 仅依赖标准库与几何包，用 `Demo/.venv/Scripts/python.exe` 在仓库根目录及 `%TEMP%` 目录均可独立运行并输出合法 UTF-8 JSON。
   - 失败样本保留在统计分母（`scanned=14`、`parsed=13`、`ERR_INVALID_STEP_HEADER=1`、`protocols={AP214: 13, UNKNOWN: 1}`）；材料与语义 PMI 总体率以全部扫描数（14）为分母，AP242 专项率以识别到的 AP242 数为分母；拓扑项允许同文件重叠并单列原始 B-Rep 面与三角片；`_measure_icp` 仅统计单次 `igl.iterative_closest_point` 耗时并校验旋转矩阵行列式。

6. **合规与工程门禁**：**通过**
   - **中文注释与 Docstring**：AST 与分词全量扫描 `Platform/backend/` 全部 19 个 `.py` 文件，所有模块、类及公开函数/方法均具备中文 Docstring，31 条行内注释均为中文（仅 `tests/test_database.py:197` 局部闭包 `consume` 无 Docstring，属私有局部函数，符合规范）。
   - **依赖合规**：`requirements.txt` 与 `tests/requirements.txt` 严格限定在采纳技术栈内，无 Redis/Celery/RQ/`trimesh`/`pythonocc-core`；`OCP` 仅在隔离解析模块与测试用例中导入；`pip check` 通过。
   - **行数门禁**：18 个手写源码文件（不含 `alembic/versions/`）最大 **279 行**（`tests/test_occt_pool.py`），远低于 1000/1500 行门禁。
   - **`git diff --check` 与 `Demo/` 冻结**：已跟踪改动与临时索引下未跟踪 `Platform/backend/` 均通过 `git diff --check`；`git diff --exit-code HEAD -- Demo` 与 `git status --short --untracked-files=all -- Demo` 均为空输出，`Demo/` 完整冻结。

### 问题清单（P0 / P1 / P2）

| 问题 ID | 级别 | 状态 | 位置 | 影响说明 | 最小修复建议 |
| --- | :---: | :---: | --- | --- | --- |
| `PLAT-001-P2-1` | P2 | `OPEN` | `Platform/backend/app/main.py:18-36`、`Platform/backend/app/http_security.py:93-98` | 当前仅在 `create_app()` 调用 `enable_log_redaction()`；若后续部署通过自定义 Uvicorn `log_config` 在应用导入后重建 logger 配置，可能丢失已挂载的过滤器。 | 在 `_lifespan` 启动阶段幂等补调一次 `enable_log_redaction()`，确保任何日志配置顺序下过滤器均生效。 |
| `PLAT-001-P2-2` | P2 | `OPEN` | `Platform/backend/app/geometry_engine/step_reader.py:90-98` | 当 `mesh_path` 非空且网格离散触发 `StepError`（如 `ERR_MODEL_TOO_COMPLEX`）时，`read_step` 捕获异常并写入 `result["mesh_error"]` 后正常返回（便于阶段 1 盘点保留协议/拓扑）；后续阶段 2 入库/对比调用方若只捕获异常而不检查 `mesh_error`，可能误判为解析成功。 | 在阶段 2 接入 `OcctPool.parse(..., mesh_target=...)` 时，显式校验 `"mesh_error" not in result` 或增加严格模式开关，避免静默漏判。 |

> 附注（仓库卫生）：`Platform/backend/alembic/versions/__pycache__/` 存在历史遗留文件 `0001_平台核心表.cpython-313.pyc`，提交前建议确认 `.gitignore` 忽略 `__pycache__/`，不提交字节码产物。

### 独立复测证据与证据边界

| 证据类别 | 本轮独立复测内容与结果 | 边界说明 |
| --- | --- | --- |
| **真实 PostgreSQL 18.7** | 使用本机 `D:\UserTemp\step-plat-001-pg18` 启停独立临时实例，独立执行 `alembic upgrade head` / `check` / `downgrade base` / `upgrade head` / `upgrade head --sql`（含 `JSONB`）、全部 **19/19** 项 `unittest`（含 3 项 PG 用例），以及独立连接持首行锁 + 10 路并发 `SKIP LOCKED` + 超时边界（65s / 60.05s）复测，全部通过。 | 仅覆盖首版建表、回滚与队列抢占骨架；队列入口限深、进程故障后任务回收、10 路试用配额 CAS、迟到结果拦截（`QUOTA_EXPIRED`）及跨企业全路径隔离仍待后续切片实测（见 `PLAT-DEF-002`）。 |
| **SQLite 替身测试** | `SqliteMigrationTests` 与 `test_http.py` 全部通过；确认 `claim_compare_job` / `claim_indexing_model` 对 SQLite 显式抛 `RuntimeError`。 | 仅用于基础迁移、外键/约束与 HTTP 路由单测，不代表并发与行锁结论。 |
| **故障注入与真实 Uvicorn** | 合成子进程卡死强杀回收、崩溃退出、Python 异常、2 任务轮换、Windows Job Object 128 MiB 限制拦截 256 MiB 分配均复测通过；真实 Uvicorn（CLI normal、CLI `--log-level trace`、`uvicorn.run` + 未捕获异常路由）实测 0 凭证/堆栈泄露。 | 故障注入采用可控测试 `_processor`，用于验证池框架与 OS 强杀机制。 |
| **Demo 与自制 STEP 样例** | 用 `Demo/.venv` 独立跑通 `step_inventory.py` 扫描 `Demo/samples/rehearsal`（14 件：13 成功 + 1 文件头失败留分母，AP214 13 件/UNKNOWN 1 件，材料与 PMI 0/14，12 对单次 `igl` ICP 均 `ok`，P50/P95 15.9/21.0 ms），并从 `%TEMP%` 复测 `--max-brep-faces 2`（13 超限 + 1 文件头失败）；`RealOcctTests` 覆盖 Demo 英寸件及自制 AP242 材料/PMI、多实体、开壳、外部引用拦截。 | 全部为 `Demo/` 演示件与代码生成的合成样例，**不代表**客户 20–50 件真实 `.stp` 样本的协议、PMI/材料覆盖率、面片分裂或 24 初值精排耗时（见 `PLAT-DEF-001`）。 |
| **未验证环境** | 无 Linux 运行环境及客户目标服务器。 | Linux `RLIMIT_AS` 分支、客户目标服务账号下 Windows Job Object / Linux cgroup 权限边界、Nginx 反向代理与全链路 HTTPS 均未实测（见 `PLAT-DEF-003`）。 |
