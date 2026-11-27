# STEP-SIM-004 技术架构与代码逻辑修正：并发配额守卫、分层进程隔离、鉴权缓存与版本化特征

**STEP-SIM-005 · 第 2 轮 · 2026-11-27 · 提案方 Antigravity · 待独立审核**

## 要决定的问题

在 `STEP-SIM-004` 正式多企业平台（单人独立开发）进入代码实现前，针对 [详细开发设计文档](../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md) 和 [详细开发计划](../Docs/STEP模型相似度对比与检索系统详细开发计划.md) 中的技术框架、并发状态机、进程隔离边界与依赖选型进行工程级复查与收口。

第 1 轮独立审核（Neon）给出 `CHANGES_REQUESTED` 并提出 `S5-1` 至 `S5-4` 共 4 项代码逻辑与架构修正要求，同时指示按单人独立开发视角精简掉商务报价重排与双人 A/B 周表。本轮已全盘采纳并完成修订，请复核：

1. **配额状态机与迟到结果拦截（`S5-1`）**：引入涵盖解析与全部至多 $K$ 次精排的任务级总硬超时 `compare_job_timeout`，将 `reaper_ttl` 公式修正为 `> 队列最大等待时间 + compare_job_timeout + 安全缓冲`；任务完成写结果前在同一事务中对 `trial_reservations` 执行 `status='RESERVED' → 'COMMITTED'` 的 CAS 更新，若 `rowcount == 0`（已被超时回收）则严禁写入/返回比对结果，直接标 `FAILED (QUOTA_EXPIRED)`，彻底堵住“迟到任务免费拿结果”漏洞。
2. **短时密码校验缓存与即时失效（`S5-2`）**：仅缓存校验成功项；缓存键改为 `HMAC-SHA256(server_pepper, f"{user_id}:{auth_version}:{plaintext_password}")`；`users` 表新增 `auth_version` 整型列，改密、禁用、触发锁定、变更企业或角色均原子递增 `auth_version` 使旧缓存立即失效；缓存未命中进入 Argon2id 慢哈希前叠加 **IP 维度 + 账号维度**双限流。
3. **OCCT / ICP 两层隔离、真实依赖修正与 PostgreSQL 队列收口（`S5-3`）**：按 `Demo/src/geometry.py` 与 `alignment.py` 真实代码划分边界——仅 STEP 解析/XDE 提取/网格离散与 GLB 导出走常驻受控 OCCT 子进程池（硬超时强杀重建 + 每 $N$ 任务轮换防 C++ 内存泄漏）；至多 $K$ 次 ICP 对齐与 AABB 双向距离基于 `numpy + scipy + libigl (igl)` 在任务级 Worker 进程内复用查询件 `igl.AABB` 树顺序执行，受 `compare_job_timeout` 管控，不逐次起子进程；修正几何依赖为 `cadquery-ocp-novtk` + `numpy` + `scipy` + `libigl`（剔除误写的 `trimesh`）；单人开发任务队列直接收口为 **PostgreSQL 任务表（`SELECT ... FOR UPDATE SKIP LOCKED`）+ 常驻 Python Worker 进程池**，首期不引入 Redis / Celery。
4. **`feature_version` 版本隔离防混算（`S5-4`）**：将特征存储改为按版本隔离的 `model_features(model_id, feature_version, feature_payload, ...)` 表（`UNIQUE(model_id, feature_version)`），系统维护全局 `active_feature_version`；算法升级时后台异步为全库已入库模型提取新版本特征，全部就绪校验通过后再原子切换 `active_feature_version`，切换前在线对比全程统一使用旧版本提取器与旧版本特征，杜绝新旧混算导致分数漂移。
5. **混合同步对比、有界队列与验收量化签认**：保持第 1 轮已确认的混合同步模式（限时等待 + 超时转 `202` 轮询）、有界队列满额直接返回 `503 QUEUE_FULL`、阶段 1 补齐预登记正例集/找回率门槛/性能目标签认及 D3/D1 数据可用性体检说明。

## 建议决定

### 1. 无邀请码 5 次配额：CAS 状态守卫、迟到结果拦截与 `reaper_ttl` 公式（落实 `S5-1`）

1. **任务级总硬超时与回收阈值公式**：
   - 定义单次对比任务的端到端计算硬超时为 `compare_job_timeout`（默认建议 `120s`，阶段 1 实测后冻结），涵盖：**查询件 OCCT 子进程解析与网格离散 + 候选粗筛 + 至多 $K$ 次 `igl` ICP 精排**。
   - 定义队列最大排队等待上限为 `queue_wait_timeout`（默认建议 `60s`）。
   - 超时回收器阈值 `reaper_ttl` 必须满足硬性不等式：
     $$\text{reaper\_ttl} > \text{queue\_wait\_timeout} + \text{compare\_job\_timeout} + \text{safety\_buffer (30s)}$$
     （例如 `60s + 120s + 30s = 210s`），确保任何在合法生命周期内的任务绝不会被提前误回收。
2. **四态预占单（`trial_reservations`）原子流转与迟到结果拦截**：
   - **入口预占（`RESERVED`）**：队列未满（`< max_queue_depth`）时，在单短事务内执行：
     ```sql
     UPDATE users
     SET in_flight_count = in_flight_count + 1
     WHERE id = :uid AND is_trial = TRUE AND (used_count + in_flight_count) < 5;
     ```
     `rowcount == 1` 时同事务插入 `trial_reservations(id=:rid, user_id=:uid, job_id=:jid, status='RESERVED', expires_at=now + reaper_ttl)`。
   - **成功实扣与迟到结果拦截（`RESERVED → COMMITTED`）**：Worker 完成比对准备落库结果时，必须在**写入 `compare_results` 与更新 `compare_jobs.status = 'DONE'` 的同一数据库事务中**先执行 CAS：
     ```sql
     UPDATE trial_reservations
     SET status = 'COMMITTED', updated_at = NOW()
     WHERE id = :rid AND status = 'RESERVED';
     ```
     - 若 `rowcount == 1`：同事务执行 `UPDATE users SET used_count = used_count + 1, in_flight_count = GREATEST(in_flight_count - 1, 0) WHERE id = :uid`，并写入 `compare_results`、将 `compare_jobs` 置为 `DONE`；
     - 若 `rowcount == 0`（说明该预占单已被回收器置为 `EXPIRED` 或已被异常回滚）：**严禁写入或向客户端暴露 `compare_results`**，同事务直接将 `compare_jobs` 置为 `status = 'FAILED', error_code = 'QUOTA_EXPIRED'`，阻断“超时回收后白嫖结果”路径。
   - **失败/损坏回滚（`RESERVED → RELEASED`）**：查询件非合法 STEP、OCCT 解析失败或任务异常中止时，在同一事务内执行：
     ```sql
     UPDATE trial_reservations
     SET status = 'RELEASED', updated_at = NOW()
     WHERE id = :rid AND status = 'RESERVED';
     ```
     **仅当 `rowcount == 1` 时**，才同事务执行 `UPDATE users SET in_flight_count = GREATEST(in_flight_count - 1, 0) WHERE id = :uid`。
   - **超时回收器（`RESERVED → EXPIRED`）**：后台定时扫描 `status = 'RESERVED' AND expires_at < NOW()`，逐条在短事务内执行 `WHERE id = :rid AND status = 'RESERVED'` 更新为 `EXPIRED`；仅当 `rowcount == 1` 时扣减 `in_flight_count = GREATEST(in_flight_count - 1, 0)`。

### 2. 客户端鉴权：HMAC 短时缓存、`auth_version` 即时失效与双维限流（落实 `S5-2`）

1. **推荐路径与兼容路径**：客户端 SDK 默认先调 `POST /api/v1/auth/token` 换取短期 JWT（只跑一次 Argon2id）；为兼容客户“接口直接带用户名和密码调用”习惯，在 HTTPS Header/Body 传密路径上引入受控内存短缓存（TTL 默认 `300s`）。
2. **缓存键构造与只缓成功原则**：
   - `users` 表新增整型列 `auth_version INTEGER NOT NULL DEFAULT 1`。
   - 请求到达时，先按 `username` 查出用户行（索引主键级毫秒查询），校验 `is_active == True` 且未处于 `locked_until > NOW()` 锁定态。
   - 用服务端启动加载的高熵密钥 `server_pepper` 计算快哈希缓存键：
     $$K_{\text{auth}} = \text{HMAC-SHA256}\big(\text{server\_pepper},\ \texttt{f"\{user.id\}:\{user.auth\_version\}:\{plaintext\_password\}"}\big)$$
   - 若 $K_{\text{auth}}$ 命中缓存，直接通过鉴权（耗时 `< 0.1 ms`，不存明文密码）；
   - 若未命中，先经过 **IP 维度 + 账号维度**双限流桶（例如单 IP 每分钟最多 15 次慢哈希、单账号每分钟最多 5 次慢哈希，超限直接返回 `429 TOO_MANY_AUTH_ATTEMPTS`），通过后才执行 `argon2.verify(user.password_hash, plaintext_password)`；
   - **仅当 Argon2id 校验成功时**将 $K_{\text{auth}}$ 写入缓存（TTL `300s`）；校验失败**绝不写入缓存**，并递增 `failed_login_count`（连续失败 5 次锁定 15 分钟，同时 `auth_version += 1`）。
3. **状态变更即时失效**：凡发生**修改密码、管理员禁用账号、连续失败触发锁定、解绑/改绑企业或变更角色**，同一数据库事务内强制执行 `auth_version = auth_version + 1`，旧缓存键因版本号不匹配在 `0 ms` 内全部自然失效，无 5 分钟权限滞后窗口。

### 3. 两层进程隔离、`cadquery-ocp-novtk + libigl` 真实依赖与 PostgreSQL 队列收口（落实 `S5-3`）

1. **真实几何依赖修正**：
   - 对照 `Demo/src/geometry.py`、`Demo/src/alignment.py` 与 `Demo/.venv` 实测环境，将平台几何与科学计算依赖明确修正为：
     - **`cadquery-ocp-novtk`（8.0.1+，导入名 `OCP`）**：负责 STEP 文件读取（`STEPCAFControl_Reader` / `STEPControl_Reader`）、XDE 属性/材料/图层/颜色/PMI 提取、B-Rep 拓扑遍历、几何曲面类型识别（`BRepAdaptor_Surface`）、质量属性（`BRepGProp`）及三角网格离散（`BRepMesh_IncrementalMesh`）；
     - **`numpy` + `scipy`（`scipy.spatial.transform.Rotation`）**：负责主轴惯性张量、24 组右手系初值生成、点到面最小二乘（`np.linalg.lstsq`）与 SVD 回退配准、姿态角/位移去重与歧义计算；
     - **`libigl`（导入名 `igl`）**：负责 `igl.AABB` 加速树构建与 `squared_distance` 双向表面最近点距离采样。
   - 从设计文档技术栈中**移除未实际使用的 `pythonocc-core` 与 `trimesh` 表述**。
2. **OCCT 与 ICP 两层进程隔离模型**：
   - **第一层：常驻受控 OCCT 解析子进程池（`occt_worker_pool`）**：
     - 仅在“新模型入库 `INDEXING` 提取 `D1/D2/D3/D4/D6/D7` + 离散网格 `.npz` + 导出 `.glb`”以及“对比查询件上传后的首次解析与网格离散”时调用；
     - 每个子进程设置操作系统级内存上限（Linux `RLIMIT_AS`/cgroup，Windows Job Object）、最大面片熔断（如 `50,000` 面）与单件解析硬超时（默认 `30s–60s`）；
     - 超时或崩溃由父进程强杀并拉起新进程；正常跑满 $N$（如 `50`）个模型后主动优雅重启子进程，回收 OCCT C++ 堆碎片。
   - **第二层：对比任务 Worker 进程内复用 `igl.AABB` 树批量精排（不逐次起进程）**：
     - 查询件经 OCCT 子进程解析出特征 JSON 与采样网格 `.npz` 后，对比任务 Worker 在本进程内存中一次性构建查询件的 `igl.AABB` 树；
     - 经有效权重上界剪枝筛出的至多 $K$ 个候选件，直接从磁盘读取已缓存的候选 `.npz` 采样点云/网格，在任务 Worker 进程内顺序执行 24 初值 + Trimmed ICP 与双向距离评估；
     - 整场对比任务受 `compare_job_timeout` 硬超时保护（超时由 Worker 管理器终止该任务进程并触发配额回滚），避免为 $K$ 次 ICP 重复创建进程的开销与序列化瓶颈。
3. **单人可维护的任务队列收口（去 Redis / Celery）**：
   - 首期**不引入 Redis 与 Celery/RQ**，消除多组件运维与双写状态同步负担；
   - 统一采用 **PostgreSQL 任务队列（基于 `models` 表的 `INDEXING` 状态与 `compare_jobs` 表的 `QUEUED` 状态，使用 `SELECT ... FROM compare_jobs WHERE status = 'QUEUED' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1` 抢占任务）+ 独立常驻 Python Worker 进程池**。

### 4. `feature_version` 版本隔离存储与原子切换防混算（落实 `S5-4`）

1. **版本隔离表结构**：
   - 将特征数据从 `models` 表字段拆出（或辅以主表当前版本冗余指针），建立独立的版本化特征表 `model_features`：
     - 核心字段：`id`, `model_id` (FK), `feature_version` (如 `"1.0.0"`), `feature_payload` (JSONB), `sample_points_path`, `glb_path`, `extracted_at`, `status` (`READY` / `FAILED`)；
     - 唯一约束：`UNIQUE(model_id, feature_version)`。
   - 系统配置表（或 `similarity_rules`）维护当前全局生效的 `active_feature_version`（初值 `"1.0.0"`）。
2. **平滑升级与零混算保证**：
   - 当算法升级至 `"1.1.0"` 时，后台启动低优先级批量重提取任务，遍历所有 `status = 'APPROVED'` 的模型，用 `"1.1.0"` 提取器写入 `model_features(model_id, feature_version='1.1.0', ...)`；
   - **在全库 `APPROVED` 模型的 `"1.1.0"` 特征 100% 就绪之前**：`active_feature_version` 仍保持 `"1.0.0"`，在线对比查询件同样调用 `"1.0.0"` 提取器，并仅关联查询 `model_features.feature_version = '1.0.0'` 的候选特征；
   - **原子切换**：待全库 `"1.1.0"` 提取完成并通过一致性校验后，单事务更新 `active_feature_version = '1.1.0'`，此后新发起的对比任务统一使用 `"1.1.0"` 提取器与 `"1.1.0"` 候选特征，**全程杜绝新旧版本特征混算**。

### 5. 混合同步对比、有界队列、验收量化与单人开发顺序

1. **有界队列与混合同步对比**：
   - 提交对比请求前先检查 `SELECT COUNT(*) FROM compare_jobs WHERE status IN ('QUEUED', 'RUNNING')`；若达到 `max_queue_depth`（阶段 1 冻结），在预占配额前直接返回 `503 QUEUE_FULL`；
   - 客户端 `POST /api/v1/client/compare` 默认采用**混合同步模式**：服务端入队后异步挂起等待结果至 `sync_wait_timeout`（默认建议 `25s–30s`）；若期间完成则直接返回 `200 OK` 及完整比对结果；若超时仍未完成则返回 `202 Accepted`（含 `job_id` 与轮询 URL），客户端通过 `GET /api/v1/client/compare/{job_id}` 获取结果。
2. **阶段 1 验收量化与数据可用性签认**：
   - 阶段 1 样本盘点报告单列“AP242 语义 PMI 可读率”与“材料填写率”，向客户书面说明缺项时 `D3 → D7` 权重承接的实际触发比例；
   - 阶段 1 冻结清单补齐：**预登记正例集**（客户提供或确认 20–30 组查询-历史正例对；不足时验收第 3 项只统计四类归因不设硬门槛）、**Top-5 找回率门槛**（阶段 2 出口跑正例基线后签认，占位建议 `>= 80%`）、**同步对比响应目标**（基于冻结服务器配置与库规模，占位建议 `P95 <= 30s`）。
3. **跨企业缓存与开工治理**：
   - 首期不同企业上传相同 MD5 模型时各自独立提取特征与 GLB，不跨企业共享底层缓存，消除响应耗时侧信道；
   - 代码在 `Platform/` 目录、`feat/platform-v1` 分支下按单人模块依赖顺序推进；每个阶段放行前必须跑通对应的 PostgreSQL 并发/越权/四类难例自动化回归测试。

## 证据

- `Demo/src/geometry.py`（L1–22）与 `Demo/src/alignment.py`（L1–60）实物核对：`geometry.py` 依赖 `OCP`（`cadquery-ocp-novtk 8.0.1.1.0`）、`numpy`、`igl`；`alignment.py` 仅依赖 `numpy`、`scipy.spatial.transform.Rotation` 及 `geometry` 中封装的 `igl.AABB` `closest()` 查询，**ICP 循环内无任何 OCCT 调用**。
- 原设计文档第 4.4 节预占实扣与回滚 SQL 未校验 `trial_reservations.status = 'RESERVED'`，且未在 `rowcount == 0` 时阻断结果落库；第 2.2 节误写 `pythonocc-core` 与 `trimesh`。
- Git 现状：`main`、`test1`、`feat/platform-v1` 同指向 `2f19d54`，标签 `demo-v0.3.1` 已冻结。

## 未核实

- 客户真实 STEP 样本上的单件 OCCT 解析耗时、单次 `igl` ICP 耗时及语义 PMI / 材料携带率尚无实测，待阶段 1 实测后冻结 `K`、`compare_job_timeout`、`reaper_ttl` 与 `sync_wait_timeout`。
- Top-5 找回率 `>= 80%` 与同步对比 `P95 <= 30s` 为占位建议值，待阶段 1 确认客户能否提供 20–30 组预登记正例并在阶段 2 出口实测基线后签认。

## 建议顺序

按单人独立开发的模块依赖顺序推进：

1. **收口更新设计文档与开发计划**：本轮审核收敛并获用户采纳后，将 `S5-1` 至 `S5-4` 的表结构、SQL 状态守卫、两层 Worker 架构与 PostgreSQL `SKIP LOCKED` 队列正式写入 [详细开发设计文档](../Docs/STEP模型相似度对比与检索系统详细开发设计文档.md) 与 [详细开发计划](../Docs/STEP模型相似度对比与检索系统详细开发计划.md)。
2. **阶段 1 样本盘点与基础骨架并行**：确定前端框架（Vue 3 或 React 二选一）与唯一首期部署目标（Linux Docker 或 Windows Server 二选一）；搭建 FastAPI + PostgreSQL + Alembic + `SKIP LOCKED` Worker 骨架与 `config.py`；同步用盘点脚本实测客户 STEP 样本，冻结 $K$、超时参数与正例集。
3. **阶段 2 多维特征引擎与评分剪枝内核**：实现常驻受控 OCCT 解析子进程池、`D1–D7` 提取与 `model_features` 版本化存储、任务 Worker 内 `igl.AABB` 复用 ICP 精排、动态有效权重打分与上界剪枝，通过四类难例回归。
4. **阶段 3 多企业权限、五态台账与并发配额**：实现 `apply_visibility_scope` 跨企业隔离、HMAC + `auth_version` 鉴权缓存与双限流、五态审核流与同企 MD5 幂等、以及带迟到结果拦截的四态配额预占与回收器（在 PostgreSQL 上完成 10 路并发与迟到拦截压测）。
5. **阶段 4 三维对比工作台、客户端混合同步双 API 与自动化验收**：完成 Three.js 并排/叠加工作台、客户端接口与六大验收矩阵交付。

## 明确不做

- 不改动 `STEP-SIM-004` 已采纳的 `D1–D7` 维度定义、安全门禁、五态流转及客户需求功能范围。
- 首期不引入 Redis、Celery/RQ 或跨企业几何缓存共享。
- 不在本话题中展开双人排期或商务报价总额调整；不修改 `Demo/` 既有代码。

## 对上一轮审核的处理

| 审核意见 | 处理结论 | 依据与改动位置 |
| --- | --- | --- |
| **不成立项 1**：单人开发下去掉 92 vs 106 人/天报价重排与 A/B 双人周表 | **采纳** | 按用户“单人开发，聚焦框架与代码逻辑”指令，删除第 1 轮第 1、2 节的报价分项重排、106 人/天备选与 A/B 双线周表，改为「建议顺序」中的单人模块依赖顺序 |
| **不成立项 2 & S5-3**：ICP 依赖 `numpy+scipy+libigl` 不调 OCCT，无需每次起子进程；修正 `libigl` 依赖；队列收口为 PostgreSQL `SKIP LOCKED` | **采纳** | 实查 `Demo/src/geometry.py` 与 `alignment.py` 确认属实。在本提案「建议决定 3」及设计文档第 2.1/2.2/3.1/3.4 节改为“常驻受控 OCCT 解析子进程池 + 任务级 Worker 复用 `igl.AABB` 树顺序 ICP”，依赖修正为 `cadquery-ocp-novtk + numpy + scipy + libigl`，队列收口为 PostgreSQL `FOR UPDATE SKIP LOCKED`（去 Redis/Celery） |
| **S5-1**：配额 CAS 失败须拦截迟到结果防白嫖；`reaper_ttl` 须覆盖 `排队最大等待 + compare_job_timeout`（含解析与 $K$ 次精排） | **采纳** | 在本提案「建议决定 1」及设计文档第 4.4/7.1/7.2 节定义任务级总硬超时 `compare_job_timeout` 与公式 `reaper_ttl > queue_wait_timeout + compare_job_timeout + 30s`；规定结果落库前同事务 CAS 更新 `trial_reservations` 为 `COMMITTED`，若 `rowcount == 0` 严禁写入/返回 `compare_results`，直接置 `FAILED (QUOTA_EXPIRED)` |
| **S5-2**：密码缓存仅缓成功项，键采用 `HMAC-SHA256(pepper, user_id:auth_version:password)`，改密/禁用/锁定/改权递增 `auth_version` 即时失效，未命中加 IP+账号双限流 | **采纳** | 在本提案「建议决定 2」及设计文档第 4.1/5.1 节给 `users` 表增加 `auth_version` 字段，落地 HMAC 缓存键、只缓成功项、状态变更递增 `auth_version` 即时失效及未命中前的 IP+账号双限流 |
| **S5-4**：`feature_version` 升级按版本隔离存储，全库就绪后原子切换 `active_feature_version`，禁止新旧混算 | **采纳** | 在本提案「建议决定 4」及设计文档第 3.2/4.1 节增设 `model_features(model_id, feature_version, ...)` 版本隔离结构与 `active_feature_version` 全局指针，确保全库重提取完成前在线对比统一使用旧版特征 |
