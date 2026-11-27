# 审核

**STEP-SIM-005 · 第 2 轮 · 2026-11-27 · 审核方 Neon · 审核对象 PROPOSAL.md 第 2 轮（提案方 Antigravity）· 结论 CONVERGED**

第 1 轮及前序话题 `STEP-SIM-004`、`STEP-SIM-003` 历史审核原文保留在下方。

## 本轮核对了什么

- 读取 `PlanDocs/STATUS.md`（STEP-SIM-005 第 2 轮，`READY_FOR_REVIEW`）与 `PlanDocs/PROPOSAL.md`（第 2 轮全文及「对上一轮审核的处理」），逐条核对第 1 轮意见 `S5-1` 至 `S5-4` 与两项“不成立或说重了”的修正落实情况。
- 交叉抽检同步更新后的 `Docs/STEP模型相似度对比与检索系统详细开发设计文档.md` 与 `Docs/STEP模型相似度对比与检索系统详细开发计划.md`，核实 `compare_job_timeout`、`QUOTA_EXPIRED`、`auth_version`、`HMAC-SHA256`、`SKIP LOCKED`、`libigl`、`model_features`、`active_feature_version` 等核心工程机制均已写入正文。

## 属实

1. **S5-1 已落实。** 提案第 1 节明确了涵盖解析与全部至多 $K$ 次 ICP 的任务级总硬超时 `compare_job_timeout`，将 `reaper_ttl` 公式修正为 `> queue_wait_timeout + compare_job_timeout + 30s`；并在写入 `compare_results` 与更新 `compare_jobs` 的同一短事务中执行 `trial_reservations` `RESERVED → COMMITTED` 的 CAS 守卫，当 `rowcount == 0` 时严禁落库或返回结果、直接置 `FAILED (QUOTA_EXPIRED)`，彻底封堵迟到任务白嫖漏洞。
2. **S5-2 已落实。** 提案第 2 节为 `users` 表增加 `auth_version`，鉴权先查用户激活/锁定态，仅缓存校验成功项，缓存键采用 `HMAC-SHA256(server_pepper, f"{user.id}:{user.auth_version}:{plaintext_password}")`，改密/禁用/锁定/改企/改权同事务递增 `auth_version` 实现即时失效；缓存未命中进入 Argon2id 前叠加 IP + 账号双限流，防 CPU 耗尽。
3. **S5-3 已落实。** 提案第 3 节已修正真实几何依赖为 `cadquery-ocp-novtk + numpy + scipy + libigl`（剔除 `pythonocc-core` 与 `trimesh`）；拆分“常驻受控 OCCT 解析子进程池（超时强杀 + 每 $N$ 任务优雅轮换）”与“任务级 Worker 进程内复用 `igl.AABB` 树顺序跑完至多 $K$ 次 ICP（不逐次起进程）”；任务队列收口为 PostgreSQL `FOR UPDATE SKIP LOCKED` + 常驻 Python Worker 池，去掉 Redis/Celery，契合单人开发与内网可维护性。
4. **S5-4 已落实。** 提案第 4 节拆出独立版本化表 `model_features(model_id, feature_version, ...)`（`UNIQUE(model_id, feature_version)`）与全局 `active_feature_version` 指针，确保全库重提取就绪前在线对比统一使用旧版本提取器与旧版本特征，就绪后原子切换，杜绝新旧特征混算。
5. **单人开发视角收口到位。** 已剔除第 1 轮中的 92 vs 106 人/天商务报价重排与 A/B 双人周表，改为按单人模块依赖顺序推进。

## 不成立或说重了

无。

## 遗漏

没有会阻挡开工的实质遗漏。以下 2 条为编码落地时的轻量化工程注意点，供开发时直接落实（无需另开一轮）：

1. **鉴权内存缓存须设容量上限**：`K_auth` 短时内存缓存（TTL 300s）应使用有界 LRU/TTL 缓存（如 `maxsize=10000`），避免极端高并发下内存无界增长。
2. **特征升级窗口期内的新入库补齐**：后台批量重提取 `v1.1.0` 期间若有新模型审核通过入库，须在原子切换 `active_feature_version` 前做一次增量查漏补齐（或入库时双写目标版本），保证切换瞬间 `APPROVED` 模型 100% 具备新版本特征。

## 顺序

不改。按第 2 轮提案单人模块依赖顺序执行：收口更新设计文档与开发计划 → 阶段 1 样本盘点与 FastAPI/PostgreSQL/`SKIP LOCKED` 骨架并行（冻结前端框架、唯一部署目标与超时/正例参数）→ 阶段 2 OCCT/ICP 两层引擎与版本化特征内核 → 阶段 3 多企业隔离/HMAC+`auth_version` 鉴权/五态台账与并发配额 → 阶段 4 Three.js 工作台、客户端混合同步双 API 与自动化验收。

## 未核实

沿用第 2 轮提案「未核实」节：客户真实 STEP 上的单件 OCCT 解析耗时、单次 `igl` ICP 耗时、PMI/材料携带率及 20–30 组预登记正例集，待阶段 1 实测后冻结 `K`、`compare_job_timeout`、`reaper_ttl`、`sync_wait_timeout` 及找回率/P95 门槛。

## 结论

**CONVERGED。**

`S5-1` 至 `S5-4` 及单人架构收口均已完整落实，技术框架、并发状态机、进程隔离与数据版本逻辑全部闭合，可交由用户决定是否采纳并直接按此基线开工编码。

下一位负责人：用户（决定是否采纳）。

---

# 第 1 轮审核（原文保留）

**STEP-SIM-005 · 第 1 轮 · 2026-11-27 · 审核方 Neon · 审核对象 PROPOSAL.md 第 1 轮（提案方 Antigravity）· 结论 CHANGES_REQUESTED**

本话题意见编号为 S5-1 至 S5-4。按用户明确指令（本项目由单人独立开发，不关注商务金额与双人排期，聚焦技术框架、架构与代码逻辑），本轮仅针对架构、代码逻辑、并发状态机与依赖选型进行独立核查。前序话题 `STEP-SIM-004` 与 `STEP-SIM-003` 历史审核原文保留在下方。

## 本轮核对了什么

- 读取 `PlanDocs/README.md`、`PlanDocs/STATUS.md`（STEP-SIM-005 第 1 轮，`READY_FOR_REVIEW`）与 `PlanDocs/PROPOSAL.md` 全文，并对照已采纳决定 `STEP-SIM-004` 与 `Docs/STEP模型相似度对比与检索系统详细开发设计文档.md` 的最新变更。
- 核对工作区实物代码与环境：
  - 检查 `Demo/.venv` 实装包列表，确认绑定包为 `cadquery-ocp-novtk 8.0.1.1.0`（导入名 `OCP`），而非 `pythonocc-core`；
  - 读取 `Demo/src/geometry.py`（L1–60）与 `Demo/src/alignment.py`（L1–60），核实 OCCT 调用范围与 ICP/AABB 实际依赖（`numpy` + `scipy.spatial.transform.Rotation` + `igl`，而非设计文档所写的 `trimesh`）。
- 重点审查提案中的 5 项核心代码逻辑：试用配额预占与回收器竞态、短时密码校验缓存、对比队列硬上限与混合同步模式、进程隔离边界、以及 `feature_version` 升级与选型收敛。

## 属实

1. **配额状态机竞态（重复扣减 / 错扣）发现属实，CAS 守卫方向正确。** 原设计文档第 4.4 节在实扣与回滚时未校验 `trial_reservations.status = 'RESERVED'`，超时回收后迟到任务会误改 `users` 计数；用单事务 `UPDATE trial_reservations SET status=... WHERE id=:rid AND status='RESERVED'` 且仅在 `rowcount == 1` 时改 `users` 计数，是正确的并发防线。
2. **Basic Auth / 表单密码逐请求触发 Argon2id 慢哈希的 CPU DoS 风险属实。** 客户端高频调用若每次都跑慢哈希，会直接打满服务端 CPU；优先引导换取短期 JWT Token 是正确方向。
3. **队列无界会破坏 `reaper_ttl` 约束，混合同步（限时等待 + 超时转 `202` 轮询）与 `503 QUEUE_FULL` 硬上限方向成立。** 队列满时在预占前直接拒绝，能防止请求无限积压导致配额假死。
4. **实物依赖与数据库测试约束核实属实。** `Demo/.venv` 实际使用 `cadquery-ocp-novtk 8.0.1.1.0`；SQLite 不支持 `FOR UPDATE` 等行锁语义，并发配额与行锁测试必须在 PostgreSQL 上执行；首期不跨企业复用底层几何缓存可消除存在性侧信道；阶段 1 与不依赖冻结参数的骨架代码并行（参数走 `config.py`）合理。

## 不成立或说重了

1. **提案第 1、2 节关于 92 vs 106 人/天工时重排、A/B 双人周表与报价单微调的篇幅，在单人开发约束下不再适用。** 用户已明确本项目由单人独立开发，钱与人数不作为本话题决策重点；工程计划只需按单人可维护的模块依赖顺序组织，不必强拆 A/B 双人周表或引入商务降级条款。
2. **“精排上限 K=50，每次 ICP 在独立子进程中执行导致分钟级耗时”的架构前提有误。** 实查 `Demo/src/alignment.py` 可知，24 初值刚体配准与 Trimmed ICP 完全基于 `numpy`、`scipy` 和 `libigl (igl)` 对已提取的点云/网格数组运算，**根本不调用 OCCT C++ 内核**，无需为单次 ICP 重复起子进程。见 **S5-3**。

## 遗漏

**S5-1 配额 CAS 守卫漏掉了“迟到任务免费拿结果”漏洞，且 `reaper_ttl` 公式漏算了 $K$ 次精排总耗时**
- **代码逻辑缺陷**：
  1. 当预占单已被超时回收器置为 `EXPIRED` 并释放 `in_flight_count` 后，若原比对任务稍后跑完，按提案写法 `UPDATE trial_reservations ... WHERE status='RESERVED'` 返回 `rowcount == 0`，不会扣减 `used_count`；但如果 Worker 仍把算好的候选写入 `compare_results` 并返回给用户，试用账号就会**白得一次不扣额度的免费比对**。
  2. `reaper_ttl > 排队上限 + OCCT 单件解析硬超时` 只算了单件解析，漏掉了后续最多 $K$ 次 ICP 精排的耗时，在真实对比任务中极易触发误回收。
- **影响**：无邀请码 5 次限额的严格性与并发验收。
- **最小修正**：
  1. 为每次对比任务设定明确的**任务级总硬超时 `compare_job_timeout`**（涵盖解析 + 全部至多 $K$ 次精排），规定 `reaper_ttl > 队列最大等待时间 + compare_job_timeout + 安全缓冲`；
  2. 任务结束写入结果前，须在同一事务中先执行 `UPDATE trial_reservations SET status='COMMITTED' WHERE id=:rid AND status='RESERVED'`；若 `rowcount == 0`（说明已被回收过期），**禁止写入/暴露比对结果**，直接将任务置为 `FAILED (QUOTA_EXPIRED)` 返回。

**S5-2 短时密码校验缓存的键构造与失效条件存在逻辑歧义和权限滞后漏洞**
- **代码逻辑缺陷**：提案写“以 `用户名 + 密码哈希摘要` 为键，改密后失效”。若此处的“密码哈希”指 Argon2id，则构造缓存键本身仍需运行慢哈希，缓存形同虚设；若指普通无秘钥哈希且仅在“改密后失效”，则在**管理员禁用账号、账号连续失败被锁定、或企业/角色变更**后的 5 分钟内，缓存仍会放行已失效凭证；同时未限制失败条目进缓存与按 IP 限流，随机错密码仍可穿透打满 Argon2id CPU。
- **影响**：客户端双 API 鉴权安全性与抗 CPU DoS 能力。
- **最小修正**：
  1. 明确仅缓存**校验成功**的凭证，缓存键统一用带服务端秘钥的快哈希：`HMAC-SHA256(server_pepper, f"{user_id}:{auth_version}:{plaintext_password}")`（不存明文，不缓存失败条目）；
  2. 在 `users` 表增加 `auth_version` 整型字段，凡发生**改密、禁用账号、触发锁定、变更企业/角色**均原子执行 `auth_version += 1`，令旧缓存立即失效；
  3. 对缓存未命中而进入 Argon2id 校验的请求，叠加 **IP 维度 + 账号维度**双限流。

**S5-3 进程隔离边界、几何依赖清单与任务队列架构需面向真实代码和单人可维护性收敛**
- **代码逻辑与框架问题**：
  1. 实查 `Demo/src/geometry.py` 与 `Demo/src/alignment.py`：只有 STEP 解析/XDE 提取/网格离散与 GLB 导出依赖 `OCP`（`cadquery-ocp-novtk`）；ICP 对齐与双向距离计算依赖的是 `numpy + scipy + libigl (igl)`，并非设计文档写的 `trimesh`，且不需要每次 ICP 单独起子进程。
  2. 设计文档在“Celery / RQ (Redis) 或 `multiprocessing.Pool`”之间未定型，对单人开发与企业内网部署而言，引入 Redis + Celery 会平白多出两套运维组件与跨库状态同步复杂度。
- **影响**：对比接口延迟、代码复杂度、单人交付与部署运维成本。
- **最小修正**：
  1. 明确两层隔离模型：**OCCT 解析/特征提取**运行在常驻受控子进程池（超时强杀重建 + 每完成 $N$ 个任务主动轮换防 C++ 内存泄漏）；**对比任务（至多 $K$ 次 ICP）**在任务级 Worker 进程内复用查询件 `igl` AABB 树顺序完成并受 `compare_job_timeout` 硬超时管控，不逐次起进程。
  2. 修正设计文档几何依赖为 `cadquery-ocp-novtk` + `numpy` + `scipy` + `libigl`（去掉 `trimesh` 误写）。
  3. 任务队列直接收口为 **PostgreSQL 任务表（`SELECT ... FOR UPDATE SKIP LOCKED`）+ 独立常驻 Python Worker 进程池**，首期不引入 Redis 与 Celery（前端框架与部署目标按提案第 6 节在阶段 1 二选一冻结即可）。

**S5-4 `feature_version` 升级期间缺少新旧版本特征隔离机制，混算会导致分数漂移**
- **代码逻辑缺陷**：提案第 5 节要求“feature_version 升级时后台批量重提取，期间旧版本特征继续服务”，但未说明过渡期内在线查询用哪个版本提取器。若在线查询切到了 `v2` 提取器，拿 `v2` 特征去和库里尚未迁完的 `v1` 模型算分，维度定义或数值尺度不一致会导致结果失真或异常。
- **影响**：后续算法迭代升级时的线上兼容性与数据正确性。
- **最小修正**：将特征存储从单列覆盖改为**按版本隔离（如独立 `model_features(model_id, feature_version, payload)` 表或双版本槽位）**，系统配置 `active_feature_version`；后台对目标版本完成全库重提取校验后，再原子切换 `active_feature_version`，切换前在线对比全程统一使用旧版本提取器与旧版本特征，杜绝新旧混算。

## 顺序

按单人独立开发视角调整，去掉 A/B 双人排期与报价单重排步骤，聚焦技术落地顺序：

1. **本轮修正收敛并更新设计文档**：落盘 S5-1 至 S5-4 的代码级约束（配额结果拦截与 `compare_job_timeout`、HMAC+`auth_version` 鉴权缓存、OCCT/ICP 分层 Worker 与 PostgreSQL 队列、`model_features` 版本隔离），并修正 `cadquery-ocp-novtk` + `libigl` 依赖。
2. **阶段 1 冻结与基础骨架并行**：先定前端框架（Vue 3 或 React 二选一）与唯一部署目标（Linux Docker 或 Windows Server 二选一），搭建 FastAPI + PostgreSQL + Alembic + `SKIP LOCKED` Worker 骨架与 `config.py`；同步用样本盘点脚本实测客户 STEP 的 PMI/材料携带率与单次 ICP 耗时，冻结 $K$、超时、正例集及验收门槛。
3. **内核 → 权限台账 → 工作台与客户端 API → 自动化验收**：先打通 OCCT 隔离解析与 `D1–D7` + 有效权重上界剪枝内核，再实现多企业隔离/五态审核/并发配额（在 PostgreSQL 上跑并发压测），最后集成 Three.js 工作台、客户端混合同步双 API 与全套自动化回归。

## 未核实

沿用提案「未核实」中的技术实测项（去掉与单人开发无关的工时压缩与 106 人/天商务项）：
- 客户真实 STEP 上的单次 OCCT 解析耗时、单次 `igl` ICP 耗时及 PMI/材料填写率尚无实测，待阶段 1 实测后冻结 `K`、`compare_job_timeout`、`reaper_ttl` 与同步等待上限。
- 找回率 80% 与同步 P95 30 s 为占位建议值，待阶段 1 确认客户能否提供 20–30 个预登记正例并在阶段 2 出口实测基线后签认。

## 结论

**CHANGES_REQUESTED。**

提案抓住了原设计中的配额竞态、慢哈希 DoS、同步对比超时与 `cadquery-ocp-novtk` 依赖误写等关键工程隐患；但以下 4 处框架与代码逻辑问题需在第 2 轮修严密后再收敛：

- **S5-1**：`reaper_ttl` 须覆盖 `排队最大等待 + compare_job_timeout`（含解析与全部 $K$ 次精排），且预占 CAS 失败（已过期）时必须丢弃/拦截结果，杜绝迟到任务免费出分；
- **S5-2**：密码校验缓存改为仅缓存成功项，键采用 `HMAC-SHA256(pepper, user_id + auth_version + password)`，改密/禁用/锁定/改权均递增 `auth_version` 立即失效，未命中叠加 IP + 账号双限流；
- **S5-3**：按真实代码分离隔离边界（OCCT 解析走常驻受控子进程池，ICP/AABB 走 `numpy+scipy+libigl` 任务级 Worker 复用 AABB 树，不逐次起进程），并收口为 PostgreSQL `SKIP LOCKED` 队列（单人开发首期不引入 Redis/Celery）；
- **S5-4**：`feature_version` 升级采用按版本隔离存储 + 全库就绪后原子切换 `active_feature_version`，禁止新旧版本特征混算。

（同时建议第 2 轮提案按用户最新要求精简掉双人排期与报价重排内容，聚焦单人工程框架与代码逻辑。）

下一位负责人：提案方 Antigravity，进入第 2 轮。
