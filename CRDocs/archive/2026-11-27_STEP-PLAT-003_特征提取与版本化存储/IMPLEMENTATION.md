# 执行者实施与自测交接记录

> 本文件只保留当前任务最近 3 轮实施记录。[归档与交接规则](README.md)

## 第 1 轮 — STEP-PLAT-003（2026-11-27）

分支 `feat/platform-v1`，审核差异基线 `4232583`。本轮对应开发计划 2.2，承接已独立通过的 [STEP-PLAT-002](../2026-11-27_STEP-PLAT-002_任务级对比Worker与P2收口/INDEX.md)。

### 改动与调用链

| 文件 | 交付 |
| --- | --- |
| `Platform/backend/app/geometry_engine/step_reader.py`、`worker_runner.py` | `OcctPool.extract_features` 在第一层受控子进程内用同一份毫米制 B-Rep 完成特征、网格和可选预览。保留原解析和盘点入口。版本未实现、必需网格缺失、超时或面片超限时报告失败。 |
| `features_d1_d4.py`、`features_d6_d7.py` | D1 原件名词干、XDE 名称/颜色/图层和材料、台账材料优先及反算参数。D2 有效闭合体积累加，开壳显式缺项。D3 语义尺寸、公差和基准的计数、类型以及度/毫米数值。D4 确切曲面面积、面积比例和共边平面/柱面并查集。D6 惯性主轴 B-Rep 边界，D7 内柱面角覆盖、孔径、孔心和孔心距。 |
| `mesh_artifacts.py`、`feature_versions.py` | 产出兼容现有精排加载器的 `vertices`、`faces` 毫米制网格和 `sample_points`。采样为固定种子的 6500 面积加权点、三角形面心和有效顶点。GLB 使用右手系 Y 向上米制坐标，并在特征中保存从计算坐标到场景坐标的矩阵。 |
| `Platform/backend/app/db/model_features.py`、`models.py`、`alembic/versions/0002_feature_catalog.py` | 新增全局单例指针，迁移读取既有生效全局规则版本或初始化为 `1.0.0`。降级回写旧全局规则（若存在）。调用方读取一次指针后，将该版本同时传给 `OcctPool.extract_features` 和 `load_ready_features`。候选仅按已授权 ID、`APPROVED`、版本及 `READY` 读取，缺口不跨版本回退。 |
| `feature_indexing.py` | `build_model_features` 按企业、模型、版本落库和发布成对缓存；`rebuild_feature_version` 后台分批提取、从头增量查漏，再走事务切换。旧版本保留供在途任务使用。 |
| `tests/test_feature_geometry.py`、`tests/test_database.py` | 覆盖真实 OCCT、合成 AP242 和拆面/多实体样例、真实 PostgreSQL 迁移、资产修复、回滚与版本切换竞争。 |

### 写入与切换边界

- 预览与网格先写入同卷暂存目录，同步文件内容后一次重命名发布。数据库事务同时写 `model_features` 记录与入库终态。已发布路径按企业、模型、版本和独立目录隔离。同版本完整产物可重复使用。已知数据库写入失败时清理未引用目录，提交结果不明时保留目录，避免删除可能已被引用的文件。
- 入库提交前，在 PostgreSQL `READ COMMITTED` 事务内先锁全局指针（`FOR SHARE`），再锁模型。切换持指针排他行锁（`FOR UPDATE`），拿到锁后用新快照核对全库 `APPROVED` 和缓存文件。旧版任务若在切换后到达提交点便释放抢占，将模型交回队列按新版本重做。全量和增量扫描各自固定 ID 上界，最后的竞争窗口由切换事务判定。 [PostgreSQL 行锁](https://www.postgresql.org/docs/18/explicit-locking.html)
- 全局指针作为新读写入口的事实来源，旧规则列和环境配置字段保留兼容。当前节点的真实提取算法为 `1.0.0`，事务测试用占位算法模拟后续版本。 `.npz` 的 D5 输入沿用现有加载契约，完整 D5 刚体对齐与评分由计划 2.3 接入。 [OCCT STEP/XDE](https://github.com/Open-Cascade-SAS/OCCT/wiki/step) · [glTF 2.0](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)

### 实测验证

在仓库根目录运行，环境为 Windows、Python 3.13.14、OCCT 绑定 8.0.1.1.0。本机临时 PostgreSQL 18.7 监听回环地址，集成用例自行创建和清理隔离 schema。

```powershell
$py = Join-Path $env:TEMP 'step-plat-001-venv/Scripts/python.exe'
$env:PYTHONPATH = (Resolve-Path Platform/backend).Path
$env:PYTHONIOENCODING = 'utf-8'
$env:STEP_PLATFORM_TEST_POSTGRES_URL = 'postgresql+psycopg://postgres@127.0.0.1:56431/step_plat_001'
$env:STEP_PLATFORM_DATABASE_URL = $env:STEP_PLATFORM_TEST_POSTGRES_URL
& $py -m pip check
& $py -m compileall -q Platform/backend
& $py -m alembic -c Platform/backend/alembic.ini upgrade head --sql
& $py -m unittest discover -s Platform/backend/tests -v
git diff --check HEAD -- Platform/backend CRDocs
git diff --exit-code HEAD -- Demo
```

- 全量 **36/36 项通过**（39.199 秒），其中 **9 项连接真实 PostgreSQL**。Alembic `check` 由隔离 schema 用例执行，无新增迁移差异。通过首版及本轮升级、回滚、再升级、旧规则指针继承、现有版本记录保留，以及 PostgreSQL 离线 SQL 输出。
- 真实数据库与真实 OCCT 用例覆盖入库、同一模型重复索引、同件跨企路径独立、坏文件失败、缓存丢失后查漏与修复。真实数据库配占位几何验证发布后事务回滚、增量补齐、锁竞争两个先后顺序以及按版本精确查询。
- 通过 `OcctPool.extract_features` 扫描 `Demo/samples/rehearsal`，13 件生成有效特征和非空网格/GLB 对，1 件按预期报 `ERR_INVALID_STEP_HEADER`。合成 AP242 回读直径、位置公差和基准，拆面、多实体、开壳、采样重复性和 GLB 二进制坐标回读通过。
- 用 `Demo/.venv/Scripts/python.exe Platform/backend/scripts/step_inventory.py Demo/samples/rehearsal` 回归，常规模式扫描 14 件、13 件解析成功、12 对单次 ICP 通过；设 `--max-mesh-triangles 1` 时，仍保留 13 件解析结果与独立超限诊断，0 对 ICP。
- 统计 `rg --files Platform` 中 28 个手写源码文件（迁移目录除外），最大 451 行（`tests/test_database.py`），1000/1500 两档均未触发。依赖、语法、Git 差异与 Demo 冻结检查通过。

### 未验证边界

- 当前 PMI、D4 拆面和 D7 内孔证据来自 Demo 与合成 STEP。CAD 特有输出、样条近似柱面与复杂修剪柱面仍待客户样本验证。D7 两两距离采用 1024 孔提取上限，实际容量待实测。
- 切换持有指针锁期间会验证全库目标版本的非空缓存引用。大库扫盘时长、生产存储卷故障及断电恢复待目标环境验证。正常事务失败已注入验证，异常退出或提交结果不明留下的孤立目录需后续运维回收。
- `PLAT-DEF-001/002/003` 继续开放。完整 D5、动态剪枝、常驻调度/回收和跨企业在线鉴权由对应后续阶段交付。
