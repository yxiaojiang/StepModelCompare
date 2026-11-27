# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核结论（最新在前）。超过 3 轮或切换任务前须先按 [`README.md`](README.md) 归档至 `archive/`。

## 第 1 轮独立审核 — STEP-PLAT-002（2026-11-27）

- **审核结论**：`APPROVED`（仅针对本轮 `STEP-PLAT-002` 送审范围：计划 2.1 任务级对比执行骨架 `app/compare/worker.py` 以及 `PLAT-001-P2-1`、`PLAT-001-P2-2` 两项闭环修正；不代表完整 D5 24 初值精排、配额事务、客户真实 STEP 样本集或目标部署环境已验收）
- **审核分支与范围**：`feat/platform-v1` 工作区 `Platform/backend/`（含 Git 未跟踪新文件）及 `CRDocs/` 活动文件；历史基线参考 [STEP-PLAT-001 独立审核归档](../2026-11-27_STEP-PLAT-001_后端骨架与OCCT解析池/REVIEW.md)
- **问题汇总**：P0 `0` 项，P1 `0` 项，新开 P2 `0` 项；上轮遗留 `PLAT-001-P2-1`、`PLAT-001-P2-2` 均已独立复核并标记 `RESOLVED`

### 逐项核对结论

1. **`PLAT-001-P2-1`（日志脱敏启动补装与幂等性）**：**通过（`RESOLVED`）**
   - `Platform/backend/app/main.py:27-31` 在 `_lifespan` 启动最前（`Settings()` 之前）再次调用 `enable_log_redaction()`；`Platform/backend/app/http_security.py:93-98` 按 `isinstance(item, CredentialLogFilter)` 判重，保证幂等。
   - 独立复测：在 `create_app()` 后强制替换全部 5 个 logger（`step_platform`、`uvicorn`、`uvicorn.access`、`uvicorn.error`、`uvicorn.asgi`）的 `filters` 列表，连续启动两次 `_lifespan`，5 个 logger 均恢复且各自恰好仅有 `1` 个 `CredentialLogFilter`。同时复测了查询凭证（`uvicorn.access` 剥离查询串）、`Authorization` / `Cookie` 头掩码、异常堆栈清空以及 `uvicorn.asgi` 丢弃；另复跑真实 Uvicorn（CLI normal、CLI `--log-level trace`、`uvicorn.run(app)` + 未捕获异常路由），均保持 `0` 泄露。

2. **`PLAT-001-P2-2`（严格网格门禁与盘点兼容）**：**通过（`RESOLVED`）**
   - `Platform/backend/app/geometry_engine/worker_runner.py:150-156,225-236` 新增 `require_mesh: bool = False`（开启时强制要求 `mesh_target is not None`）：遇到 `value["mesh_error"]` 直接抛原 `StepError`，槽位未产出本次新 `mesh.npz` 时抛 `StepError("ERR_STEP_MESH_MISSING")`，不读取也不复用已有目标文件；`Platform/backend/app/compare/worker.py:66` 正式对比入口固定以 `require_mesh=True` 调用。
   - 独立复测三种场景均通过：① 正式入口 `run_compare_task` 下真实 Demo STEP 触发 `max_mesh_triangles=1` 抛 `StepError("ERR_MODEL_TOO_COMPLEX")`；② 正式入口下子进程未产出网格抛 `StepError("ERR_STEP_MESH_MISSING")`；③ 磁盘已有旧 `stale.npz` 目标文件时，`require_mesh=True` 仍抛 `ERR_STEP_MESH_MISSING` 且不修改、不复用旧产物。
   - 阶段 1 盘点脚本回归：用 `Demo/.venv` 分别执行常规扫描（14 件：13 解析成功、1 文件头失败、12 对单次 `igl` ICP `ok`）与 `--max-mesh-triangles 1`（14 件：保留 13 件解析成功与协议/拓扑 `AP214:13, UNKNOWN:1, single_closed_solid:13`，单列 `mesh_failures={"ERR_MODEL_TOO_COMPLEX": 13}`，输出 0 对 ICP），兼容口径无退化。

3. **任务总时限（单调时钟共用、强杀回收与临时清理）**：**通过**
   - `Platform/backend/app/compare/worker.py:58-73` 在入口生成单一 `deadline = monotonic() + settings.compare_job_timeout`，透传给 `occt_pool.parse(..., deadline=deadline)`（`worker_runner.py:237-239` 取池时限与调用方截止值的较早者），再以同一 `deadline` 监督第二层 `spawn` 计算进程。
   - 故障注入复测：① 第一层解析卡死时，按任务截止值强杀回收 OCCT 进程、抛 `CompareTaskError("ERR_COMPARE_TIMEOUT")`，后续解析自动拉起新 PID；② 第二个候选卡死时，父进程 `kill()` + `join()` + `close()` 回收第二层计算进程，不返回第一候选的部分结果，下一任务正常运行；③ 第二层在截止值刚过后写盘的迟到场景被 `_check_deadline` 拦截并抛 `ERR_COMPARE_TIMEOUT`；④ `TemporaryDirectory(prefix="step-compare-")` 在 `finally` 回收子进程后自动清理，实测无残留 `step-compare-*` 目录与孤儿子进程。

4. **候选处理与真实 Demo STEP → libigl 链路**：**通过**
   - `Platform/backend/app/compare/worker.py:59-61` 在调用 OCCT 之前即校验 `len(work) > settings.max_refine_k`，超限直接抛 `CompareTaskError("ERR_COMPARE_BUDGET_EXCEEDED")`。
   - `compare/worker.py:141-154` 在任务内只构建一次 `igl.AABB()` 查询树并复用于全部候选；按输入 `candidates` 顺序遍历；坏缓存或缺失缓存由 `_load_mesh`（`allow_pickle=False` + 维度/类型/有限值/越界索引校验 + 只读数组）拦截，按 `model_id` 记录 `ERR_COMPARE_CANDIDATE_MESH` 而不中断其余候选。
   - 真实 Demo 英寸件 `SHOW-POSE-INCH.step` 经第一层 OCCT 生成 `.npz` 网格后进入第二层 `igl.AABB.squared_distance`，复测同一任务内候选顺序 `[3, 2, 1]`、中间坏缓存单列诊断、首尾复用同一子进程 PID 与同一 `id(tree)`，且最大平方距离 `< 1e-15`。

5. **合规与工程门禁**：**通过**
   - **中文注释与 Docstring**：全量扫描 `Platform/backend/` 全部 22 个 `.py` 文件，所有模块、类及公开函数/方法均具备中文 Docstring，39 条行内注释均为中文（仅 `tests/test_database.py:197` 局部闭包 `consume` 无 Docstring，属私有局部函数）。
   - **依赖合规**：无 Redis / Celery / RQ / `trimesh` / `pythonocc-core`；`pip check` 与 `compileall` 均通过。
   - **行数门禁**：21 个手写源码文件（不含 `alembic/versions/`）最大 **293 行**（`tests/test_occt_pool.py`），远低于 1000/1500 行门禁。
   - **`git diff --check` 与 `Demo/` 冻结**：已跟踪改动与临时索引下未跟踪 `Platform/backend/` 均通过 `git diff --check`；`git diff --exit-code HEAD -- Demo` 退出码 `0`，`git status --short --untracked-files=all -- Demo` 计数 `0`，`Demo/` 完整冻结。

### 问题清单（沿用稳定 ID）

| 问题 ID | 级别 | 状态 | 位置 | 核销依据 |
| --- | :---: | :---: | --- | --- |
| `PLAT-001-P2-1` | P2 | `RESOLVED` | `Platform/backend/app/main.py:27-31`、`Platform/backend/tests/test_http.py:102-113` | `_lifespan` 启动时幂等补装 `enable_log_redaction()`；独立复测 5 个 logger 过滤器替换后连续启动两次均恢复且不叠加，真实 Uvicorn 0 泄露。 |
| `PLAT-001-P2-2` | P2 | `RESOLVED` | `Platform/backend/app/geometry_engine/worker_runner.py:150-156,225-236`、`Platform/backend/app/compare/worker.py:66` | 正式对比启用 `require_mesh=True`；独立复测网格超限、缺失与已有旧目标文件三类场景均失败且不复用旧产物；盘点脚本默认模式保留协议/拓扑与 `mesh_error` 诊断。 |

### 独立复测命令与证据边界

| 证据类别 | 复测命令与结果摘要 | 边界说明 |
| --- | --- | --- |
| **真实 PostgreSQL 18.7** | 启停本机临时 PG 18.7 实例，执行 `alembic upgrade head` / `check`（`No new upgrade operations detected`）/ `downgrade base` / `upgrade head` / `upgrade head --sql`（含 `JSONB`），全量 `unittest discover -s Platform/backend/tests -v` **26/26 通过**（30.30s），以及独立连接持锁 + 10 路 `SKIP LOCKED` 并发与超时边界复测通过。 | 覆盖既有迁移、队列抢占回归与全套单测；常驻队列调度器、进程异常后的数据库任务回收、10 路试用配额 CAS/迟到拦截（`QUOTA_EXPIRED`）与跨企业全路径隔离归后续切片（见 `PLAT-DEF-002`）。 |
| **SQLite 替身测试** | `SqliteMigrationTests` 与 `test_http.py` 全部通过（含 `PLAT-001-P2-1` 过滤器替换回归）。 | 仅用于基础迁移、外键/约束与 HTTP 路由/生命周期单测，不代表并发与行锁结论。 |
| **Demo 自制样例** | `Demo/samples/rehearsal/queries/SHOW-POSE-INCH.step` 验证 OCCT 网格 → `igl.AABB` 链路及 `max_mesh_triangles=1` 严格拦截；`Demo/.venv` 独立跑通 `step_inventory.py` 常规模式（14 件 / 13 成功 / 12 对 ICP `ok`）与 `--max-mesh-triangles 1`（13 件保留协议与拓扑、`ERR_MODEL_TOO_COMPLEX: 13`、0 对 ICP）。 | 均为 `Demo/` 演示件与测试合成网格，**不代表**客户 20–50 件真实 `.stp` 样本的协议、PMI/材料覆盖率、面片分裂或完整 D5 24 初值精排耗时（见 `PLAT-DEF-001`）。 |
| **故障注入与真实 Uvicorn** | 独立复测 5 logger 过滤器替换与真实 Uvicorn（CLI normal / `trace` / `uvicorn.run` + 异常路由 0 泄露）；合成故障复测解析阶段卡死、第二候选卡死、迟到结果拦截、进程崩溃恢复、临时目录与子进程零残留。 | 验证任务级进程监督与熔断框架；完整 D5 24 初值 Trimmed ICP 与动态剪枝归计划 2.3/2.4 验收。 |
| **未验证环境** | 本机为 Windows Python 3.13.14，无 Linux 宿主与客户生产服务器。 | Linux `RLIMIT_AS`、目标部署环境权限与容器/服务化配置、Nginx 与全链路 HTTPS 待目标环境实测（见 `PLAT-DEF-003`）。 |
