# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录。[归档与交接规则](README.md)

## 第 1 轮 — STEP-PLAT-004（2026-11-27）

分支 `feat/platform-v1`，审核差异基线 `1e0fc76`。承接已独立通过的 [STEP-PLAT-003](archive/2026-11-27_STEP-PLAT-003_特征提取与版本化存储/INDEX.md)。按用户要求合并交付正式计划 2.3 的 D5 与几何门禁、2.4 的评分与剪枝。

### 改动与调用链

| 文件 | 交付 |
| --- | --- |
| `Platform/backend/app/geometry_engine/alignment_d5.py` | 24 个惯性主轴右手系初值，双向 Trimmed ICP，点到面秩不足时点到点 SVD 并修正行列式。以双向 RMS、P95、最大偏差排序，按严格去重与拟合阈值判断歧义。 |
| `Platform/backend/app/compare/worker.py` | 新入口 `run_ranked_compare_task` 读取单版本的已授权、已审核候选和规则快照。复用 OCCT 池提取查询，或直接接收同版本的查询特征与 NPZ 以重算。继承单任务 `spawn` 监督与截止值，任务内只建立一次查询 AABB。缺特征与损坏网格单列。 |
| `Platform/backend/app/compare/rule.py` 与 `scoring.py` | 校验七维权重总和和零分阈值。支持双方缺项重分、D3 转 D7、单方 ZERO/NEUTRAL/REALLOCATE 及空有效集。D1 细分名称、材料、属性和反算参数；D2 体积、D3 PMI 计数/类型/分量纲分布、D4 面积/曲面分布/合并面数、D6 主轴尺寸、D7 孔数/孔径/间距。D5 双向各取较大误差，三项 4:2:1。 |
| `Platform/backend/app/compare/ranking.py` | 用已知带权分加 D5 有效权重的满分贡献算上界。可靠榜按得分降序、模型 ID 升序；待复核不作为榜尾。精确剪枝、预算截断及剩余上界分别记录。 |
| `Platform/backend/tests/test_alignment_d5.py` 与 `test_ranked_compare.py` | 回归真实 Demo 英寸件、对称件、合成镜像/拆面、D1–D7 缺项、同体积异形、重打分、剪枝边界和全量对照。 |

### 关键口径

- 可靠候选必须通过几何门禁。D5 权重为零时，配准只用于门禁，可能仍消耗精排预算。没有加权 D5 且完成有界验证或确证剪枝时，结果为 `exact_no_d5`。预算耗尽且仍有可能进入可靠榜的候选时为 `truncated_by_k`，同步给出候选 ID 和最大未测上界。精排尝试计数包含待复核和缓存错误，避免坏缓存绕开预算。
- 门禁使用独立拟合和歧义阈值。对称件若有多个合格且明显不同的姿态，记录 `SYMMETRY_AMBIGUITY`。当右手拟合距离超过镜像诊断起点时，允许单独测试反射距离；反射达到拟合阈值且显著改善时，记录 `HANDEDNESS_MISMATCH`。反射距离仅作诊断；只有门禁通过才发布毫米刚体与 `C_查询 T C_候选^-1`。阈值是待签认的开发建议。[libigl AABB](https://libigl.github.io/libigl-python-bindings/api/igl/#aabb) · [Kabsch–Umeyama 算法](https://doi.org/10.6028/jres.124.028)
- `PLAT-003-P2-1` 已在评分侧处理，待独立复核。类型计数取可识别语义，未知类型独占时视为缺项。版本 `1.0.0` 的数值多重集缺少逐值来源，出现未知公差类型且仍有有效语义时取消该侧长度数值证据；有已知公差类型时过滤无法区分显式与默认的零长度值。保留可用类型与角度信息并标出未核实。真实 OCP 探针确认公差未赋值与显式设 0.0 均通过 `GetValue()` 返回 0.0。此口径沿用既有提取契约和版本。
- 双方无 PMI 的 D3 权重可转 D7。双方孔系数据均可用且至少一侧检测出孔时才转入，其余按剩余有效维度重分；若 D7 原本启用，检测出双方零孔仍是有效的 D7 自身证据。

### 实测验证

Windows Python 3.13.14。临时 PostgreSQL 18.7 在回环地址运行，集成测试使用隔离 schema。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path Platform/backend).Path
$env:PYTHONIOENCODING = 'utf-8'
$env:OPENBLAS_NUM_THREADS = '1'
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56431/step_plat_001'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m unittest discover -s Platform/backend/tests -v
git diff --check 1e0fc76 -- Platform/backend CRDocs
git diff --exit-code 1e0fc76 -- Demo
```

- **48/48 测试通过**（56.693 秒）。其中 9 个真实 PostgreSQL 用例回归既有事务、版本切换、队列行锁和迁移；Alembic `check` 无漂移。新排序入口用 SQLite、真实 OCCT、真实 libigl 和 Demo STEP 文件验通，候选含坏缓存、待审核与同版本可用模型。
- Demo 英寸件 24 个初值，经已知控制点断言校核候选至查询姿态；GLB 矩阵按计算坐标验证。Demo 对称件进入待复核且无叠加姿态。合成镜像件触发左右手性门禁，同曲面跨网格拆分仍贴合。
- 评分单测覆盖权重边界、缺项三种策略、D3 转 D7 和空有效集。40 组固定种子的上界剪枝与全量评分的可靠榜一致。另验证有效 D5 权重放大、同分模型 ID、缓存失败和零权重门禁。
- `pip check`、语法、Git 差异与 Demo 冻结检查通过。手写源码共 34 个，最大 451 行（数据库用例），生产文件最大 326 行（评分模块），1000/1500 行门禁均未触发。

### 未验证边界

- 量产 AP242 导出器的 PMI 差异、真实件镜像/近对称判定、候选规模、存储与进程内存实测、Top-5 与 P95 待客户样本和目标环境验证。
- 本轮返回内部服务结果。用户态授权候选生成、规则持久化与规则版本快照落库、队列调度、配额 CAS、前端仅并排显示和端到端并发由对应后续阶段接入。后续公开入口必须只传入已授权的候选 ID。
