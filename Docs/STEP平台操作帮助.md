# STEP 模型相似度平台操作帮助

版本：`feat/platform-v1` 当前开发版
适用对象：第一次接触本项目的测试人员、业务人员和现场演示人员
更新时间：2026-11-28

## 先看结论

当前主要开发工作已经完成了第一条可运行主链路，可以进入**受控的实际测试阶段**：

1. 后端可以连接 PostgreSQL，完成登录、权限校验、STEP 上传、排队、特征提取、相似度计算和结果查询。
2. React + Three.js 工作台可以登录、提交 STEP、查看候选、查看 D1–D7 分项、并排查看两个模型、在通过门禁时叠加显示，以及记录选中结果。
3. 五态模型入库、企业隔离、试用配额、异步任务和 GLB 预览已经有自动化回归和本机真实 PostgreSQL 验证。

但这还不是客户生产上线版本。当前测试应使用测试数据库、测试账号和可回收的模型存储目录，不要把测试结果当成客户真实样本验收结论。客户的真实 STEP 样本、目标部署方式、浏览器/GPU 兼容性、HTTPS 反向代理、存储保留策略和完整管理后台仍需要后续确认或开发。

## 一、项目功能模块

| 模块 | 给用户带来的功能 | 当前入口 | 当前状态 |
| --- | --- | --- | --- |
| React 三维对比工作台 | 登录、上传查询件、查看任务状态、候选列表、分项详情、三维并排和叠加 | 浏览器页面 | 已完成，可实际测试 |
| 账号与权限 | Bearer 登录、账号版本失效、系统管理员/企业管理员/企业用户/试用用户隔离 | 工作台和 `/api/v1/auth/*` | 已完成基础链路 |
| 企业与邀请码 | 创建企业、发放邀请码、试用账号绑定企业 | 管理 API 和 Swagger | 已完成 API，暂无独立管理页面 |
| 模型入库 | 批量上传、MD5 去重、容量检查、五态流转 | 管理 API 和客户端 API | 已完成基础链路 |
| 人工审核 | 待审、通过、驳回、通过后进入索引 | `/api/v1/admin/models/*` | API 已有，模型台账页面是下一切片 |
| 特征提取与索引 | D1–D7 特征、版本化特征、GLB 预览 | 后台队列自动执行 | 已完成基础链路 |
| 相似度计算 | 门禁、粗排、D5 刚体对齐、精排、待复核分流 | 工作台和客户端 API | 已完成，可实际测试 |
| 队列与配额 | 异步任务、排队超时、试用次数预占和回收 | 后台自动执行 | 已完成基础链路 |
| 规则配置 | 全局/企业规则、D1–D7 权重、阈值和特征版本 | `/api/v1/admin/rules/*` | API 已有，配置页面待后续 |
| 客户端 API | 上传 STEP、同步/混合等待、轮询和选中回传 | `/api/v1/client/*` | 已完成基础链路 |

重要边界：相似度分数只用于检索排序和差异辅助检查，不能单独作为工程替代或放行结论。最终是否采用，必须由工程负责人结合图纸、工况和其他工程资料确认。

## 二、目录和文件作用

```text
Platform/
├─ backend/       FastAPI 后端、数据库迁移、队列和几何计算
└─ frontend/      React + Three.js 前端工作台
Docs/
└─ STEP平台操作帮助.md  本文档
Demo/              已冻结的旧版演示目录，不属于本平台开发目录
```

不要在 `Demo/` 下安装依赖、写入测试模型或修改代码。本平台测试全部使用 `Platform/` 和单独的测试存储目录。

## 三、测试前准备

### 3.1 软件要求

- Windows 10/11 或等效开发环境。
- Python 3.13。当前回归使用 Python 3.13.14。
- Node.js 22.12 或更高版本。当前回归使用 Node.js 24。
- PostgreSQL 18 或兼容版本。真正测试不要用 SQLite，SQLite 只适合基础自测。
- 支持 WebGL 2 的现代浏览器，例如新版 Chrome、Edge 或 Firefox。
- 至少一份可读的 `.step` 或 `.stp` 文件。建议先使用测试副本，不要直接操作客户原件。

### 3.2 首次账号说明

当前版本没有公开注册页面，也没有“一键初始化系统管理员”命令。测试前需要由开发者或运维人员准备：

- 一个 `SYS_ADMIN` 测试账号。
- 至少一个状态为 `ACTIVE` 的企业及对应企业账号，或一个已经绑定企业的邀请码。
- 一条已经激活的全局相似度规则。

如果只拿到普通账号而没有企业或规则，上传可能返回 `FORBIDDEN`、`ENTERPRISE_NOT_AVAILABLE` 或 `RULE_UNAVAILABLE`，这不是浏览器操作错误。

## 四、项目配置

后端通过 `STEP_PLATFORM_` 前缀读取环境变量。下面是本地测试的最小配置示例。示例密钥只用于说明格式，不能照搬到真实环境，也不要提交到 Git。

```powershell
$env:STEP_PLATFORM_DATABASE_URL = "postgresql+psycopg://step_user:请替换@127.0.0.1:5432/step_platform"
$env:STEP_PLATFORM_MODEL_VAULT = "D:\StepPlatformData\vault"
$env:STEP_PLATFORM_AUTH_PEPPER = "请生成至少32字节的独立随机字符串"
$env:STEP_PLATFORM_JWT_SECRET = "请生成至少32字节的另一条独立随机字符串"
$env:STEP_PLATFORM_RUN_QUEUE_WORKERS = "true"
```

### 4.1 常用配置项

| 配置项 | 必需 | 默认值 | 用途 |
| --- | --- | ---: | --- |
| `STEP_PLATFORM_DATABASE_URL` | 是 | 本地默认地址 | PostgreSQL 连接地址 |
| `STEP_PLATFORM_MODEL_VAULT` | 是 | 无 | STEP 原件和 GLB 预览的存储根目录 |
| `STEP_PLATFORM_AUTH_PEPPER` | 是 | 无 | 登录成功缓存的服务端密钥，至少 32 字节 |
| `STEP_PLATFORM_JWT_SECRET` | 是 | 无 | JWT 签名密钥，必须与 pepper 不同且至少 32 字节 |
| `STEP_PLATFORM_RUN_QUEUE_WORKERS` | 否 | `true` | 是否在当前后端进程启动队列和索引消费者 |
| `STEP_PLATFORM_DEFAULT_TOP_N` | 否 | `5` | 默认返回的候选数量 |
| `STEP_PLATFORM_MAX_TOP_N` | 否 | `50` | 单次对比允许的最大候选数量 |
| `STEP_PLATFORM_MAX_FILE_SIZE_BYTES` | 否 | 由解析限制决定 | 单个 STEP 文件上限 |
| `STEP_PLATFORM_MAX_BATCH_FILES` | 否 | `50` | 批量入库最多文件数 |
| `STEP_PLATFORM_COMPARE_JOB_TIMEOUT` | 否 | `120` 秒 | 单个对比任务计算时限 |
| `STEP_PLATFORM_QUEUE_WAIT_TIMEOUT` | 否 | `60` 秒 | 任务允许等待队列的时限 |
| `STEP_PLATFORM_REAPER_TTL` | 否 | `211` 秒 | 试用预占回收时限，必须大于前两项之和再加 30 秒 |
| `STEP_PLATFORM_SYNC_WAIT_TIMEOUT` | 否 | `25` 秒 | 同步/混合请求最多在 HTTP 内等待多久 |
| `STEP_PLATFORM_OCCT_POOL_SIZE` | 否 | `2` | OCCT 解析子进程数 |
| `STEP_PLATFORM_MAX_QUEUE_DEPTH` | 否 | `32` | 队列最大深度，满时返回 `QUEUE_FULL` |

不要把密码、token、pepper 或 JWT secret 放进 URL、截图、日志或提交记录。系统会拒绝带敏感查询参数的 URL。

## 五、第一次启动

以下命令以 PowerShell 为例。请在项目根目录 `D:\TEmp\StepModelCompare` 执行。

### 5.1 安装后端依赖

```powershell
Set-Location D:\TEmp\StepModelCompare\Platform\backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果机器已经有项目专用虚拟环境，可以直接使用现有环境，不要把依赖安装进 `Demo/.venv`。

### 5.2 创建数据库并执行迁移

先在 PostgreSQL 中创建数据库和账号，再在后端目录执行：

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m alembic upgrade head
```

迁移成功后，数据库应包含 `users`、`enterprises`、`models`、`model_features`、`similarity_rules`、`compare_jobs` 等表。迁移命令只应对测试数据库执行。

### 5.3 启动后端

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到服务启动后，另开一个 PowerShell 检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常结果是：

```json
{"status":"ok"}
```

如果返回 `503`，先检查 PostgreSQL 是否启动、连接地址是否正确、迁移是否完成。后端终端保持运行，不要关闭。

### 5.4 启动 React 工作台

另开 PowerShell：

```powershell
Set-Location D:\TEmp\StepModelCompare\Platform\frontend
npm ci
$env:STEP_PLATFORM_API_TARGET = "http://127.0.0.1:8000"
npm run dev
```

打开 Vite 输出的地址，通常是 `http://127.0.0.1:5173`。开发代理会把 `/api` 请求转发给后端 8000 端口。

## 六、普通用户操作步骤

### 6.1 登录

1. 打开 React 工作台。
2. 点击右上角“登录”。
3. 输入测试账号和密码。
4. 登录成功后，右上角会显示角色。

令牌只保留在当前页面内存中。刷新页面或点击“退出登录”后，需要重新登录。

### 6.2 提交一个 STEP 对比

1. 点击“选择 STEP 文件”。
2. 选择扩展名为 `.step` 或 `.stp` 的文件。
3. 点击“开始对比”。
4. 页面会显示“正在上传查询件”“任务已入队，等待计算”或“正在对比模型”。
5. 等待任务变成“对比完成”。网页默认使用异步模式，浏览器可以按提示继续查询任务。

建议第一次只上传一个小型、确定可读取的测试文件，确认流程后再测试复杂装配体。

### 6.3 阅读结果

对比完成后，页面会出现：

- **候选列表**：显示模型名称和相似度分数。
- **待复核**：对称歧义、数据缺失或门禁不确定的结果会单独放在这里。
- **详情**：查看 D1–D7 的原始测量、有效权重、分项分数和缺失提醒。
- **并排视图**：同时查看查询件和候选件，两个视口共享相机和缩放。
- **叠加视图**：只有通过刚体、手性和对齐门禁的候选可以叠加。叠加使用右手系旋转和平移，不做缩放或镜像。
- **记录选中**：选择候选后点击记录，成功后会显示“已记录选中”。同一候选重复点击不会重复累计。

看到“部分数据未核实”“暂无可用分数”或“待复核”时，不要把它当成系统故障，应打开详情查看缺失原因。

### 6.4 结束操作

确认结果后点击“退出登录”。测试人员应记录：测试文件名、任务编号、任务状态、候选数量、待复核原因和浏览器版本，不要记录密码或完整 token。

## 七、管理员和接口测试

当前还没有完整的管理后台页面，管理员操作通过 API 或 FastAPI Swagger 完成。启动后打开：

```text
http://127.0.0.1:8000/docs
```

先调用 `POST /api/v1/auth/token` 登录，复制返回的 `access_token`，在 Swagger 的 **Authorize** 中输入：

```text
Bearer <access_token>
```

常用接口如下：

| 目的 | 方法和路径 | 权限 |
| --- | --- | --- |
| 登录 | `POST /api/v1/auth/token` | 无 |
| 创建企业 | `POST /api/v1/admin/enterprises` | `SYS_ADMIN` |
| 创建邀请码 | `POST /api/v1/admin/invite-codes` | `SYS_ADMIN` |
| 绑定邀请码 | `POST /api/v1/auth/bind-invite` | 已登录试用用户 |
| 查看模型 | `GET /api/v1/models` | 已登录 |
| 查看待审模型 | `GET /api/v1/admin/models/pending` | `SYS_ADMIN` |
| 批量上传待审模型 | `POST /api/v1/client/models/batch-upload` | 企业账号 |
| 管理员直传 | `POST /api/v1/models/batch-upload` | `SYS_ADMIN` |
| 通过模型 | `POST /api/v1/admin/models/{model_id}/approve` | `SYS_ADMIN` |
| 驳回模型 | `POST /api/v1/admin/models/{model_id}/reject` | `SYS_ADMIN` |
| 查看生效规则 | `GET /api/v1/rules/active` | 已登录 |
| 创建规则 | `POST /api/v1/admin/rules` | `SYS_ADMIN` |
| 激活规则 | `POST /api/v1/admin/rules/{rule_id}/activate` | `SYS_ADMIN` |
| 网页对比 | `POST /api/v1/compare/upload` | 已登录 |
| 网页轮询 | `GET /api/v1/compare/jobs/{job_id}` | 任务所属账号 |

规则请求体字段较多，第一次使用建议在 Swagger 中展开 `ComparisonRule` 查看当前版本的完整字段，不要根据旧文档手写不完整 JSON。

### 7.1 管理员审核流程

1. 企业账号通过批量上传接口上传模型，模型进入 `PENDING`。
2. 系统管理员调用待审列表接口，记录 `model_id`。
3. 确认模型来源和内容后调用 `approve`，模型进入 `INDEXING`，后台完成特征提取后才可用于检索。
4. 不合格模型调用 `reject`，请求体必须填写驳回原因。
5. 通过 `GET /api/v1/models` 检查模型是否进入可见范围。

`PENDING`、`INDEXING`、`APPROVED`、`PARSE_FAILED`、`REJECTED` 是模型台账状态，不要只看上传接口返回的 `accepted` 就认为模型已经可检索。

## 八、推荐的实际测试顺序

### 8.1 冒烟测试

- [ ] `/health` 返回 `status: ok`。
- [ ] 正确账号可以登录，错误密码返回 401。
- [ ] 企业用户能打开工作台并提交一个小 STEP。
- [ ] 任务可以从 `QUEUED` 或 `RUNNING` 进入 `DONE`。
- [ ] 页面能显示候选或明确显示无候选。
- [ ] 详情、并排视图和叠加门禁提示可用。
- [ ] 记录选中后刷新当前结果不会重复累计。
- [ ] 退出登录后不能继续读取旧任务。

### 8.2 业务边界测试

- [ ] 两个企业的账号不能读取对方模型或任务。
- [ ] 试用用户没有邀请码时不能上传企业模型。
- [ ] 无效扩展名、超大文件、损坏 STEP 能得到明确错误。
- [ ] 同一企业重复上传相同 MD5 会被去重。
- [ ] 管理员通过和驳回流程都能留下状态和原因。
- [ ] 规则未激活时，对比能明确返回规则不可用，而不是静默给出分数。

### 8.3 浏览器和性能测试

- 在 Chrome 和 Edge 各执行一次完整流程。
- 检查 WebGL 2 是否开启，确认两个视口都能旋转和缩放。
- 使用 3–5 个不同复杂度的 STEP，记录上传大小、排队时间、计算时间和失败原因。
- 不要在第一次测试就并发上传大量客户原件。队列、存储和 OCCT 资源上限仍需结合真实样本校准。

## 九、常见问题

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| 页面打不开 | Vite 没启动或端口被占用 | 检查 `npm run dev` 输出的地址 |
| 页面能开但登录失败 | 后端未启动、密钥未配、账号不存在 | 检查后端终端、`/health` 和测试账号 |
| `/health` 返回 503 | PostgreSQL 不可达或未迁移 | 检查数据库连接和 `alembic upgrade head` |
| 上传返回 `STORAGE_UNAVAILABLE` | 未配置模型存储或不是 PostgreSQL | 设置 `STEP_PLATFORM_MODEL_VAULT`，使用 PostgreSQL |
| 上传返回 `FORBIDDEN` | 当前角色没有企业上传权限 | 使用企业账号或让管理员绑定企业 |
| 上传返回 `RULE_UNAVAILABLE` | 没有激活可用规则 | 由系统管理员创建并激活规则 |
| 任务长时间排队 | 队列没有消费者或队列已满 | 确认 `STEP_PLATFORM_RUN_QUEUE_WORKERS=true`，查看后端日志 |
| 任务变成 `PARSE_FAILED` | STEP 损坏、格式不支持或模型超出解析限制 | 更换可读文件，记录错误码和文件规模 |
| 候选显示“待复核” | 对称、手性、缺项或门禁未通过 | 查看详情，不要强行当作可靠排名 |
| 叠加按钮不可用 | 没有可靠刚体矩阵或候选预览不存在 | 先用并排视图，检查任务是否为本切片之后生成 |
| 旧任务没有查询件预览 | STEP-PLAT-007 之前的任务没有回填查询 GLB | 重新提交该查询件 |

## 十、当前已知限制和测试边界

1. 管理后台的可视化模型台账、五态筛选、批量审核和完整角色越权矩阵还在后续切片。
2. 当前没有公开注册页面，也没有面向小白用户的一键初始化账号流程。
3. 管理员直传审计、表单上传未认证流量限制和部分依赖升级兼容性仍有延期项。
4. Windows 高负载下测试轮询的 25 秒阈值仍有测试侧 P2 跟踪，不等于生产任务超时。
5. 当前自动化验证使用本机 PostgreSQL 和自制 STEP 夹具，不等于客户真实样本、客户服务器或生产 GPU 验收。
6. 客户部署时还必须补齐 HTTPS、可信代理、备份、原件保留和孤立文件清理策略。

## 十一、开发者回归命令

以下命令用于开发人员复核，不是普通业务人员的日常操作：

```powershell
# 后端
Set-Location D:\TEmp\StepModelCompare
$env:PYTHONPATH = (Resolve-Path Platform\backend).Path
python -m compileall -q Platform/backend
python -m unittest discover -s Platform/backend/tests

# 前端
Set-Location Platform/frontend
npm test
npm run build
```

截至本文更新时间，阶段性回归记录为后端 `67/67`、前端 `3/3`，并包含本机真实 PostgreSQL 用例。每次更换依赖、数据库版本、浏览器或部署方式后，都要重新执行对应测试并记录环境。

## 十二、下一步建议

下一切片建议优先开发“模型台账与人工审核”页面，把现有管理员 API 变成可视化操作，包括状态筛选、批量通过/驳回、驳回原因、管理员直传审计和企业/角色边界提示。与此同时收集客户 20–50 件真实 STEP，冻结部署目标、规则参数和验收门槛，再进入客户样本验收。
