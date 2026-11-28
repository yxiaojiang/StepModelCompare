# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录。[归档与交接规则](../../README.md)

## 第 1 轮 — STEP-PLAT-006（2026-11-28）

分支 `feat/platform-v1`，差异基线 `6f88188`。上轮 0 P0、0 P1 的独立通过记录见 [STEP-PLAT-005 归档](../2026-11-28_STEP-PLAT-005_鉴权隔离与试用配额/INDEX.md)。

### 实现与复核入口

| 改动 | 事务与边界 |
| --- | --- |
| `app/uploads.py`、`app/repository/model_intake.py`、`app/workflow_api.py` | 客户端批量上传进入 PENDING，系统管理员直传或审核批准进入 INDEXING，常驻消费者转 APPROVED 或 PARSE_FAILED。驳回进入 REJECTED 并保存原因。上传按企业事务锁串行化容量和 MD5 去重，失败项可重提，跨企业文件和特征缓存独立。限制单文件、批次及整个 multipart 请求，支持 Header 或表单身份。 |
| `app/repository/rules.py` | 创建不可变规则版本并记录审计，同一作用域原子激活。企业规则优先，缺少企业覆盖时回退全局。任务入队固定模板内容、规则版本及全局特征版本。全局规则未配置且无可用覆盖时返回 503。 |
| `app/repository/quota.py`、`app/compare/trial_job.py`、`app/repository/compare_history.py` | 企业、管理员与试用共用队列容量事务锁，试用另做 5 次 CAS 预占。进行中返回 202 与 Location，窗口内完成返回 200，损坏件返回 422。持久化任务只允许所属账号按对应来源轮询，候选生成、发布和结果读取重复施加隔离条件。选中回传按任务与模型幂等。 |
| `app/db/queue.py`、`app/queue_runtime.py`、`app/main.py` | PostgreSQL `SKIP LOCKED` 领取持久化查询或待索引模型。ASGI 生命周期管理对比、入库、回收三条常驻循环及 OCCT 池。定时处理超期预占、排队超时和中断任务。 |
| `alembic/versions/0003_compare_source.py` | 增加可空查询原件路径，保留既有任务并可降级。常驻消费者只领取含存储键的对比任务。 |
| `app/api.py`、`app/config.py` | 修复 `PLAT-005-P2-1`。邀请码绑定使用独立的 IP/账号窗口，超限 429。既有和新增列表的游标先按 int64 校验，越界固定 422。台账接口返回六项字段，缓存键不外发。 |

启用业务接口需先迁移至 head、设置 `STEP_PLATFORM_MODEL_VAULT` 与既有鉴权密钥、创建并激活首条全局规则。管理员通过 `POST /api/v1/admin/rules` 创建模板，通过 `POST /api/v1/admin/rules/{rule_id}/activate` 激活。默认配置在 PostgreSQL 加存储卷时随服务启动调度。分开部署时需使消费者与 API 进程读写同一存储卷。Web 对比默认异步，客户端默认混合等待，Web 可显式选混合等待。

### 自测命令

Windows Python 3.13.14，本机临时 PostgreSQL 18.7，`python-multipart 0.0.34`。PostgreSQL 用例在独立 schema 中运行。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path 'Platform/backend').Path
$env:PYTHONIOENCODING = 'utf-8'
$env:OPENBLAS_NUM_THREADS = '1'
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56434/postgres'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m unittest discover -s Platform/backend/tests
git diff --check 6f88188 -- Platform CRDocs Docs .claude/Plan.md .claude/SessionBrief.md .claude/contexts
git diff --exit-code 1e0fc76 -- Demo
```

- 全量 **64/64 通过**（87.754 秒），其中 **16 项使用本机 PostgreSQL 18.7**。Alembic 升降级及 `check` 通过，无元数据漂移。
- 新增 PostgreSQL / ASGI 用例 **5 项**，覆盖五态、同企并发去重、跨企同件独立入库、租户规则覆盖和任务快照、200/202、轮询越权与公开撤回、选中幂等、分块上传 413、邀请码限流 429 及游标越界 422。双入口 10 路并发返回 5 个 202 和 5 个 403，混合身份容量门禁返回 503。定时回收同时验证已过期试用任务与中断索引。
- `pip check`、`compileall`、已跟踪及新增文件差异检查、归档链接和 `Demo/` 冻结检查通过。手写源码 53 个，最大 560 行，生产文件最大 326 行，低于 1000/1500 行门槛。

### 验证边界

- 本机 ASGI 测试直接访问实际路由，使用真实 PostgreSQL、OCCT/libigl 和 Demo 英寸件。十路并发用例经两种 HTTP 路由向同一账号预占配额。迟到发布竞态由 PostgreSQL 事务用例控制过期时间，定时调度另由 ASGI 生命周期联调。
- 管理后台交付 REST 接口及 OpenAPI，专用可视化规则配置页、注册防刷、客户端调用样例及部署交付按后续计划。审核权限目前仅系统管理员。
- 客户真实 STEP、目标 HTTPS/可信代理、多实例限流及整机并发仍按 [延期事项](../../DEFERRED.md) 验收。查询原件保存在独立目录，目标存储卷的保留期限、容量告警和孤立文件处置仍待运维签认。
