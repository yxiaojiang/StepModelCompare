# 执行者实施与自测交接记录

## STEP-PLAT-008 第 1 轮 · 2026-11-28

实施于 `feat/platform-v1` 基线 `c874ae5`，待独立审核。

### 实施内容

- [认证、企业与台账 API](../../../Platform/backend/app/api.py) 新增系统管理员五态清单 `GET /api/v1/admin/models`，按状态、企业、名称、MD5、上传人和 int64 游标过滤，每页最多 100 条。返回 MD5、名称、上传及入库时间、上传人、当前版本特征和选中次数，附诊断原因、公开演示标记。企业账号走 `GET /api/v1/enterprise/models`，五态台账只按登录身份的企业限定。通用列表、详情、预览与候选沿用原有可见性守卫。邀请码绑定在读取邀请码前校验试用角色。
- [入库仓储](../../../Platform/backend/app/repository/model_intake.py) 在管理员直传建档事务内写 `MODEL_DIRECT_UPLOADED`，跳过的重复件不产生该事件。[审核路由](../../../Platform/backend/app/workflow_api.py) 新增 `POST /api/v1/admin/models/batch-review`，一批限 100 件。每件复用既有的状态更新、实时权限校验和审计事务，按提交顺序返回状态或 409 冲突。通过可标记公开演示，驳回须填写非空原因。驳回提交后原件清理失败时仍返回已提交状态，并附清理提示。
- `GET /api/v1/admin/audit-logs` 仅向系统管理员分页返回事件编号、操作者、操作、目标和时间。数据库查询排除历史详情和 IP 字段，凭证及邀请码明文不会随审计记录返回。邀请码列表沿用已有的安全字段投影。
- [平台入口](../../../Platform/frontend/src/Workbench.jsx) 共用页面内存登录会话，按角色挂载企业台账、系统管理或试用绑定视图。路由切换保留当前工作台任务，邀请码绑定后清除当前登录态，重新登录后启用企业权限。
- [模型台账](../../../Platform/frontend/src/ModelLedger.jsx)、[规则配置](../../../Platform/frontend/src/RulesPage.jsx) 与[企业、邀请码及审计](../../../Platform/frontend/src/Administration.jsx) 已接通后端。规则以可编辑的权重初值开始，提交时校验七维合计 100%、正数阈值与精排上限，保存未激活版本后可一键激活。载入旧版本时保留其 D4 子权重和独立安全门禁配置。
- [真实数据库与 ASGI 用例](../../../Platform/backend/tests/test_workflow_api.py) 新增四角色权限矩阵，以及台账筛选、int64 游标、混合审核结果、并发同件审核和审计脱敏回归。检索用例为待审核、解析失败件补入就绪特征和公开标记，再经真实 Worker 对比验证两者在四角色下均零命中。[前端用例](../../../Platform/frontend/src/platform-forms.test.js) 覆盖权重、审核载荷、int64 与角色视图门禁。

### 执行证据

Windows Python 3.13 虚拟环境使用现有依赖。本机独立 PostgreSQL 实例通过 psycopg 查询 `SHOW server_version` 返回 `18.7`，仅监听 `127.0.0.1:56438`。各集成用例使用独立 schema，Alembic 升至 head。

```powershell
$env:PYTHONPATH = 'Platform/backend'
$env:OPENBLAS_NUM_THREADS = '1'
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56438/postgres'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
$python = "$env:TEMP/step-plat-001-venv/Scripts/python.exe"
& $python -m pip check
& $python -m compileall -q Platform/backend
& $python -m unittest discover -s Platform/backend/tests
npm.cmd test --prefix Platform/frontend
npm.cmd run build --prefix Platform/frontend
git diff --check
git diff --exit-code demo-v0.3.1 -- Demo/
```

- Python 依赖与语法检查通过。全量后端 **69/69 通过**，无跳过，耗时 155.971 秒。两项新增真实 PostgreSQL 与 ASGI 用例单独运行亦通过。Alembic 校验无模型迁移差异。全量运行出现既有 Starlette TestClient 对 HTTPX 的弃用提示。
- Node 24.18.0 / npm 11.16.0 下前端 **6/6 通过**，Vite 8.4.0 构建通过。手写源文件统计覆盖 71 个文件，全工程最长 585 行，前端最长 344 行，均通过 1000/1500 行门禁。工作区补丁通过空白检查，`Demo/` 对标签 `demo-v0.3.1` 无差异。
- 本地联调复用 STEP-PLAT-007 的临时 Uvicorn 夹具脚本（以 `WorkflowTests.setUp()` 创建隔离 schema、文件库和测试账号），后端监听 `127.0.0.1:8017`。设置 `STEP_PLATFORM_API_TARGET=http://127.0.0.1:8017` 后，执行 `npm.cmd run preview --prefix Platform/frontend -- --port 4188 --strictPort`，在 Codex 内置浏览器打开 `http://127.0.0.1:4188/` 验证生产构建。
- 浏览器实测企业上传进入待审、管理员按状态筛选并批量驳回及通过（勾选公开演示）、企业创建和管理员直传入库、101% 拒绝保存而 100% 可保存并激活企业规则、邀请码创建与试用绑定、重新登录后的本企业台账、停用邀请码、只读审计事件、刷新清除内存会话。用 Demo 夹具完成三维工作台查询，切换导航再返回后保留任务结果。

### 延期核销与未验证边界

- `PLAT-006-P2-1` 的管理员直传审计项已实现并实测。表单上传的代理层限流、超时与 Starlette 升级兼容继续由 [有效延期项](../../DEFERRED.md) 跟踪。
- `PLAT-007-P2-1` 的 `WorkflowTests._poll` 硬超时由 25 秒放宽至 45 秒，已在全量 Windows 回归中验证。
- 客户真实 STEP 样本、目标部署环境的整机压力、HTTPS 与反向代理、目标浏览器和 GPU 兼容仍待验证。浏览器冒烟覆盖系统管理员、企业普通用户和试用用户，企业管理员由真实 PostgreSQL 与 ASGI 权限矩阵覆盖。
