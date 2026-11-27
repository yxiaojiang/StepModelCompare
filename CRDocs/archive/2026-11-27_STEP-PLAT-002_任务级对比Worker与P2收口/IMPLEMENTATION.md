# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录。归档规则见 [README.md](README.md)。

## 第 1 轮 — STEP-PLAT-002（2026-11-27）

分支 `feat/platform-v1`。承接上一轮 [STEP-PLAT-001 独立审核](../2026-11-27_STEP-PLAT-001_后端骨架与OCCT解析池/REVIEW.md)。

### 改动与执行边界

| 文件 | 内容 |
| --- | --- |
| `Platform/backend/app/main.py` | `PLAT-001-P2-1`。保留创建应用时的脱敏，在启动生命周期加载配置前再幂等补装过滤器。 |
| `Platform/backend/app/geometry_engine/worker_runner.py` | `PLAT-001-P2-2`。新增必需网格模式，网格离散失败时传播原错误，缺少本次网格时返回 `ERR_STEP_MESH_MISSING`。解析池取自身时限与调用方截止值的较早者。 |
| `Platform/backend/app/compare/__init__.py`、`worker.py` | 计划 2.1 的任务级执行入口。查询件解析复用常驻池并强制要求网格。同一计算子进程只构建一次查询件 `igl.AABB`，按输入顺序加载候选并执行调用层提供的计算函数。 |
| `Platform/backend/tests/test_compare_worker.py`、`test_http.py`、`test_occt_pool.py` | 两项 P2 和任务执行回归，包括网格超限、缺少新产物、候选预算、顺序、查询树复用、两个阶段的超时以及进程退出。 |
| `CRDocs/`、`.claude/` | 按角色归档上轮原始记录，建立本轮送审记录并同步当前计划。 |

- 任务由调用方提供已筛选的候选和可信、可跨进程传递的计算函数。输入超出 `max_refine_k` 时先返回 `ERR_COMPARE_BUDGET_EXCEEDED`，候选损坏或缺失按模型 ID 给出诊断。查询失败、进程失败或总时限耗尽均不交付部分结果。Linux 和 Windows 使用同一个 `spawn` 计算进程监督流程。每个任务启动一个第二层进程，任务内候选共用该进程和查询树。[Python multiprocessing](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Process.join) · [libigl AABB](https://libigl.github.io/libigl-python-bindings/api/igl/#aabb)
- `compare_job_timeout` 使用一个单调时钟截止值，覆盖第一层解析等待、解析、第二层启动和所有候选计算。截止时回收正在处理该任务的子进程，返回 `ERR_COMPARE_TIMEOUT`。任务文件在临时目录内处理，调用结束后清理。
- 正式对比调用 `OcctPool.parse(..., require_mesh=True)`，无新网格即失败。阶段 1 盘点继续保留解析成功时的协议、拓扑和独立网格诊断。两项 P2 的执行者自测已通过，审核状态由独立审核者回写。

### 实测验证

在仓库根目录使用 Windows Python 3.13.14。复用上轮的本机 PostgreSQL 18.7 临时实例，各集成测试使用隔离 schema。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path Platform/backend).Path
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56431/step_plat_001'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m alembic -c Platform/backend/alembic.ini check
& $py -m unittest discover -s Platform/backend/tests -v
```

- 本机全部 **26 项通过**，其中 3 项连接真实 PostgreSQL，覆盖既有迁移与队列行锁行为。SQLite 继续用于基础迁移、约束和 HTTP 用例。`pip check`、语法检查通过，Alembic 报告 `No new upgrade operations detected`。
- 5 项对比 Worker 用例独立运行通过。真实 Demo 英寸件经第一层生成网格，第二层对同一件做 libigl 表面最近距离查询，复用了同一进程和查询树。输入中的损坏候选按原顺序单列诊断。故障注入已在查询解析阶段与第二个候选阶段分别复现卡死，断言进程被回收、部分结果未交付、下一任务可运行。
- 日志回归模拟应用创建后过滤器被替换，连续启动两次均恢复脱敏，且每次只有一个过滤器。真实 OCCT 超出三角片上限时，严格模式抛 `ERR_MODEL_TOO_COMPLEX`，普通盘点模式保留结构化诊断。

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& Demo/.venv/Scripts/python.exe Platform/backend/scripts/step_inventory.py Demo/samples/rehearsal --sample-label 'Demo 自制样例，STEP-PLAT-002 回归' --output (Join-Path $env:TEMP 'step-plat-002-demo-inventory.json')
& Demo/.venv/Scripts/python.exe Platform/backend/scripts/step_inventory.py Demo/samples/rehearsal --max-mesh-triangles 1 --output (Join-Path $env:TEMP 'step-plat-002-demo-inventory-limit.json')
```

- 普通盘点扫描 14 件，13 件解析成功，1 件文件头失败，完成 12 对单次 igl ICP。三角片上限设为 1 时，保留 13 件解析成功记录及各自网格超限诊断，输出 0 对 ICP。
- `rg --files Platform` 后按 `[IO.File]::ReadAllLines(...)` 核对 21 个手写源码，最大 293 行，1000/1500 两档均无触发。`git diff --check` 及临时索引纳入新增文件后的差异检查通过。`git diff --quiet HEAD -- Demo` 和 Demo 状态检查通过，历史 `__pycache__` 由 Git 忽略。

### 未验证边界

- 模块范围停在任务级内部执行。阶段 2.2 的特征版本化持久化、阶段 2.3 的完整 D5 24 初值 ICP、阶段 2.4 的动态剪枝仍待实现。常驻队列调度、进程异常后的数据库任务回收、试用配额 CAS/迟到拦截和跨企业隔离仍按后续切片处理。
- 几何证据来自 Demo 与合成故障。`PLAT-DEF-001` 客户 STEP 数据、`PLAT-DEF-002` 完整业务并发条件与 `PLAT-DEF-003` Linux 和目标部署环境继续开放。
