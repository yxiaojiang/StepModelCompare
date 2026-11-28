# 执行者实施与自测交接记录

## 第 1 轮 — STEP-PLAT-007（2026-11-28）

分支 `feat/platform-v1`，基线 `bcde2b0`。[上一轮归档](../2026-11-28_STEP-PLAT-006_五态入库与对比调度/INDEX.md)。

### 实现与复核入口

| 改动 | 契约与边界 |
| --- | --- |
| [React 工作台](../../../Platform/frontend/README.md) | Bearer 令牌仅驻留页面内存。提交后按 `Retry-After` 轮询，候选点击触发幂等选中，支持 D1–D7 分项、缺失提示及精排截断提示。退出清理正在进行的请求、结果与模型。 |
| [三维视口](../../../Platform/frontend/src/viewport.js) · [矩阵门禁](../../../Platform/frontend/src/alignment.js) | Three.js 本地打包，双视口共享同一相机与尺度。叠加仅在两个模型成功加载、结果明确未待复核且刚体矩阵可用时开放。候选使用后端场景矩阵，拒绝镜像、缩放、剪切与透视。两件可单独显隐；工程边界文案固定展示。 |
| [任务预览路由](../../../Platform/backend/app/workflow_api.py) · [任务授权](../../../Platform/backend/app/repository/compare_history.py) | `GET /api/v1/compare/jobs/{job_id}/query/glb` 与 `GET /api/v1/compare/jobs/{job_id}/models/{model_id}/glb` 复用任务所有者、来源与实时账号状态检查，仅发布 `DONE` 的 Web 任务。候选再核对结果成员身份、当前可见性和任务冻结的特征版本。GLB 响应标记 `private, no-store`，支持 Range。 |
| [查询 Worker](../../../Platform/backend/app/compare/worker.py) · [任务结算](../../../Platform/backend/app/compare/trial_job.py) | 同次 OCCT 提取生成查询网格和 GLB，在成功结算前禁止访问。失败或结算被拦截时清理未发布预览。Windows 定时等待提前返回时，仍存活的子进程继续等待至退出或截止，避免将超时误判为崩溃。 |
| [存储路径校验](../../../Platform/backend/app/db/model_features.py) | Windows 首次并发创建目录时可能返回普通和扩展前缀两种表示。只在比较时统一等价前缀，读写使用原始解析结果且继续拒绝越界。 |

### 验证命令与结果

- 后端 Windows Python 3.13.14，设置 `PYTHONPATH=Platform/backend`、`OPENBLAS_NUM_THREADS=1`，将 `STEP_PLATFORM_TEST_POSTGRES_URL` 与 `STEP_PLATFORM_DATABASE_URL` 指向同一本机临时 PostgreSQL 18.7。执行 `python -m pip check`、`python -m compileall -q Platform/backend`、`python -m unittest discover -s Platform/backend/tests`。最终 **67/67** 通过（107.421 秒），包括 17 项 PostgreSQL 用例及 Alembic 迁移检查。Starlette TestClient 给出既有 HTTPX 弃用提示。
- 真实 PostgreSQL 和 ASGI 回归覆盖任务所有者预览、越权与客户端任务拒绝、试用公开撤回、特征指针切换后仍读原版本、Range、失败任务不可读和选中回传。另用 100 个新临时目录各并发 10 路调用 `save_step`，共 1000 次首次目录写入通过。
- `Platform/frontend` 下用 Node.js 24 执行 `npm ci`、`npm test`、`npm run build` 均通过。3 项前端用例覆盖矩阵方向、待复核与无效矩阵门禁、int64 ID 和请求头鉴权。
- 浏览器通过 Vite 代理连接本机 FastAPI、PostgreSQL 和真实 OCCT/libigl。用 Demo 自制英寸件验证登录、上传轮询、100 分候选、分项 D5、双视口联动旋转、叠加和单模显隐、选中回写与退出清理。
- `git diff --check`、新增前端文件空白检查和 `git diff --exit-code demo-v0.3.1 -- Demo/` 通过。检查 64 个手写源文件，最大 585 行。

### 验证边界

- 已完成的旧 Web 任务没有查询件 GLB。新提交的 Web 任务生成独立预览，客户端任务继续沿用原合同。无需数据库迁移或补写历史任务。
- 浏览器验证使用本机浏览器与自制夹具。客户真实样本、目标 HTTPS/代理和跨浏览器/GPU 兼容矩阵待验收。
- 查询件 GLB 随原件存放。目标存储卷保留、容量告警及进程强制终止后孤立产物清理随 [PLAT-DEF-003](../../DEFERRED.md) 签认。
