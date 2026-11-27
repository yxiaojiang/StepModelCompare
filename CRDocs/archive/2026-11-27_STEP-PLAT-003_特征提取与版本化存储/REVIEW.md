# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核结论（最新在前）。[归档与交接规则](README.md)

## 第 1 轮独立审核 — STEP-PLAT-003（2026-11-27）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，3 个 P2 非阻断建议）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `4232583`，覆盖计划 2.2 全部改动与新增文件（`0002_feature_catalog.py`、`config.py`、`db/models.py`、`db/model_features.py`、`geometry_engine/feature_versions.py`、`features_d1_d4.py`、`features_d6_d7.py`、`mesh_artifacts.py`、`feature_indexing.py`、`step_reader.py`、`worker_runner.py`、`tests/test_database.py`、`tests/test_feature_geometry.py`）。审核者未修改任何业务代码。

### 重点复核结论

1. **几何与 PMI 缺项语义（通过）**
   - D1 规范化原件名词干（NFKC + casefold）与 XDE 名称/颜色/图层/材料，台账 `registered_material` 优先于 XDE 材料，重提取与修复可从活跃版本继承台账材料；`has_material`/`has_attributes` 显式标注缺项。
   - D2 仅在全部实体为有效闭合实体且无开壳时累加毫米立方体积，开壳或坏实体显式输出 `volume_available=False, volume_mm3=None`；D6 在无有效体积属性时回退表面质量属性并保持右手系惯性主轴。
   - D3 跳过纯图形标注（`_DimensionPresentation`、`_CommonLabel`），区分长度（mm）与角度（deg）数值集合；经独立 OCP 探针实测，角度类型枚举均以 `_Angular` 结尾且 OCCT 读入时确按度输出。
   - D4 共边平面/柱面并查集归并仅考察拓扑共边面，分离同轴孔不被误合并；D7 内柱面朝向公式 `geometry.Direct() == (face.Orientation() == TopAbs_REVERSED)` 与半周角覆盖合并逻辑数学正确，设 1024 孔上限防平方载荷膨胀。
2. **NPZ / GLB 坐标契约（通过）**
   - `.npz` 输出毫米制 `vertices`（float64）、`faces`（int64）和固定种子 `57` 的 `sample_points`（6500 面积加权采样 + 三角面心 + 有效顶点），与 `compare/worker.py:_load_mesh` 和 `Demo/src/geometry.py` 完全兼容。
   - `.glb` 采用右手系 Z-up mm → Y-up m 矩阵 `CAD_TO_SCENE`（行列式 $+10^{-9} > 0$，保留原点），JSON 块 4 字节空格对齐、BIN 块 `<f4`/`<u4` 天然 4 字节对齐、accessor 含 `min`/`max` 符合 glTF 2.0 规范，并将 `glb_coord_matrix` 写入 `cache_refs`。
3. **缓存发布与事务回滚（通过）**
   - 产物先在同卷 `.staging-*` 暂存目录落盘并对 `.npz`/`.glb` 执行 `os.fsync`，再通过单次目录 `rename` 发布到不可变 `uuid4` 路径；路径按企业/公共演示/模型 ID/版本隔离，严禁跨企业共享缓存。
   - 数据库写入在 `commit_started = True` 前失败时（如约束冲突），`except BaseException` 自动 `shutil.rmtree(published)` 清理未引用目录；提交回执不明时保留目录防误删，边界处理合理。
   - `OcctPool` 严格要求新生成网格/GLB，超时或超限不覆盖旧目标文件并在 `finally` 清理槽位残余。
4. **迁移兼容性（通过）**
   - `0002_feature_catalog.py` 通过 `CHECK (id = 1)` 强制单例表，升级时 `COALESCE` 继承既有生效 `GLOBAL` 规则的 `active_feature_version`（无规则时默认 `'1.0.0'`），降级时回写生效 `GLOBAL` 规则并删表；保留 `similarity_rules.active_feature_version` 与 `Settings.active_feature_version` 兼容旧字段。
   - Alembic `check` 无未迁移模型漂移，`upgrade head --sql` 离线 SQL 正确，PostgreSQL 与 SQLite 升降级往返通过。
5. **PostgreSQL 入库与版本切换两种竞争顺序（通过）**
   - 锁序严格无环：入库 `_finish(admit=True)` 持 `feature_catalog (FOR SHARE)` → `models (FOR UPDATE)`；切换 `switch_active_feature_version` 仅持 `feature_catalog (FOR UPDATE)`；后台重提取仅持 `models (FOR UPDATE)`，无死锁可能。
   - **竞争顺序 A（切换先于入库提交）**：切换提交后，迟到旧版入库在 `READ COMMITTED` 下拿 `FOR SHARE` 读到新版本指针，触发 `active != version`，清空 `indexing_started_at` 并返回 `RETRY (FEATURE_VERSION_CHANGED)`，交回队列按新版重做，不写入旧版 `APPROVED`。
   - **竞争顺序 B（入库先拿 `FOR SHARE` 并提交，切换等待）**：切换的 `FOR UPDATE` 阻塞至入库提交；因显式启用 `READ COMMITTED`，切换获锁后的 `_missing(target)` 查询使用新 MVCC 快照，必然发现刚入库模型缺少目标版本并抛 `FeatureVersionConflict` 回滚，绝不发生带缺口切换。
   - `rebuild_feature_version` 采用双轮固定 ID 上界扫描（全量 + 从头增量查漏），配合切换事务终检闭环。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议，后续阶段跟进）**：
  1. **`PLAT-003-P2-1`（OPEN｜PMI 未知类型与零值公差的过滤口径）**
     - **位置**：`Platform/backend/app/geometry_engine/features_d1_d4.py:165-186`
     - **说明**：`extract_pmi` 遇到 `dimensions` 的 `Location_None` 时先计入 `counts`/`has_pmi` 再跳过数值；遇到 `geometric_tolerances` 的 `GeomToleranceType_None` 或未设值（默认 `0.0`）时仍会将 `0.0` 写入 `numeric_values_mm`。不阻断 2.2，建议在 2.3 接入 D3 评分或客户真实 AP242 样本盘点（`PLAT-DEF-001`）时统一过滤口径。
  2. **`PLAT-003-P2-2`（OPEN｜闭合实体混含游离自由面时的 D2 体积口径）**
     - **位置**：`Platform/backend/app/geometry_engine/step_reader.py:195-212`、`features_d1_d4.py:121-143`
     - **说明**：当前通过 `open_shell_count == 0` 且 `solid_count == closed_solid_count` 判定体积可用；若复合体同时含闭合实体与未归入 `SHELL` 的游离 `FACE`，D2 仍输出闭合体体积而 D4 面积包含游离面。实务罕见，不阻断 2.2，若客户样本出现此类文件再补自由面检查。
  3. **`PLAT-003-P2-3`（OPEN｜切换持排他锁期间全库扫盘时长与运维回收）**
     - **位置**：`Platform/backend/app/db/model_features.py:74-91`
     - **说明**：`switch_active_feature_version` 在持有 `feature_catalog` `FOR UPDATE` 行锁期间逐条 `stat()` 核验全库 `APPROVED` 缓存文件，大库规模下可能拉长入库提交等待时间；提交结果不明产生的孤立目录亦需运维回收。逻辑正确不阻断 2.2，纳入目标部署环境压测（`PLAT-DEF-003`）评估。

### 独立验证结果与证据分级

审核者独立复跑以下验证（Windows，Python 3.13.14，`cadquery-ocp-novtk 8.0.1.1.0`，本机临时 PostgreSQL 18.7 端口 `56432`，测毕即停库）：

- **真实 PostgreSQL 18.7 证据（9/9 通过）**：全量 `unittest discover -s Platform/backend/tests -v` 共 **36/36 通过**（39.280s），覆盖 `0001 → 0002` 升降级与旧全局规则指针继承、Alembic `check` 无漂移、真实 OCCT+PostgreSQL 入库幂等/跨企路径隔离/缓存丢失修复；以及**真实 PostgreSQL + 占位几何桩（`_VersionFixturePool`）**验证的事务回滚清盘、双轮增量查漏和入库/切换两种锁竞争顺序。
- **SQLite / 无外部库单测证据（27/27 通过，9 跳过）**：独立无库模式下 27 项单测与 SQLite 迁移/约束测试通过（34.734s）。
- **Demo 样例与合成 STEP 证据**：`Demo/samples/rehearsal/queries/SHOW-POSE-INCH.step` 验证英寸转毫米、固定种子采样重复性与 GLB 二进制坐标矩阵回读；合成 AP242/拆面/多实体/开壳样例验证 PMI、共边归并、孔系提取与开壳缺项；审核者额外运行独立 OCP 探针确认 `_Angular` 枚举覆盖全部角度尺寸类型且 OCCT 按度输出。
- **工程门禁证据**：`compileall` 通过；`alembic upgrade head --sql` 离线 SQL 正确；`git diff --check 4232583 -- Platform/backend CRDocs` 通过；`git diff --exit-code 4232583 -- Demo` 通过（冻结目录零改动）；手写源码最大 451 行（`tests/test_database.py`），生产模块最大 297 行（`worker_runner.py`），远低于 1000/1500 行门禁；全中文注释与 Docstring 合规。

### 未验证边界

- 客户真实 20–50 件 `.stp`/`.step` 样本的 AP242 语义 PMI 可读率、英寸/公制混合 PMI 换算、样条近似柱面、复杂修剪柱面及 1024 孔容量上限尚未实测（`PLAT-DEF-001`）。
- 仅 `1.0.0` 为真实几何提取算法，`_VERSION_FIXTURE = "1.1.0"` 及两种竞争顺序/回滚测试中的第二版本均由占位几何桩（`_VersionFixturePool`）模拟。
- 大库规模下版本切换持锁扫盘耗时、生产存储卷故障/断电恢复、孤立目录回收及目标部署环境内存限制尚未实测（`PLAT-DEF-003`）；完整 D5 24 初值精排、动态上界剪枝与安全门禁留待计划 2.3 交付。
