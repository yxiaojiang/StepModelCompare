# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核与整改交接。[归档与交接规则](../../README.md)

## 第 1 轮独立审核 — STEP-PLAT-007（2026-11-28）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，1 个 P2 非阻断建议）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `bcde2b0`，覆盖工作树全部已跟踪改动与新增文件（后端 `app/workflow_api.py`、`app/repository/{compare_history,visibility}.py`、`app/compare/{trial_job,worker}.py`、`app/db/model_features.py`、`tests/{test_compare_worker,test_database,test_workflow_api}.py`；前端 `Platform/frontend/` 全部源码、配置与测试）。审核者未修改任何业务代码。

### 重点复核结论

1. **任务 GLB 授权（通过）**
   - `workflow_api.py:256-266,303-315` 与 `compare_history.py:59-98` 为 `GET /api/v1/compare/jobs/{job_id}/query/glb` 和 `/models/{model_id}/glb` 强制执行 `current_user` 鉴权（缺失/失效返回 401）与 `_owned_job(connection, actor, job_id, "WEB")` 归属复核，仅对 `status == "DONE"` 的 Web 任务所有者开放。
   - `_owned_job` 实时回查数据库用户表（`role`、`enterprise_id`、`auth_version`、`is_active`、`locked_until`）并核对 `rule_version_snapshot` 中的身份快照；跨账号、跨企业、跨入口（`CLIENT_API` 任务）及身份变更均返回 404。
   - 候选预览调用 `visible_glb_query(actor, model_id, version, target_enterprise_id=job.enterprise_id)`，强制限定 `Model.status == "APPROVED"`、`CompareResult(job_id, model_id)` 成员身份与任务冻结的 `feature_version`；公开演示撤回（`is_public_demo=False`）后即时返回 404，全局 `active_feature_version` 切换后仍准确读取任务原版本 GLB。
   - 响应经 `vault_path` 边界校验后由 `FileResponse` 返回 `model/gltf-binary` 与 `Cache-Control: private, no-store`，Range 请求返回 `206 Partial Content` 且保留 `private, no-store`。
2. **三维叠加门禁（通过）**
   - `Platform/frontend/src/alignment.js:7-23` 严格校验 `candidate?.needs_review === false`、4×4 有限数值矩阵、末行 `[0, 0, 0, 1]`（拒绝透视）、前三行正交单位向量（拒绝缩放与剪切），以及 `Matrix4.set(...)` 行主序载入后 `determinant() ≈ +1`（拒绝镜像）。
   - `alignment_d5.py:133` 生成行主序 `scene_matrix = query.scene @ best.pose @ inv(candidate.scene)`（候选 GLB → 查询 GLB）；`viewport.js:100-121` 保持 `queryGroup` 单位矩阵、仅对 `candidateGroup` 施加 `mode.transform`，叠加时整体平移居中保持相对刚体姿态，并排时各自居中并共享同一 `camera`、`OrbitControls` 与 `radius = max(queryRadius, candidateRadius)` 实现同尺度联动。
   - `Viewer.jsx:77-84,119-120,141-146` 仅在两件预览均加载完成、矩阵过门禁且 WebGL 就绪时启用叠加，支持单模显隐且防止同时隐藏两件；`Workbench.jsx:9,209` 固定展示工程边界提示。
3. **任务生命周期与迟到/失败清理（通过）**
   - 前端 `api.js` 与 `Workbench.jsx` 仅在内存保存 Bearer 令牌（仅放 `Authorization` 头、`cache: "no-store"`、`redirect: "error"`），通过 `JSON.parse` `context.source` 保留 int64 `job_id`/`model_id` 原文；支持 202 `Retry-After` 轮询、非终态错误断点续查、选中回传防重与后端幂等、退出登录/401/到期定时器中止在途请求并释放 Three.js 几何与材质（`Viewer.jsx:10-18,48-52`）。
   - 后端 `trial_job.py:42-108` 仅对 `WEB` 任务在同次 OCCT 提取中生成查询 GLB；结算前任务处于 `RUNNING` 不可访问，`finally` 在 `not published` 时（含解析失败、超时、迟到结算返回 `False`、身份变化抛 `QuotaError`）清理未发布预览，且 `status != "DONE"` 兜底拒绝读取，不暴露未发布预览。
4. **Windows 回归（通过）**
   - `model_features.py:101-121` 的 `_vault_scope` 仅在 `is_relative_to` 边界比较时归一化 `\\?\UNC\...` 与 `\\?\X:\...` 扩展前缀，实际读写保留原始 `resolve()` 路径，继续拒绝绝对路径、根目录本身、同前缀兄弟目录（如 `vault-sibling`）及跨盘越界。
   - `worker.py:177-184` 的 `_supervise` 使用 `while process.exitcode is None:` 配合 `_check_deadline(deadline)` 循环等待，修复 Windows `process.join` 提前返回导致将超时误判为进程崩溃的问题，能够准确区分 `ERR_COMPARE_TIMEOUT` 与 `ERR_COMPARE_WORKER_EXIT`。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议）**：
  1. **`PLAT-007-P2-1`（OPEN｜Windows 高负载下 `WorkflowTests._poll` 25 秒硬超时偶发抖动）**
     - **位置**：`Platform/backend/tests/test_workflow_api.py:113-120`
     - **影响**：独立复跑全量 67 项测试时，`test_web_previews_follow_job_owner_visibility_and_feature_snapshot`（需完成 OCCT 查询提取 + GLB 导出 + Windows `spawn` 启动 `libigl` D5 精排）在第 1 次全量跑中因超过 `_poll` 硬编码 25 秒上限失败 1 次，随后单独复跑通过（22.293 秒）；属于测试等待阈值偏紧而非生产代码缺陷（生产 `compare_job_timeout` 默认 120 秒），建议后续切片适当放宽 `_poll` 超时或区分 CI 负载阈值。

### 独立验证结果与证据分级

审核者独立复跑以下验证（Windows，Python 3.13.14，Node.js v24.18.0 / npm 11.16.0，本机临时 PostgreSQL 18.7 端口 `56436`/`56437`，测毕即停库清理）：

- **本机真实 PostgreSQL 18.7 + ASGI 后端测试**：
  - 全量 `unittest discover -s Platform/backend/tests -v`（端口 `56436`）：**66/67 通过**（131.060s），其中 `test_web_previews_follow_job_owner_visibility_and_feature_snapshot` 触发 25s 轮询超时；随即单独复跑该用例（端口 `56437`）：**1/1 通过**（22.293s）。67 项用例（含 17 项 PostgreSQL 真实用例、Windows 扩展路径边界测试与 Worker 早返回回归）及 Alembic 元数据检查均获独立实测通过。
  - `pip check`、`compileall -q Platform/backend` 通过。
- **前端测试与构建（Node.js v24.18.0）**：
  - `Platform/frontend` 下 `npm test`：**3/3 通过**（227.66ms，覆盖行主序刚体变换、待复核/非法矩阵门禁、int64 编号与 Header 鉴权）。
  - `npm run build`：Vite 8.4.0 构建成功（332ms，产出 `dist/` 与 `GLTFLoader`/`viewport` 拆包）。
- **工程门禁**：`git diff --check bcde2b0` 通过；`git diff --exit-code demo-v0.3.1 -- Demo` 退出码 `0`（`Demo/` 严格冻结）；手写源码最大 585 行（`tests/test_database.py`），生产源码最大 344 行（`workbench.css`）/ 340 行（`workflow_api.py`），远低于 1000/1500 行门禁，中文注释规范与轻量依赖约束合规。
- **未实测边界**：
  - 本轮独立审核未启动实时浏览器做 WebGL 交互点击（执行者已在本地浏览器用 `Demo/` 自制英寸件完成联调）；真实浏览器/GPU 兼容矩阵、客户 20–50 件真实 STEP 样本、目标部署环境 HTTPS/可信代理及查询原件/GLB 保留与孤立文件策略，继续由 `DEFERRED.md`（`PLAT-DEF-001/002/003`）跟踪。
