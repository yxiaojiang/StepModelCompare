# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录。[归档与交接规则](../../README.md)

## 第 1 轮 — STEP-PLAT-005（2026-11-28）

分支 `feat/platform-v1`，差异基线 `ecc9474`。阶段 2 的审核结论与 `PLAT-003-P2-1` 核销证据保存在 [STEP-PLAT-004 归档](../2026-11-28_STEP-PLAT-004_D5精排与动态评分剪枝/INDEX.md)。阶段 3 首切片的代码和测试留在当前工作树，复核时需纳入新增文件。

### 改动与事务边界

| 文件 | 交付与边界 |
| --- | --- |
| `app/auth/{service,cache,rate_limit,tokens}.py`、`app/{api,main,config}.py` | Argon2id、Basic 与 JWT。每次鉴权重查数据库身份状态，成功缓存键含 HMAC 与当前版本。缓存与双维限流均按应用进程有界分配。失败计数以数据库列更新，触发五次连续失败锁定 15 分钟并递增版本。慢校验后按版本复核，避免覆盖并发的改密、锁定或改权。 |
| `app/enterprise.py`、`app/api.py` | 系统管理员可创建与查询企业，创建、查询与撤销邀请码。用量更新与企业绑定在同一事务，竞争失败的核销回滚，绑定递增账号版本。日志与列表均省去邀请码值。 |
| `app/repository/visibility.py`、`app/api.py` | 模型列表、详情、GLB、试用候选生成及结果发布共用 SQL 可见性范围。企业用户及企业管理员都只见本企已审核模型，试用仅见已审核公开模型，平台管理员可按企业收窄。拒绝会使基表谓词错绑的别名查询。企业管理员的审核权限待签认。 |
| `app/repository/quota.py`、`app/compare/trial_job.py` | PostgreSQL 事务锁序列化队列容量检查，任务和预占同事务写入。已领用任务可生成授权候选并调用既有精排。发布前锁任务、CAS 预占、复核并锁定候选、实扣及入库。回收与发布竞争时过期结果返回失败，重复完成保留已完成状态。失败与回收均按 CAS 释放一次。 |
| `requirements.txt`、`tests/test_database.py`、`tests/test_stage3_first_slice.py` | 新增 Argon2id 与 JWT 依赖和回归。复用已有 Alembic 表与列。 |

启用 HTTP 鉴权需设置不同且不少于 32 字节的 `STEP_PLATFORM_AUTH_PEPPER` 与 `STEP_PLATFORM_JWT_SECRET`。两项均未设置时鉴权入口返回 503。设置 `STEP_PLATFORM_MODEL_VAULT` 后可提供已授权的 GLB。部署需在 HTTPS 终止层与可信代理配置中保留真实客户端地址。

### 执行者实测

Windows Python 3.13.14，`argon2-cffi 25.1.0`、`PyJWT 2.15.1`。临时 PostgreSQL 18.7 仅监听回环地址，数据库用例按独立 schema 运行。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path 'Platform/backend').Path
$env:PYTHONIOENCODING = 'utf-8'
$env:OPENBLAS_NUM_THREADS = '1'
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56433/postgres'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m unittest discover -s Platform/backend/tests
& $py -m unittest Platform.backend.tests.test_stage3_first_slice Platform.backend.tests.test_database.PostgresTests.test_trial_ten_concurrent_admissions_and_late_completion -v
git diff --check ecc9474 -- Platform CRDocs Docs .claude/Plan.md .claude/SessionBrief.md .claude/contexts
git diff --exit-code 1e0fc76 -- Demo
```

- 全量 **59/59**，其中 PostgreSQL **11 项**。本机 10 路预占仅 5 笔，损坏 STEP 经真实解析入口拒绝并释放，模拟过期时间与回收并发交错时迟到结果零落库。邀请绑定双事务只提交一个。Alembic `check` 无漂移。
- 收紧别名隔离后定向复跑 **10/10**，含 SQLite/ASGI 路由、真实 PostgreSQL 10 路及损坏件/迟到门禁。SQLite 用例覆盖 4 种角色访问、越权详情/GLB、改密/锁定/绑定时的身份失效、双限流、校验错误不回显凭证，以及重复完成幂等。
- `pip check`、语法、差异和冻结目录检查通过。手写源码 46 个，最大 560 行，生产文件最大 326 行。1000/1500 行门禁通过。TestClient 的当前 Starlette 版本报告 httpx 弃用提示，测试正常完成。

### 未验证边界

- 当前 Worker 接线由调用方传入已领取任务与受信暂存路径。公开 Web/客户端对比接口、企业对比入口、规则配置和快照生成后台、常驻消费/定时回收、完整台账与公开注册均由对应切片完成。
- PostgreSQL 证据来自本机数据库及部分真实解析调用。目标环境的整机吞吐、混合上传与轮询、多进程鉴权限流、HTTPS 和客户真实样本继续按 [延期项](../../DEFERRED.md) 验收。
