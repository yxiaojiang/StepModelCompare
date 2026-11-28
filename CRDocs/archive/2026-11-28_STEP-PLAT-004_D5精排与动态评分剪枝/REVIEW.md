# 独立代码审核记录

> 本文件只保留当前任务最近 3 轮独立审核结论（最新在前）。[归档与交接规则](../../README.md)

## 第 1 轮独立审核 — STEP-PLAT-004（2026-11-28）

- **审核结论**：`APPROVED`（0 个 P0，0 个 P1，1 个 P2 非阻断建议；`PLAT-003-P2-1` 在 `1.0.0` 评分侧兼容口径复核通过并置 `RESOLVED`）
- **审核基线与范围**：分支 `feat/platform-v1`，差异基线 `1e0fc76`，覆盖计划 2.3/2.4 全部改动与新增文件（`geometry_engine/alignment_d5.py`、`compare/rule.py`、`compare/scoring.py`、`compare/ranking.py`、`compare/worker.py`、`tests/test_alignment_d5.py`、`tests/test_ranked_compare.py`）。审核者未修改任何业务代码。

### 重点复核结论

1. **24 初值与右手刚体约束（通过）**
   - `alignment_d5.py:_seeds` 从 6 种主轴排列 × 8 种符号组合中严格按行列式符号筛出 24 个右手系初值（$\det = +1$，独立探针验证 24/24 全为 $+1$）与 24 个反射诊断初值（$\det = -1$）。
   - 双向 Trimmed ICP（`_refine`）中点到面雅可比 $(p \times n, n)$ 与残差 $n \cdot (q - p)$ 符号一致；秩不足时回退点到点 SVD 并以 `np.diag([1, 1, sign(det(V U^T))])` 修正反射；发布姿态前再次以 `_orthogonal`（`det > 0` 且正交误差 $\le 10^{-8}$）硬校验，确保任何发布姿态严格属于右手刚体群 $SE(3)$。
2. **GLB 矩阵契约（通过）**
   - `prepare_surface` 校验查询与候选的 `glb_coord_matrix` 均为统一尺度、正行列式的 4×4 相似变换；`align_surfaces` 仅在门禁通过（`reason is None`）时发布 `transform_mm` 及 `scene_matrix = query.scene @ best.pose @ inv(candidate.scene)`（即 $C_{\text{新}} T C_{\text{旧}}^{-1}$），并通过 Demo 英寸件已知控制点与矩阵等式实测验证。
3. **镜像与对称件分流（通过）**
   - 对称件存在多个通过拟合阈值且在采样质心处差异超过歧义阈值的右手姿态时，分流为 `SYMMETRY_AMBIGUITY` 且扣留 `transform_mm` 与 `scene_matrix`（Demo 对称件实测 `accepted_poses >= 2` 且 `scene_matrix is None`）。
   - 镜像诊断在 `not disagreements and best.key[0] >= limits.mirror_margin_mm` 时触发（即使右手勉强落在拟合阈值内也能捕获轻微手性镜像），反射显著优于右手时分流为 `HANDEDNESS_MISMATCH`；反射结果仅写入 `mirror_diagnostic`，绝不进入 D5 计分或叠加矩阵。
4. **D5 零权重门禁（通过）**
   - `ranking.py` 中几何门禁独立于 D5 权重：即使 `weights["D5"] == 0`，候选仍须通过 `refine` 门禁才可进入可靠榜 `results` 或充当剪枝榜尾；被门禁拒绝或缓存损坏的候选均计入 `attempts` 预算并转入 `needs_review`，防止坏缓存绕开 `max_refine_k`。
   - 无加权 D5 且全量或精确剪枝完成时输出 `exact_no_d5`，预算先耗尽时显式输出 `truncated_by_k` 及 `max_untested_upper`。
5. **缺项重分后的真实上界（通过）**
   - `scoring.py:ScoreCard.upper_bound` 与 `total` 共用 `fsum` 逐项求和路径，仅将 `PENDING_D5` 替换为其重分配后的 `effective_weight`（对应 D5 满分 100 的精确上界）；因 `apply_surface` 保证 `score <= 100.0`，IEEE-754 下 `total() <= upper_bound()` 严格成立（审核者独立 200 组随机小数权重 + 缺项组合探针零违例，40 组固定种子随机对拍全量基准一致）。
   - `D3_TO_D7` 仅在双方无 PMI、双方孔系可用且至少一侧有孔时触发；双方零孔时 D3 不转 D7，而用户显式启用 D7 时双方零孔仍为有效证据。空有效集返回 `None` 并分流 `INSUFFICIENT_FEATURES`，杜绝除零。
6. **同分排序与 K 截断（通过）**
   - `work` 按 `(-upper, -geo, model_id)` 确定性排序，可靠榜 `best` 按 `(-similarity_score, model_id)` 排序。
   - 剪枝时严格区分 `upper < floor.score`（剩余全部精确剪枝 `break`）与 `upper == floor.score and model_id > floor.model_id`（仅剪当前较大 ID 并 `continue`，保留后续可能同分但 ID 更小的候选），且预算判断按 `min(budget, rule.max_refine_k)` 执行，截断时输出 `truncated_model_ids` 与 `max_untested_upper`。
7. **`PLAT-003-P2-1` 旧版 PMI 兼容口径（`RESOLVED`）**
   - `scoring.py:_pmi` 在不修改已落库 `1.0.0` 特征的前提下：仅含 `Location_None` / `geometric_tolerances.None` 等未知类型时返回 `None`（按缺项处理）并记 `PMI_UNKNOWN_TYPE`；未知公差与有效语义共存时清空该侧长度数值并标 `PMI_NUMERIC_UNVERIFIED` + `D3:PARTIAL_UNVERIFIED`（独立探针验证）；已知公差共存时剔除无法区分默认/显式的 `0.0` 长度值并标 `PMI_ZERO_UNVERIFIED`，保留可用类型与角度分布。1.0.0 评分侧口径闭环，`PLAT-003-P2-1` 置为 `RESOLVED`（量产 AP242 实测继续归 `PLAT-DEF-001`）。

### 分级问题清单

- **P0（阻断）**：无
- **P1（阻断）**：无
- **P2（非阻断建议）**：
  1. **`PLAT-004-P2-1`（OPEN｜D1 名称编辑距离选取词典序首项的敏感性）**
     - **位置**：`Platform/backend/app/compare/scoring.py:190-197`
     - **说明**：`_names` 取 `min(a)` 与 `min(b)` 做 `SequenceMatcher` 编辑距离，保证确定性与 O(1) 复杂度，但当上传原件名带数字/日期前缀时，`min(a)` 可能选中临时文件名而非 `product_names`，拉低编辑分（词元 Jaccard 仍覆盖全集）。不阻断本轮，后续在客户真实样本实测 D1 区分度时按需调整。
- **历史跟踪项核销**：
  - **`PLAT-003-P2-1`（`RESOLVED`）**：`1.0.0` 评分侧未知 PMI 与零公差过滤兼容口径经代码走查与独立探针复核通过，不破坏既有提取契约；客户真实 AP242 样本验证继续由 `PLAT-DEF-001` 跟踪。

### 独立验证结果与证据分级

审核者独立复跑以下验证（Windows，Python 3.13.14，`cadquery-ocp-novtk 8.0.1.1.0`，本机临时 PostgreSQL 18.7 端口 `56432`，测毕即停库）：

- **全量回归与真实 PostgreSQL 18.7 证据（48/48 通过，63.816s）**：9 项既有 PostgreSQL 集成用例（迁移、Alembic `check`、行锁队列、版本切换两种竞争与事务回滚）全部通过；其余 39 项覆盖新交付的 D5 对齐、评分、剪枝与排序任务入口。
- **新排序入口的数据库与几何证据边界**：`RankedWorkerTests.test_demo_pose_and_cached_rule_change` 使用 **SQLite 替身 + 真实 OCCT + 真实 libigl + Demo 英寸件** 串通，验证姿态控制点误差、GLB 矩阵、版本隔离、坏缓存/非 `APPROVED` 诊断及改规则免重读 STEP（不代表 PostgreSQL 并发压测）。
- **Demo 样例、合成网格与占位精排桩证据**：Demo 对称件（`SHOW-SYMMETRIC` / `SHOW-S01`）验证真实 `SYMMETRY_AMBIGUITY`；合成手性四面体与拆面验证 `HANDEDNESS_MISMATCH` 与抗拆面；上界剪枝、40 组全量对拍、同分 ID 与零权重门禁使用占位精排桩验证算法正确性。
- **审核者独立探针证据**：验证 `_seeds` 24 个右手初值 $\det=+1$ 与 24 个反射初值 $\det=-1$；200 组随机小数权重与缺项组合验证 `total() <= upper_bound()` 零违例；验证 `PLAT-003-P2-1` 未知公差与有效尺寸共存时触发 `PMI_NUMERIC_UNVERIFIED` 与 `D3:PARTIAL_UNVERIFIED`。
- **工程门禁证据**：`pip check`、`compileall`、`git diff --check 1e0fc76 -- Platform/backend CRDocs`、`git diff --exit-code 1e0fc76 -- Demo` 全部通过；手写源码最大 451 行（`tests/test_database.py`），生产模块最大 326 行（`compare/scoring.py`），远低于 1000/1500 行门禁；全中文注释与 Docstring 合规。

### 未验证边界

- 客户真实 20–50 件 STEP 样本的量产 AP242 PMI、真实镜像/近对称阈值、D1–D7 默认权重与零分阈值、Top-5 找回率与 P95 耗时待实测签认（`PLAT-DEF-001`）。
- 公开 API 鉴权、跨企业 `apply_visibility_scope` 候选授权生成、规则持久化与快照落库、PostgreSQL 队列调度与试用配额 CAS 迟到拦截、前端三维工作台与端到端并发压测由阶段 3/4 交付验证（`PLAT-DEF-002`、`PLAT-DEF-003`）。
