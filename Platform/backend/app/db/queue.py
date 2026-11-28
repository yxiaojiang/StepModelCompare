"""抢占 PostgreSQL 队列记录。依赖 SQLAlchemy，引擎由消费进程持有，每次领用只开一个短事务。"""

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

from app.db.models import CompareJob, Model


def claim_compare_job(engine: Engine, queue_wait_timeout: int, *, stored_only: bool = False) -> int | None:
    """从 engine 领用仍在 queue_wait_timeout 秒内的最早任务并返回 ID，空队列返回 None。

    stored_only 限定有可恢复原件引用的任务。非 PostgreSQL 抛 RuntimeError，无效等待时间抛 ValueError，
    数据库故障传播 SQLAlchemyError。
    """
    if queue_wait_timeout <= 0:
        raise ValueError("队列等待上限须为正数")
    cutoff = func.statement_timestamp() - timedelta(seconds=queue_wait_timeout)
    # 过期任务留给配额回收流程，避免抢占骨架单独终结状态、遗漏预占回滚。
    candidate = (
        select(CompareJob.id)
        .where(CompareJob.status == "QUEUED", CompareJob.created_at > cutoff)
        .order_by(CompareJob.created_at, CompareJob.id)
    )
    if stored_only:
        candidate = candidate.where(CompareJob.query_storage_path.is_not(None))
    return _claim(engine, candidate, CompareJob, status="RUNNING", started_at=func.now())


def claim_indexing_model(engine: Engine) -> int | None:
    """从 engine 领用尚未抢占的入库模型并返回 ID，空队列返回 None。

    非 PostgreSQL 抛 RuntimeError，数据库故障传播 SQLAlchemyError。
    """
    candidate = (
        select(Model.id)
        .where(Model.status == "INDEXING", Model.indexing_started_at.is_(None))
        .order_by(Model.id)
    )
    return _claim(engine, candidate, Model, indexing_started_at=func.now())


def _claim(engine, candidate, entity, **values):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("任务抢占仅支持 PostgreSQL")
    # 在同一语句锁定与标记记录。跳过他人行锁，提交后再做计算，避免持锁到解析结束。
    next_job = candidate.limit(1).with_for_update(skip_locked=True).scalar_subquery()
    statement = update(entity).where(entity.id == next_job).values(**values).returning(entity.id)
    with engine.begin() as connection:
        job_id = connection.execute(statement).scalar_one_or_none()
    return job_id
