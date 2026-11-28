"""管理试用任务的入队、预占与结算。依赖 SQLAlchemy，在短事务内维护任务和配额一致性。"""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.engine import Connection, Engine

from app.config import Settings
from app.db.models import CompareJob, CompareResult, TrialReservation, User
from app.repository.visibility import VisibilityUser, visible_candidate_query

# 为同一数据库中的对比任务入队保留固定事务锁键，后续全量入队入口须共用此门禁。
_QUEUE_ADMISSION_LOCK = 781_340_021
_OPEN = ("QUEUED", "RUNNING")


class QuotaError(ValueError):
    """携带可映射到业务响应的配额或任务错误码。"""


def enqueue_trial_compare(
    engine: Engine, settings: Settings, actor: VisibilityUser, *, source: str,
    query_filename: str, query_md5: str, rule_snapshot: dict, auth_version: int,
    now: datetime | None = None,
) -> int:
    """为已鉴权试用账号创建任务并同步预占，返回任务 ID。

    settings 提供队列与回收上限，actor 和 auth_version 来自鉴权，source、文件名、摘要和规则快照为任务元信息。
    队列满、配额不足或身份变化抛 QuotaError，事务回滚时不会留下可领取任务。
    """
    if actor.role != "TRIAL_USER" or actor.enterprise_id is not None:
        raise QuotaError("TRIAL_QUOTA_EXCEEDED")
    if engine.dialect.name == "postgresql":
        engine = engine.execution_options(isolation_level="READ COMMITTED")
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # 在 READ COMMITTED 下，拿到事务锁后以新的语句快照统计，避免并发入口突破队列上限。
            connection.execute(select(func.pg_advisory_xact_lock(_QUEUE_ADMISSION_LOCK)))
        moment = _now(connection, now)
        depth = connection.execute(select(func.count()).select_from(CompareJob).where(
            CompareJob.status.in_(_OPEN),
        )).scalar_one()
        if depth >= settings.max_queue_depth:
            raise QuotaError("QUEUE_FULL")
        changed = connection.execute(update(User).where(
            User.id == actor.user_id, User.role == "TRIAL_USER", User.is_trial.is_(True),
            User.enterprise_id.is_(None), User.is_active.is_(True), User.auth_version == auth_version,
            (User.locked_until.is_(None) | (User.locked_until <= moment)),
            User.used_count + User.in_flight_count < 5,
        ).values(in_flight_count=User.in_flight_count + 1)).rowcount
        if changed != 1:
            raise QuotaError("TRIAL_QUOTA_EXCEEDED")
        job_id = connection.execute(CompareJob.__table__.insert().values(
            user_id=actor.user_id, source=source, query_filename=query_filename, query_md5=query_md5,
            rule_version_snapshot=rule_snapshot, status="QUEUED", created_at=moment,
        ).returning(CompareJob.id)).scalar_one()
        connection.execute(TrialReservation.__table__.insert().values(
            user_id=actor.user_id, job_id=job_id, status="RESERVED", created_at=moment, updated_at=moment,
            expires_at=moment + timedelta(seconds=settings.reaper_ttl),
        ))
        return job_id


def commit_trial_results(
    engine: Engine, job_id: int, results: Sequence[dict], *, termination: str | None = None,
    max_untested_upper: float | None = None, now: datetime | None = None,
) -> bool:
    """发布一次试用任务的整批结果。仅本次完成实扣并发布时返回 True。

    results 是 CompareResult 字段字典集合，termination 与 max_untested_upper 来自精排。
    超时置 QUOTA_EXPIRED 并返回 False。重复、未领取或缺少预占的任务返回 False，越界候选抛 QuotaError。
    """
    with engine.begin() as connection:
        job = _locked_job(connection, job_id)
        if job is None or job.status != "RUNNING":
            return False
        moment = _now(connection, now)
        # 结果插入与最终状态必须在 CAS、实扣所在事务中，任何写入失败均回滚整次结算。
        committed = _transition(connection, job, "COMMITTED", moment, unexpired=True)
        if not committed:
            expired = _transition(connection, job, "EXPIRED", moment, unexpired=False)
            if expired:
                _deduct_in_flight(connection, job.user_id)
            _fail(connection, job_id, "QUOTA_EXPIRED", moment)
            return False

        if results:
            _check_result_visibility(connection, job.user_id, {result["model_id"] for result in results})
        _deduct_in_flight(connection, job.user_id, commit=True)
        if results:
            connection.execute(CompareResult.__table__.insert(), [dict(result, job_id=job_id) for result in results])
        connection.execute(update(CompareJob).where(CompareJob.id == job_id).values(
            status="DONE", termination=termination, max_untested_upper=max_untested_upper, finished_at=moment,
        ))
        return True


def release_trial_quota(
    engine: Engine, job_id: int, error_code: str, *, now: datetime | None = None,
) -> bool:
    """针对解析失败、超时或取消终结任务并回滚预占。仅本次完成状态转换时返回 True。"""
    with engine.begin() as connection:
        job = _locked_job(connection, job_id)
        if job is None or job.status not in _OPEN:
            return False
        moment = _now(connection, now)
        if not _transition(connection, job, "RELEASED", moment):
            return False
        _deduct_in_flight(connection, job.user_id)
        _fail(connection, job_id, error_code, moment)
        return True


def reap_expired_trial_quota(
    engine: Engine, now: datetime | None = None, *, batch_size: int = 100,
) -> int:
    """扫描至多 batch_size 笔已过期预占，按任务逐笔回收，返回本轮成功回收数。"""
    if batch_size <= 0:
        raise ValueError("回收批量须为正数")
    with engine.connect() as connection:
        moment = _now(connection, now)
        jobs = connection.execute(select(TrialReservation.job_id).where(
            TrialReservation.status == "RESERVED", TrialReservation.expires_at <= moment,
        ).order_by(TrialReservation.expires_at, TrialReservation.id).limit(batch_size)).scalars().all()
    count = 0
    for job_id in jobs:
        with engine.begin() as connection:
            job = _locked_job(connection, job_id)
            if job is not None and job.status in _OPEN and _transition(connection, job, "EXPIRED", moment, unexpired=False):
                _deduct_in_flight(connection, job.user_id)
                _fail(connection, job_id, "QUOTA_EXPIRED", moment)
                count += 1
    return count


def _now(connection: Connection, override: datetime | None) -> datetime:
    if override is not None:
        return override
    if connection.dialect.name == "postgresql":
        return connection.execute(select(func.clock_timestamp())).scalar_one()
    return datetime.now(timezone.utc)


def _locked_job(connection, job_id):
    # 领取、完成、释放和回收都先锁任务，再变更预占及计数。重复完成不能覆盖已发布的 DONE。
    return connection.execute(select(CompareJob.id, CompareJob.user_id, CompareJob.status).where(
        CompareJob.id == job_id,
    ).with_for_update()).one_or_none()


def _transition(connection, job, status, moment, *, unexpired=None):
    statement = update(TrialReservation).where(
        TrialReservation.job_id == job.id, TrialReservation.user_id == job.user_id,
        TrialReservation.status == "RESERVED",
    )
    if unexpired is not None:
        deadline = TrialReservation.expires_at
        statement = statement.where(deadline > moment if unexpired else deadline <= moment)
    return connection.execute(statement.values(status=status, updated_at=moment)).rowcount == 1


def _deduct_in_flight(connection, user_id, *, commit=False):
    connection.execute(update(User).where(User.id == user_id).values(
        used_count=User.used_count + int(commit),
        in_flight_count=case((User.in_flight_count > 0, User.in_flight_count - 1), else_=0),
    ))


def _check_result_visibility(connection, user_id, model_ids):
    trial = VisibilityUser(user_id, "TRIAL_USER", None)
    # 锁住发布涉及的模型，避免公开标记或审核状态在复核与提交之间被另一个事务更改。
    statement = visible_candidate_query(trial, model_ids=model_ids).with_for_update(read=True)
    if set(connection.execute(statement).scalars()) != model_ids:
        raise QuotaError("RESULT_OUTSIDE_VISIBILITY_SCOPE")


def _fail(connection, job_id, error_code, moment):
    connection.execute(update(CompareJob).where(CompareJob.id == job_id).values(
        status="FAILED", error_code=error_code, finished_at=moment,
    ))
