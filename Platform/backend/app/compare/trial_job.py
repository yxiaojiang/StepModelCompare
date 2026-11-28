"""连接常驻队列、授权候选、精排及事务结算，依赖 PostgreSQL、OCCT 池和受信存储卷。"""

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.compare.rule import ComparisonRule
from app.compare.worker import CompareTaskError, run_ranked_compare_task
from app.config import Settings
from app.db.model_features import vault_path
from app.db.models import CompareJob, TrialReservation
from app.geometry_engine.step_input import StepError
from app.geometry_engine.worker_runner import OcctPool
from app.repository.quota import (
    QuotaError, commit_enterprise_results, commit_trial_results, finish_failed_compare, job_actor, require_job_identity,
)
from app.repository.visibility import VisibilityUser, visible_candidate_query


_LOG = logging.getLogger("step_platform")


def run_trial_compare_job(
    settings: Settings, pool: OcctPool, engine: Engine, vault: Path, query: Path, job_id: int,
) -> bool:
    """执行已领取的试用任务。pool 与 vault 提供几何计算和缓存，query 是调度器提供的已校验暂存路径。

    仅本次结算成功返回 True。过期、非试用或已终结任务返回 False，失败只写诊断码。
    数据库或其他意外错误在尝试释放预占后向上传播。任务快照需包含 rule 和 feature_version。
    """
    return _run(settings, pool, engine, vault, query, job_id, require_trial=True)


def run_claimed_compare_job(settings: Settings, pool: OcctPool, engine: Engine, vault: Path, job_id: int) -> bool:
    """调度已领取的持久化任务，按 job_id 解析数据库存储键并执行；仅发布成功返回 True，设施异常向上传播。"""
    return _run(settings, pool, engine, vault, None, job_id, require_trial=False)


def _run(settings, pool, engine, vault, query, job_id, *, require_trial):
    preview = None
    published = False
    try:
        with engine.connect() as connection:
            job = connection.execute(select(CompareJob).where(
                CompareJob.id == job_id, CompareJob.status == "RUNNING",
            )).one_or_none()
            if job is None or (query is None and job.query_storage_path is None):
                return False
            reservation = connection.execute(select(TrialReservation.status, (
                TrialReservation.expires_at > func.now()
            ).label("unexpired")).where(
                TrialReservation.job_id == job_id, TrialReservation.user_id == job.user_id,
            )).one_or_none()
            trial = reservation is not None
            if require_trial and not trial:
                return False
            if trial and (reservation.status != "RESERVED" or not reservation.unexpired):
                return False
            if job.query_storage_path is not None:
                actor = job_actor(job)
                require_job_identity(connection, job, func.now(), lock=False)
                if (actor.role == "TRIAL_USER") != trial:
                    raise ValueError("任务类型与预占记录不一致")
            else:
                actor = VisibilityUser(job.user_id, "TRIAL_USER", None)
            # 只从数据库可见范围生成候选，事务发布前还会复核当前模型权限。
            candidate_ids = connection.execute(visible_candidate_query(actor, job.enterprise_id)).scalars().all()
        if query is None:
            query = vault_path(vault, job.query_storage_path)
            if job.source == "WEB":
                # UUID 原件与预览一一对应，预览随同次 OCCT 网格生成，只有成功结算才可读取。
                preview = vault_path(vault, Path(job.query_storage_path).with_suffix(".glb").as_posix())
        snapshot = job.rule_version_snapshot
        ranking = run_ranked_compare_task(
            settings, pool, engine, vault, query, candidate_ids, ComparisonRule.model_validate(snapshot["rule"]),
            feature_version=snapshot["feature_version"], top_n=snapshot.get("top_n"), query_glb_target=preview,
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
        publish = commit_trial_results if trial else commit_enterprise_results
        published = publish(
            engine, job_id, rows, termination=ranking["termination"], max_untested_upper=ranking["max_untested_upper"],
        )
        return published
    except QuotaError as error:
        finish_failed_compare(engine, job_id, str(error))
        return False
    except (StepError, CompareTaskError) as error:
        finish_failed_compare(engine, job_id, error.code)
        return False
    except Exception:
        finish_failed_compare(engine, job_id, "COMPARE_FAILED")
        raise
    finally:
        if preview is not None and not published:
            try:
                preview.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("无法清理未发布的对比预览")
