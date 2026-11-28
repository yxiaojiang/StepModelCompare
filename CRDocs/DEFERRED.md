# 有效延期项与跨阶段验收清单

> 本文件只保留当前仍然有效的延期项（P2 建议、依赖真实客户样本或生产部署环境的验证项）。已关闭项随对应任务归档。

## 环境与真实样本依赖项（阶段 1 / 阶段 4 核销）

1. **`PLAT-DEF-001`（OPEN｜客户真实 STEP 样本实测）**
   - **原因**：工作区目前仅有 `Demo/` 自制演示样例，尚无客户 20–50 件真实 `.stp` 历史文件。
   - **验收条件**：客户样本到位后运行阶段 1 盘点脚本，实测 AP203/214 vs AP242 分布、语义 PMI 可读率、材料填写率、单/多闭合实体与开壳占比、单件 OCCT 与单次 `igl` ICP 耗时，并冻结 `K`、`compare_job_timeout`、`reaper_ttl`、`sync_wait_timeout` 与 20–30 组预登记正例集。
2. **`PLAT-DEF-002`（OPEN｜目标环境 PostgreSQL 整机并发与行锁压测）**
   - **已有证据**：STEP-PLAT-005 已在本机 PostgreSQL 18.7 独立验证队列抢占、10 路并发配额事务与迟到拦截，见 [独立审核](archive/2026-11-28_STEP-PLAT-005_鉴权隔离与试用配额/REVIEW.md)。
   - **剩余验收条件**：目标部署环境联通上传、异步调度、回收和 HTTP 结果交付，验证并发消费、有效/损坏件混合 10 路、迟到结果拦截以及多进程鉴权限流。
3. **`PLAT-DEF-003`（OPEN｜目标部署环境实测）**
   - **原因**：阶段 1 尚未获取客户服务器清单并冻结唯一首期部署目标（Linux Docker 或 Windows Server）。
   - **验收条件**：在冻结的目标环境完成 OCCT 子进程内存限制（Linux `RLIMIT_AS`/cgroup 或 Windows Job Object）、HTTPS 凭证禁入 URL、以及整机六大验收矩阵实测。工作台还需核对目标浏览器/GPU 的 WebGL 表现、查询原件与 GLB 的保留期、容量告警及孤立文件处置。

## STEP-PLAT-003 非阻断跟踪项

- **`PLAT-003-P2-2`（OPEN｜闭合体混含游离自由面）** 客户样本出现该结构时验证并补足 D2 体积口径。
- **`PLAT-003-P2-3`（OPEN｜切换持锁扫盘与孤立目录回收）** 通过 `PLAT-DEF-003` 验证目标存储卷上的大库切换等待与回收。
- [仍开放项的原始审核](archive/2026-11-27_STEP-PLAT-003_特征提取与版本化存储/REVIEW.md)

## STEP-PLAT-004 非阻断跟踪项

- **`PLAT-004-P2-1`（OPEN｜D1 名称编辑距离词典序首项敏感性）** 客户真实样本实测 D1 区分度时，评估带数字或日期前缀的原件名是否拉低编辑分，以及是否优先使用 `product_names`。见 [STEP-PLAT-004 独立审核](archive/2026-11-28_STEP-PLAT-004_D5精排与动态评分剪枝/REVIEW.md)。

## STEP-PLAT-005 非阻断跟踪项

- **`PLAT-005-P2-2`（OPEN｜多进程共享限流与可信代理真实 IP）** 随 `PLAT-DEF-002/003` 在目标环境验证可信代理透传、防伪造及跨进程限流。
- [原始审核](archive/2026-11-28_STEP-PLAT-005_鉴权隔离与试用配额/REVIEW.md)

## STEP-PLAT-006 非阻断跟踪项

- **`PLAT-006-P2-1`（OPEN｜管理员直传审计、表单鉴权前置防大包与 Starlette 私有属性兼容）** 后续接入管理后台审计页时补记管理员直传 `AuditLog`；目标部署环境在反向代理层收紧无 Header 表单上传的未认证限流/超时（随 `PLAT-DEF-003`）；升级 Starlette/FastAPI 时回归校验 `uploads.py:65` 异常文件清理兼容。见 [STEP-PLAT-006 独立审核](archive/2026-11-28_STEP-PLAT-006_五态入库与对比调度/REVIEW.md)。

## STEP-PLAT-007 非阻断跟踪项

- **`PLAT-007-P2-1`（OPEN｜Windows 高负载下 `WorkflowTests._poll` 25 秒硬超时偶发抖动）** `test_workflow_api.py:113-120` 的 25 秒硬超时在全量并发运行 Windows `spawn` + OCCT + D5 精排时偶发偏紧，后续切片适度放宽测试轮询上限即可。见 [STEP-PLAT-007 独立审核](archive/2026-11-28_STEP-PLAT-007_React三维对比工作台/REVIEW.md)。
