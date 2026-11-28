# STEP 模型相似度对比与检索系统 · 详细开发计划（STEP-SIM-004 正式多企业平台）

**依据基线**：`STEP-SIM-004` + `STEP-SIM-005`（均已 ADOPTED，见 [PlanDocs/DECISIONS.md](../PlanDocs/DECISIONS.md)） · [详细开发设计文档](STEP模型相似度对比与检索系统详细开发设计文档.md) · [报价单（92 人/天 / ¥266,340）](STEP模型相似度对比与检索系统开发报价单.xlsx)  
**总体排期**：单人独立开发按模块依赖顺序推进（商务对外报价口径保留 **92 人/天**，规划周期 **10–12 周**，不含客户侧服务器/证书/样本整理等待时间）。  
**技术收口基线**：已合入 `STEP-SIM-005` 采纳修正（`S5-1` 至 `S5-4`：配额 CAS 迟到结果拦截与 `compare_job_timeout`、有界 LRU 的 HMAC+`auth_version` 鉴权缓存与双限流、OCCT/ICP 分层 Worker 与 PostgreSQL `SKIP LOCKED` 队列、`model_features` 版本隔离及切换前查漏补齐）。  
**说明**：本文由 `.claude/Plan.md` 迁出（2026-11-27），`.claude/Plan.md` 按项目规则只保留当前 1–3 项待推进工作。

---

## 阶段 0：开工前置准备（分支切分与报价发客）

- [x] ~~**Git 分支整理**：`test1` 已快进合并至 `main`，标签 `demo-v0.3.1` 已打，已从 `main` 切出 `feat/platform-v1`（2026-11-27 核对：三分支同指向 `2f19d54`）。正式平台在 `Platform/` 目录开工，保持 `Demo/` 不受影响。~~
- [ ] **报价单发客前复查**：在桌面版 Excel 打开 [STEP模型相似度对比与检索系统开发报价单.xlsx](STEP模型相似度对比与检索系统开发报价单.xlsx) 预览跨页打印版式并确认无误后发给客户。
- [ ] **（并行遗留）Demo 0.3.1 修复包确认**：如客户仍需本地演示，关闭 Studio 后安装 17:24 修复包并用 `Data/test/SHOW-HOLE.step` 复核。

---

## 阶段 1：客户样本盘点与安全/接口/规则契约冻结（与基础骨架并行）

**目标**：拿真实数据摸底，把所有可能返工的边界参数一次签认冻结；不依赖冻结参数的数据库与 Worker 骨架同步搭建。

- [ ] **1.1 客户真实样本盘点脚本与体检报告**
  - ~~实现 STEP 盘点脚本并用自制 STEP 回归协议、PMI、材料、几何与计时统计。~~
  - 抽取客户 20–50 件代表性 `.stp`/`.step`，运行 `STEPCAFControl_Reader` 统计：AP203/214 vs AP242 分布、语义 PMI 可读率、材料与属性填写率、单/多闭合实体与开壳/装配体占比、单文件体积与面片数分布、跨 CAD 重导出面片分裂实测及单次 `igl` ICP 耗时实测。
- [ ] **1.2 规则、正例集与安全契约签认冻结**
  - 签认 `D1–D7` 默认百分比模板、零分阈值及**单方缺失默认策略**（默认 `ZERO` 保守计分），并书面说明无 PMI 时 `D3 → D7` 替代的实际触发比例；
  - 签认**预登记正例集**（20–30 组查询-历史正例对）及阶段 2 出口 Top-5 找回率门槛签认机制；
  - 签认**待审队列审核角色粒度**（全局系统管理员 vs 增设企业管理员）；
  - 签认**无邀请码注册防刷方式**（IP 限流 + 本地生成图形验证码 + 后台开关）及公共演示库样本来源；
  - 签认规则作用域（全局唯一 vs 按企业覆盖）。
- [ ] **1.3 客户端 API 与部署环境冻结**
  - 确认客户端调用形态、峰值并发、Top-1/Top-N 返回结构、混合同步模式（`sync_wait_timeout` 默认 `25s–30s` 超时转 `202` 轮询）及选中次数回传契约；
  - 冻结有界队列上限 `max_queue_depth`、单文件上限（如 `50 MB`）、精排上限 $K$（如 `50`）、单件 OCCT 硬超时（如 `60s`）、任务级总硬超时 `compare_job_timeout`（如 `120s`）、内存/面片熔断上限及预占超时回收阈值 `reaper_ttl`（强制 `> 排队上限 + compare_job_timeout + 30s`）；
  - 获取目标服务器环境清单（OS、CPU/内存、磁盘、HTTPS 证书），据此二选一冻结唯一首期部署目标（Linux Docker / Windows Server）。
  - ~~前端框架已选定 React（2026-11-28）。~~
- [ ] **阶段 1 放行条件**：真实样本盘点报告输出，上述参数与契约经客户书面/纪要确认冻结。

---

## 阶段 2：多维特征引擎与动态有效权重评分内核（对应报价模块一 37 人/天）

**目录**：`Platform/backend/app/geometry_engine/`、`Platform/backend/app/rules/`、`Platform/backend/app/compare/`

阶段 2 里程碑 `ecc9474` 已提交。独立审核与实测边界见 [STEP-PLAT-004 归档](../CRDocs/archive/2026-11-28_STEP-PLAT-004_D5精排与动态评分剪枝/INDEX.md)。

- [x] ~~**2.1 工程脚手架、PostgreSQL `SKIP LOCKED` 队列与两层 Worker 进程池（5 人/天）**~~
  - ~~搭建 `Platform/backend/` FastAPI + PostgreSQL + SQLAlchemy + Alembic 骨架及基于 `FOR UPDATE SKIP LOCKED` 的任务队列（不引入 Redis/Celery）；~~
  - ~~实现 `worker_runner.py`：第一层常驻受控 OCCT 解析子进程池（文件头 `ISO-10303-21;` 校验、单件硬超时强杀、内存与最大面片数熔断、每 $N$ 任务主动轮换防 C++ 内存泄漏）+ 第二层对比任务 Worker（受 `compare_job_timeout` 硬超时管控）。~~
- [x] ~~**2.2 `D1–D7` 全维度特征提取与 `model_features` 版本隔离持久化（10 人/天）**~~
  - ~~`step_reader.py`：复用 `Demo/src/geometry.py`（`cadquery-ocp-novtk`）毫米统一，扩展单闭合实体/多闭合实体累加/开壳显式标记；~~
  - ~~`features_d1_d4.py`：D1（名称词干 + XDE 属性/颜色/图层 + 材料 + B-Rep 参数）、D2（有效闭合体积）、D3（`XCAFDoc_DimTolTool` AP242 PMI 解析）、D4（总表面积 + `BRepAdaptor_Surface` 类型面积占比 + 共轴/共面逻辑合并有效面数）；~~
  - ~~`features_d6_d7.py`：D6（主轴 OBB 尺寸与比例）、D7（内圆柱孔识别、孔数、孔径多重集、孔心距分布）；~~
  - ~~按 `model_features(model_id, feature_version, ...)` 版本隔离落库并导出 `.npz` 采样网格与 `.glb` 缓存，配合 `active_feature_version` 原子切换防升级混算。~~
- [x] ~~**2.3 空间刚体对齐、进程内 `igl.AABB` 复用与安全门禁（13 人/天）**~~
  - ~~`alignment_d5.py`：基于 `numpy + scipy + libigl (igl)` 复用并工程化封装 24 初值右手系刚体配准、Trimmed ICP（点到面 + SVD 回退）；在对比任务 Worker 进程内一次性构建查询件 `igl.AABB` 树并顺序完成至多 $K$ 次候选精排与 $C_{\text{新}} T C_{\text{旧}}^{-1}$ 矩阵换算（不逐次起子进程）；~~
  - ~~安全门禁：确保去重容差严格紧于歧义阈值，右手系暴露镜像偏差，多合格姿态歧义自动标「待复核」。~~
- [x] ~~**2.4 动态百分比打分器、缺项规则与有效权重上界剪枝（9 人/天）**~~
  - ~~实现 `D1–D7` 百分制子分、总和 `100%` 校验、双方缺失重分配/D3 转 D7 替代、单方缺失显式标记与三选一策略、$V(q,m)=\emptyset$ 防除零转「特征不足/待复核」；~~
  - ~~实现基于重分配有效权重 $W'_{\text{D5}}(q,m)$ 的上界剪枝 $U(q,m) = S_{\text{other}}(q,m) + W'_{\text{D5}}(q,m)$，精确区分 `exact_pruned` 与 `truncated_by_k` 诊断标记。~~
- [x] ~~**阶段 2 放行条件**：修改任意权重免重解析生效；数学一致性（误差 `< 0.05`）、$V=\emptyset$ 兜底、有效权重上界无误剪单测及四类难例（面片分裂/无 PMI/同体异形/镜像对称）回归测试全部通过（`STEP-PLAT-004` 于 2026-11-28 独立审核 `APPROVED`）。~~

---

## 阶段 3：多企业权限、五态待审台账与服务端规则后台（对应报价模块二 31 人/天）

**目录**：`Platform/backend/app/db/`、`auth/`、`models_mgmt/` 及对应管理端页面

已独立审核的服务端交付见 [STEP-PLAT-005](../CRDocs/archive/2026-11-28_STEP-PLAT-005_鉴权隔离与试用配额/INDEX.md) 和 [STEP-PLAT-006](../CRDocs/archive/2026-11-28_STEP-PLAT-006_五态入库与对比调度/INDEX.md)。

- [ ] **3.1 账号认证、HMAC+`auth_version` 鉴权缓存与 5 次并发 CAS 预占限额（11 人/天）**
  - ~~实现数据库表结构与 Alembic 迁移（`enterprises`、`invite_codes`、`users`〔含 `auth_version`〕、`trial_reservations`、`models`、`model_features`、`similarity_rules`、`compare_jobs`、`compare_results`、`audit_logs`）。~~
  - ~~实现 Argon2id、JWT 登录、Basic Auth、HMAC 有界 LRU/TTL 成功缓存、`auth_version` 即时失效和 IP + 账号双限流。~~
  - ~~实现企业创建和查询、邀请码创建/查询/撤销及限次原子绑定。~~
  - ~~实现试用任务原子入队和 5 次预占、结果 CAS 实扣、失败回滚及过期回收，接通已领取试用任务的精排与结算。~~
  - ~~封装 Repository 层 `apply_visibility_scope`，用于列表、详情、GLB、对比候选和结果发布。~~
  - ~~接通企业/试用对比、Web/客户端上传与轮询、常驻消费和定时回收。~~
  - 注册入口（IP 限流 + 本地验证码 + 后台开关），依赖阶段 1 签认。
- [ ] **3.2 模型台账管理、五态审核流与 MD5 幂等去重（13 人/天）**
  - ~~实现服务端批量上传、五态审核、同企 MD5 去重、失败重提、查询独立归档和满队列 `503`。~~
  - ~~台账 API 落库并返回六项必记字段，支持多条件筛选。~~
  - 开发可视化台账与批量审核交互，展示 MD5、模型名称、日期、上传人、模型特征值和对比选中次数。
- [ ] **3.3 服务端规则配置与管理后台（7 人/天）**
  - ~~实现规则配置后台 API、版本激活、企业覆盖和任务快照落库。~~
  - 开发可视化规则配置页（`D1–D7` 滑块/输入实时校验 `100%`、零分阈值、双/单方缺失策略、上限 $K$、多项目模板保存与一键生效）。
  - 开发企业/邀请码/试用配额与审核日志管理页，补记管理员直传审计（`PLAT-006-P2-1`）。
- [ ] **阶段 3 放行条件**
  - ~~本机 PostgreSQL 和 ASGI 验证 10 路并发 5 次限额无穿透、损坏件准确回滚、超时回收后迟到结果零落库。~~
  - 四角色越权矩阵测试 0 泄漏，并在矩阵中核对 `PENDING` 与 `PARSE_FAILED` 检索命中为 0。现有用例已覆盖待审不可读、解析失败不可读且未进入回归场景的候选结果。

---

## 阶段 4：三维图形化对比工作台、客户端双 API 与联调验收（约 2.5–3 周 · 对应报价模块三 24 人/天）

**目录**：`Platform/frontend/`、`Platform/backend/app/client_api/`、`Platform/client_sdk/`、`Platform/deploy/`

- [x] ~~**4.1 服务端三维图形化对比工作台（13 人/天）**~~
  - ~~集成 React 工作台和本地打包的 Three.js 查看器，支持同尺度双视口同步旋转缩放、通过门禁后的双色半透明叠加（$C_{\text{新}} T C_{\text{旧}}^{-1}$）与单模显隐。~~
  - ~~交付 Web 上传、异步轮询、候选/待复核结果和选中幂等回写 `selected_count` 的服务端接口。~~
  - ~~接通工作台上传、进度轮询、结果展示和选中回传。按任务授权读取查询件和候选 GLB，候选使用任务冻结的特征版本。~~
  - ~~展示「待复核」折叠区并禁用叠加，展示分项误差、有效权重和单方缺失详情，固定展示工程边界提示。~~
  - 交付证据见 [STEP-PLAT-007 独立审核归档](../CRDocs/archive/2026-11-28_STEP-PLAT-007_React三维对比工作台/INDEX.md)。
- [ ] **4.2 客户端双 API、安全限流与调用示例（11 人/天）**
  - ~~交付 `POST /api/v1/auth/token`、`POST /api/v1/client/models/batch-upload`、`POST /api/v1/client/compare`、`GET /api/v1/client/compare/{job_id}`、`POST /api/v1/client/compare/{job_id}/select` 及 OpenAPI 接口描述。~~
  - ~~实现 URL Query 凭证拦截、应用日志脱敏、进程内频率限制及单文件、批次文件数和 multipart 请求体上限。~~
  - 验证目标环境 HTTPS、可信代理和多进程限流，落实签认的暂存磁盘配额、查询原件保留期限及孤立文件处置。
  - 交付 `Platform/client_sdk/` 调用示例脚本（Python / curl）。
- [ ] **4.3 整机联调、六大验收矩阵与部署交付**（当前未分配工时，见 `STEP-SIM-005`）
  - 在目标环境执行 [详细开发设计文档](STEP模型相似度对比与检索系统详细开发设计文档.md) 第 7.1 节全部六大验收测试（含完整检索找回分母统计与四类归因）；
  - 交付部署脚本（按阶段 1 冻结的首期部署目标）、联调验收报告与运维手册。
- [ ] **阶段 4 放行条件**：六大验收维度全部通过，客户签收上线。

---

## 附录：本地全链路闭环剩余切片细化计划（STEP-PLAT-008 ～ STEP-PLAT-010）

> **推进策略（2026-11-28 确认）**：在客户真实 STEP 样本与机房清单到位前，先在本地完成全部软件功能闭环与仿真部署包，使所有角色均可在浏览器与客户端脚本内跑通完整业务；客户现场到位后仅需替换网络/证书并运行样本盘点脚本校准参数即可上线。各切片均须遵守 `.claude/Rules.md` 第 7 节（零冗余重型依赖、全中文注释、1000/1500 行门禁、严禁改动 `Demo/`、CRDocs 独立审核闭环）。

### 切片 `STEP-PLAT-008`：可视化管理后台（模型台账、五态审核、规则配置与企业/邀请码/审计）

- **状态（2026-11-28）**：已实施并通过独立审核。后端 PostgreSQL 18.7 全量 69/69，前端 6/6 且生产构建通过。[独立审核归档](../CRDocs/archive/2026-11-28_STEP-PLAT-008_可视化管理后台/INDEX.md)。

- **目标**：把已有管理员与企业入库 API 升级为浏览器可视化操作，彻底告别手敲 HTTP 接口，同步收口阶段 3 的四角色越权矩阵与两项历史 P2。
- **后端增量（最小改动，不引入新重型依赖）**：
  1. **补记管理员直传审计**（核销 `PLAT-006-P2-1` 第一项）：`POST /api/v1/models/batch-upload` 成功入库写入 `AuditLog(action="MODEL_DIRECT_UPLOADED", target_type="model", target_id=model_id)`。
  2. **管理端分页与批量接口**：
     - `GET /api/v1/admin/models`：系统管理员按五态（`PENDING`/`INDEXING`/`APPROVED`/`PARSE_FAILED`/`REJECTED`）、`enterprise_id`、`name`、`md5`、`uploader_id`、`after_id`（int64 校验）分页查询，返回 6 项必记字段及 `reject_or_error_reason`、`is_public_demo`；
     - `POST /api/v1/admin/models/batch-review`：系统管理员批量通过（可选 `public_demo`）或驳回（必填 `reason`），复用 `review_model` 原子事务与审计，逐件返回成功/冲突结果；
     - `GET /api/v1/admin/audit-logs`：分页只读审计日志（严禁回显密码、Token 或邀请码明文）。
  3. **核销 `PLAT-007-P2-1`**：放宽 `Platform/backend/tests/test_workflow_api.py` 中 `_poll` 25 秒硬超时至 45 秒（或支持环境变量覆盖），消除 Windows 高负载下偶发抖动。
- **前端增量（`Platform/frontend/`，严控单文件 < 1000 行）**：
  1. 顶部增加角色感知导航栏：所有登录用户可见“三维对比工作台”，企业用户可见“企业模型上传与台账”，`SYS_ADMIN` 额外可见“管理后台”；令牌仍仅驻留内存。
  2. **模型台账与五态审核页**：展示 6 项必记字段（MD5、模型名称、日期、上传人、特征摘要、选中次数）、五态标签与筛选；支持企业批量上传（进 `PENDING`）、管理员选定企业直传（进 `INDEXING`）以及勾选待审件批量通过/驳回。
  3. **可视化规则配置页**：`D1–D7` 权重滑块/输入框实时校验总和 `100%`、零分阈值、双/单方缺失策略、`max_refine_k`、全局/企业作用域选择、保存不可变新版本与一键激活。
  4. **企业、邀请码与审计页**：企业创建/列表、邀请码创建/列表/停用、试用账号“输入邀请码绑定企业”可视化入口，以及只读审计日志列表。
- **验收与自测门禁**：
  - 真实 PostgreSQL + ASGI 补齐**四角色越权矩阵测试**（`SYS_ADMIN` / `ENT_ADMIN` / `ENT_USER` / `TRIAL_USER` 跨租户与跨角色 0 泄漏，并核实 `PENDING` 与 `PARSE_FAILED` 检索命中为 0）；
  - 前端 `npm test` 覆盖规则 `100%` 校验、批量审核载荷及角色视图门禁，`npm run build` 成功；`Demo/` 与 `demo-v0.3.1` 一致。

### 切片 `STEP-PLAT-009`：公开注册防刷闭环与管理员初始化 CLI

- **状态（2026-11-28）**：进入实施，完成后按 CRDocs 规则独立审核。

- **目标**：打通“自助注册试用账号 → 免费对比 5 次 → 绑定邀请码升级企业账号”全自助闭环，并补齐开发/运维受控初始化首个系统管理员的命令行工具。
- **后端交付**：
  1. **公开注册开关**：在数据库配置表（提供可回滚 Alembic 迁移）或受控状态表持久化 `public_registration_enabled`，提供 `GET /api/v1/auth/registration-status`（公开只读开关状态）与 `PATCH /api/v1/admin/settings/registration`（仅 `SYS_ADMIN` 可切换并记审计日志）。
  2. **本地轻量图形验证码（零外部重型依赖）**：服务端本地生成一次性 SVG 验证码（含随机干扰线/字符偏移，不引入 Redis 或重型图像库），令牌以有界 LRU/TTL 内存存储 + HMAC 签名防伪造，校验一次即焚（防重放）。
  3. **公开注册接口 `POST /api/v1/auth/register`**：校验开关开启、IP 维度限流（复用 `AuthRateLimiter`，超限返 `429`）、图形验证码、用户名唯一性与密码长度，创建 `TRIAL_USER`（`is_trial=True, used_count=0, in_flight_count=0`）并写审计；校验错误统一脱敏 `422`。
  4. **首个管理员初始化 CLI**：`python -m app.cli init-admin`（支持交互输入或环境变量读密，禁止明文密码参数进命令行进程表，幂等创建/提权 `SYS_ADMIN`，可选同步激活默认全局规则）。
- **前端交付**：
  - 登录弹窗新增“注册试用账号”标签页（加载/点击刷新本地 SVG 验证码、提交注册并提示 5 次试用额度与邀请码绑定入口）；开关关闭时清晰展示不可注册提示。
- **验收与自测门禁**：
  - PostgreSQL + ASGI 回归覆盖注册开关关闭 `403`、错误/过期/重放验证码拒绝、IP 限流 `429`、新注册账号走通“5 次试用配额 → 绑定邀请码转 `ENT_USER` → 旧令牌/旧权限即时失效”、以及 `init-admin` CLI 幂等与脱敏测试。

### 切片 `STEP-PLAT-010`：客户端调用示例与本地仿真生产部署包

- **目标**：提供开箱即用的客户端调用示例与本地仿真生产部署脚本，在本地以“前端 `dist/` + 反向代理 + PostgreSQL + 常驻队列”跑通完整链路，缩短客户现场交付周期。
- **交付物**：
  1. **`Platform/client_sdk/`**：提供 Python 与 `curl`/PowerShell 示例脚本及 `README.md`，完整演示“登录换 Token → 批量上传待审模型 → 混合同步提交对比（处理 `200` 与 `202` 轮询）→ 选中回传”。
  2. **`Platform/deploy/`**：
     - 反向代理配置模板（Nginx / Caddy）：托管 `frontend/dist` 静态产物、同源 `/api` 转发至 FastAPI、请求体上限与未认证表单上传限流/超时加固（对应 `PLAT-006-P2-1`）、可信代理真实 IP 透传说明及 HTTPS 证书挂载位；
     - 本地仿真生产启停与健康检查脚本；
     - 存储卷查询原件与孤立 `.glb`/`.step` 定期清理脚本（支持 `--retention-days` 与默认 `--dry-run` 干跑防误删，仅清理已终结或孤立查询文件，不触碰在途任务与活跃模型）。
  3. **文档与本地多浏览器冒烟**：更新 `Docs/STEP平台操作帮助.md`（补充管理后台、公开注册、`init-admin` CLI 与仿真部署步骤）；在本机通过反向代理访问 `dist/` 产物，在 Chrome 与 Edge 各跑通一次端到端冒烟并记录实测环境与耗时。
- **现场交付边界（不可跳过）**：本地三切片全部通过后，到达客户现场仍须执行 ① 20–50 件客户真实 `.stp` 样本盘点与规则/超时/找回率校准（`PLAT-DEF-001`）；② 目标服务器 OS 内存熔断（Linux `RLIMIT_AS`/cgroup 或 Windows Job Object）、真实 HTTPS/可信代理多进程压测与客户办公机浏览器/GPU 兼容性验收（`PLAT-DEF-002/003`）。
