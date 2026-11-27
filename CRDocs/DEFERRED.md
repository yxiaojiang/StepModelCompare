# 有效延期项与跨阶段验收清单

> 本文件只保留当前仍然有效的延期项（P2 建议、依赖真实客户样本或生产部署环境的验证项）。已关闭项随对应任务归档。

## 环境与真实样本依赖项（阶段 1 / 阶段 4 核销）

1. **`PLAT-DEF-001`（OPEN｜客户真实 STEP 样本实测）**
   - **原因**：工作区目前仅有 `Demo/` 自制演示样例，尚无客户 20–50 件真实 `.stp` 历史文件。
   - **验收条件**：客户样本到位后运行阶段 1 盘点脚本，实测 AP203/214 vs AP242 分布、语义 PMI 可读率、材料填写率、单/多闭合实体与开壳占比、单件 OCCT 与单次 `igl` ICP 耗时，并冻结 `K`、`compare_job_timeout`、`reaper_ttl`、`sync_wait_timeout` 与 20–30 组预登记正例集。
2. **`PLAT-DEF-002`（OPEN｜PostgreSQL 真实并发与行锁压测）**
   - **原因**：本地无 PostgreSQL 实例时，SQLite 单测仅能覆盖基础 CRUD 与语法，无法验证 `FOR UPDATE SKIP LOCKED` 队列抢占与 10 路并发 5 次配额预占/迟到拦截。
   - **验收条件**：在真实 PostgreSQL 15+ 实例上跑通队列并发消费测试、10 路并发试用配额预占与迟到结果拦截（`QUOTA_EXPIRED`）压测。
3. **`PLAT-DEF-003`（OPEN｜目标部署环境实测）**
   - **原因**：阶段 1 尚未获取客户服务器清单并冻结唯一首期部署目标（Linux Docker 或 Windows Server）。
   - **验收条件**：在冻结的目标环境完成 OCCT 子进程内存限制（Linux `RLIMIT_AS`/cgroup 或 Windows Job Object）、HTTPS 凭证禁入 URL、以及整机六大验收矩阵实测。

## STEP-PLAT-003 非阻断跟踪项

- **`PLAT-003-P2-1`（OPEN｜PMI 未知类型与零值公差）** 在 D3 评分接入时核对可用语义、未知类型及零值兼容口径。
- **`PLAT-003-P2-2`（OPEN｜闭合体混含游离自由面）** 客户样本出现该结构时验证并补足 D2 体积口径。
- **`PLAT-003-P2-3`（OPEN｜切换持锁扫盘与孤立目录回收）** 通过 `PLAT-DEF-003` 验证目标存储卷上的大库切换等待与回收。
- [原始问题与核销依据](archive/2026-11-27_STEP-PLAT-003_特征提取与版本化存储/REVIEW.md)
