# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录（最新在前）。超过 3 轮或切换任务前须先按 [README.md](README.md) 归档至 `archive/`。

## 第 1 轮 — STEP-PLAT-001（2026-11-27）

分支 `feat/platform-v1`，本轮为工作区交付。待独立代码审核。

### 改动文件清单

| 路径 | 本轮实施内容 |
| --- | --- |
| `Platform/backend/requirements.txt`、`tests/requirements.txt` | 后端运行依赖与 HTTP 自测依赖 |
| `Platform/backend/app/config.py`、`main.py`、`http_security.py` | 应用启动配置、数据库探活、URL 凭证拦截及平台、Uvicorn 日志脱敏 |
| `Platform/backend/app/db/models.py`、`session.py`、`queue.py` | 设计文档第 4.1 节的十张核心表、数据库引擎及 PostgreSQL 对比和入库队列抢占 |
| `Platform/backend/alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/0001_core_tables.py` | Alembic 迁移入口和独立、可逆的首版迁移 |
| `Platform/backend/app/geometry_engine/step_input.py`、`process_limits.py`、`step_reader.py`、`worker_runner.py` | STEP 输入校验、操作系统内存上限、XDE/拓扑/网格读取及常驻子进程池 |
| `Platform/backend/scripts/step_inventory.py` | 递归扫描 `.stp/.step`，报告 AP、XDE 材料、语义 PMI、闭合实体/开壳、原始面、网格三角片和计时 |
| `Platform/backend/tests/test_database.py`、`test_http.py`、`test_occt_pool.py` | 数据库迁移与实际 PostgreSQL 行锁、HTTP 安全、子进程和真实解析用例 |
| `CRDocs/IMPLEMENTATION.md`、`CRDocs/STATUS.md` | 执行记录和送审状态 |

### 实施说明

- 应用在启动生命周期读取 `STEP_PLATFORM_` 配置。`MAX_REFINE_K`、`COMPARE_JOB_TIMEOUT`、`QUEUE_WAIT_TIMEOUT`、`REAPER_TTL`、`SYNC_WAIT_TIMEOUT`、`DEFAULT_TOP_N`、`MAX_QUEUE_DEPTH`、`MAX_FILE_SIZE_BYTES`、`MAX_BATCH_FILES`、`MAX_BREP_FACES`、`MAX_MESH_TRIANGLES`、`ACTIVE_FEATURE_VERSION` 和 OCCT 进程数、轮换次数、单件超时、内存上限均可覆盖。排队和对比默认 60/120 秒，回收默认 211 秒，严格校验 `reaper_ttl > queue_wait_timeout + compare_job_timeout + 30`。队列深度 32 和网格三角片上限 100 万为本轮暂定值，待客户样本与部署环境验证。
- `GET /health` 对数据库执行 `SELECT 1`，正常返回 `200`，连接或查询失败返回 `503`。URL Query 凭证请求在进入路由前返回 `400 CREDENTIALS_IN_URL_FORBIDDEN`。Uvicorn 访问日志去掉全部查询串，应用及 Uvicorn 错误日志掩码凭证和异常细节，原始 ASGI 跟踪记录不进入日志。
- PostgreSQL 用一条 `UPDATE … RETURNING` 语句消费 `SELECT … FOR UPDATE SKIP LOCKED` 选中的唯一行，短事务提交后交给调用方处理。对比任务按 `created_at, id` 消费，并在 `queue_wait_timeout` 到期后停止领用。入库领用保留 `INDEXING` 状态，使用 `indexing_started_at` 区分领取情况。对比 `started_at` 和入库 `indexing_started_at` 是本轮为区分领取状态新增的字段。生效规则使用全局与企业两个部分唯一索引，且 `model_features(model_id, feature_version)` 在数据库中唯一。[PostgreSQL SELECT 行锁说明](https://www.postgresql.org/docs/current/sql-select.html)
- 生产消费者可用 `OcctPool.from_settings(settings)` 构建解析池。子进程按需启动并复用，超时直接强杀并回收，下一件自动建新进程，到轮换阈值主动退役。预检扩展名、文件大小和签名。子进程复验输入并限制原始 B-Rep 面数及可发布的三角片数。控制通道传单字节信号，结果和网格通过槽位临时文件传递，网格完整后才发布。使用 Windows Job Object 或 Linux `RLIMIT_AS`。对当前 OCCT 版本，转换前识别并拒绝分文件外部零件引用，记录 `ERR_STEP_EXTERNAL_REFERENCE`。[OCCT 8.0.1 STEPCAF 读取实现](https://github.com/Open-Cascade-SAS/OCCT/blob/V8_0_1/src/DataExchange/TKDESTEP/STEPCAFControl/STEPCAFControl_Reader.cxx) · [Windows Job Object 内存限制](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information) · [Python RLIMIT_AS](https://docs.python.org/3/library/resource.html#resource.RLIMIT_AS)
- 样本盘点按相对路径顺序对相邻可网格化样本调用一次 `igl.iterative_closest_point`，使用 100 采样点和最多 20 次迭代。计时范围只含该函数调用。OCCT 解析计时涵盖文件读取、单位换算、XDE 转换、属性与拓扑读取，另列网格化和进程端到端耗时。材料和 PMI 总体率以全部扫描文件为分母，专项 AP242 率以已识别的 AP242 文件数为分母。无法解析的协议归入 `UNKNOWN`，语义 PMI 统计尺寸、几何公差与基准对象。拓扑项可同时命中，报告同时列出原始 B-Rep 面和离散三角片。[libigl Python 绑定](https://libigl.github.io/libigl-python-bindings/api/igl/)

### 实测命令与结果

在仓库根目录运行。Windows 使用 Python 3.13.14。后端依赖安装在 `%TEMP%/step-plat-001-venv`，盘点复用 `Demo/.venv` 的 OCP 8.0.1.1.0、NumPy 2.5.5、SciPy 1.18.1、libigl 2.6.3。PostgreSQL 18.7 是执行者启动的本机临时实例，仅监听 `127.0.0.1:56431`，数据库 `step_plat_001`。集成测试各自创建隔离 schema。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path Platform/backend).Path
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56431/step_plat_001'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m alembic -c Platform/backend/alembic.ini upgrade head
& $py -m alembic -c Platform/backend/alembic.ini check
& $py -m alembic -c Platform/backend/alembic.ini upgrade head --sql
& $py -m unittest discover -s Platform/backend/tests -v
```

- `pip check` 无损坏依赖，`compileall` 成功，Alembic 在线升级通过，ORM 差异检查报告 `No new upgrade operations detected`。离线 PostgreSQL 建表 SQL 含 `JSONB`。
- 共 **19 项测试通过**，含 3 项真实 PostgreSQL 测试。PostgreSQL 上实测迁移回滚重建、JSONB、外键、特征版本唯一及单作用域生效规则。首行被独立事务持有时，8 路对比消费取得 4 条其余待领记录，无重复。持锁记录释放后可领用，等待超限记录保持待回收。另用独立事务实测入库抢占跳锁。SQLite 测试单独覆盖迁移及基础约束，消费入口明确拒绝 SQLite。
- 合成故障测试验证进程卡死强杀、半成品清理、进程退出及异常后的自动恢复、任务轮换和输入拒绝。Windows 子进程设 128 MiB 上限后，实际 256 MiB 系统分配被拒绝。真实 OCCT 用例覆盖 Demo 英寸几何以及自制 AP242 材料和语义尺寸、多闭合实体、开壳、外部零件引用正例。
- 本机以 `python -m uvicorn app.main:app --host 127.0.0.1 --port <临时端口>` 启动应用。普通查询返回 `200 {"status":"ok"}`，`/health?%74oken=测试值` 返回指定 `400`。实际 Uvicorn 访问日志保留请求路径和状态。正常日志级别及 `--log-level trace` 下的复核均通过，查询值和测试 Cookie 值未进入日志。

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$out = Join-Path $env:TEMP 'step-plat-001-demo-inventory.json'
& Demo/.venv/Scripts/python.exe Platform/backend/scripts/step_inventory.py Demo/samples/rehearsal --sample-label 'Demo 自制演示样例' --output $out
```

- Demo 自制样例共 14 件，13 件成功解析并离散为网格，1 件损坏样本因文件头失败留在分母。AP214 为 13 件，未知协议 1 件。单闭合实体 13 件，多闭合实体、开壳均为 0。材料和语义 PMI 均为 0/14，AP242 分母为 0。
- 原始 B-Rep 面数 9–13，三角片数 456–608。成功件 OCCT 解析 P50/P95 为 **29.0/41.8 ms**，网格 P50/P95 为 **15.0/19.7 ms**。12 次 igl 单次 ICP 完成，P50/P95 为 **19.7/27.4 ms**。
- 从系统临时目录以绝对路径运行脚本并传 `--max-brep-faces 2`，标准输出可解析为 JSON，得到 13 件面数超限和 1 件文件头失败。

```powershell
$files = rg --files Platform -g '*.py' -g '*.ts' -g '*.tsx' -g '*.vue' -g '*.js' -g '*.css' -g '!**/alembic/versions/**'
$counts = $files | ForEach-Object { [IO.File]::ReadAllLines((Resolve-Path -LiteralPath $_).Path).Length }
$counts | Measure-Object -Maximum
$counts | Where-Object { $_ -ge 1000 }
$counts | Where-Object { $_ -ge 1500 }
git diff --check
git diff --exit-code HEAD -- Demo
git status --short --untracked-files=all -- Demo
```

- 行数检查覆盖 18 个手写源码文件，最大 279 行（`tests/test_occt_pool.py`），1000 行和 1500 行两档均为 0。`git diff --check` 通过，另用临时 Git 索引对本轮新增 Platform 文件做相同检查。Demo 差异和状态检查无输出。

### 未验证边界

- **PLAT-DEF-001** 客户真实 STEP 尚未提供。AP 协议、PMI/材料覆盖率、跨 CAD 面片分裂、正例集和性能目标均待真实样本验证及签认。单次 igl 计时覆盖采样和精简的单次对齐调用，24 初值精排和任务级 P95 将在后续引擎阶段测量。
- **PLAT-DEF-002** 本机实测范围为 PostgreSQL 18.7 的迁移与队列行锁争抢。队列入口限深、进程故障后的数据库任务回收、试用 10 路配额 CAS、迟到结果拦截及跨企业全路径验收留待对应业务切片。
- **PLAT-DEF-003** Windows 本机通过内存限额与进程回收自测。Linux `RLIMIT_AS` 分支、客户目标服务账号下的 Windows Job Object 或 Linux 容器配置、网关日志和 HTTPS 均待目标部署环境验证。原生进程隔离的部署权限边界也在此核验。
