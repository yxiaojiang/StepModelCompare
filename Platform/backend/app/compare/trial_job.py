"""串接已领取的试用任务、授权候选、精排及 CAS 发布。由后续队列执行器提供受信查询路径。"""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.compare.rule import ComparisonRule
from app.compare.worker import CompareTaskError, run_ranked_compare_task
from app.config import Settings
from app.db.models import CompareJob, TrialReservation
from app.geometry_engine.step_input import StepError
from app.geometry_engine.worker_runner import OcctPool
from app.repository.quota import commit_trial_results, release_trial_quota
from app.repository.visibility import VisibilityUser, visible_candidate_query


def run_trial_compare_job(
    settings: Settings, pool: OcctPool, engine: Engine, vault: Path, query: Path, job_id: int,
) -> bool:
    """执行已领取的试用任务。pool 与 vault 提供几何计算和缓存，query 是调度器提供的已校验暂存路径。

    仅本次结算成功返回 True。过期、非试用或已终结任务返回 False，失败只写诊断码。
    数据库或其他意外错误在尝试释放预占后向上传播。任务快照需包含 rule 和 feature_version。
    """
    with engine.connect() as connection:
        job = connection.execute(select(CompareJob.user_id, CompareJob.rule_version_snapshot).join(
            TrialReservation, (TrialReservation.job_id == CompareJob.id) & (TrialReservation.user_id == CompareJob.user_id),
        ).where(
            CompareJob.id == job_id, CompareJob.status == "RUNNING", TrialReservation.status == "RESERVED",
            TrialReservation.expires_at > func.now(),
        )).one_or_none()
        if job is None:
            return False
        # 只从数据库可见范围生成候选。结算前再次校验范围，防止运行期间撤下模型后发布结果。
        candidate_ids = connection.execute(visible_candidate_query(
            VisibilityUser(job.user_id, "TRIAL_USER", None),
        )).scalars().all()
    try:
        snapshot = job.rule_version_snapshot
        ranking = run_ranked_compare_task(
            settings, pool, engine, vault, query, candidate_ids, ComparisonRule.model_validate(snapshot["rule"]),
            feature_version=snapshot["feature_version"],
        )
        rows = [
            {
                "model_id": row["model_id"], "rank": row.get("rank"), "similarity_score": row["similarity_score"],
                "needs_review": row["needs_review"], "review_reason": row["review_reason"],
                "breakdown_json": {"dimensions": row["breakdown"], "warnings": row["warnings"]},
                "align_matrix_json": (row.get("alignment") or {}).get("scene_matrix"),
            }
            for group in ("results", "needs_review") for row in ranking[group]
        ]
        return commit_trial_results(
            engine, job_id, rows, termination=ranking["termination"], max_untested_upper=ranking["max_untested_upper"],
        )
    except (StepError, CompareTaskError) as error:
        release_trial_quota(engine, job_id, error.code)
        return False
    except Exception:
        release_trial_quota(engine, job_id, "COMPARE_FAILED")
        raise
