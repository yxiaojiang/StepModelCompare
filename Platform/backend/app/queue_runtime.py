"""随服务生命周期运行入库、对比和回收线程。几何计算由 OCCT 池和任务级进程完成。"""

from contextlib import contextmanager
from datetime import timedelta
import logging
from pathlib import Path
from threading import Event, Thread

from sqlalchemy import func, select, update

from app.compare.trial_job import run_claimed_compare_job
from app.db.models import CompareJob, Model
from app.db.queue import claim_compare_job, claim_indexing_model
from app.geometry_engine.feature_indexing import build_model_features
from app.geometry_engine.worker_runner import OcctPool
from app.repository.model_intake import discard_model_source
from app.repository.quota import finish_failed_compare, reap_expired_trial_quota

_LOG = logging.getLogger("step_platform")


@contextmanager
def queue_workers(engine, settings):
    """按配置启动三条队列循环并管理共享解析池，退出时等在途受限计算结束；启动失败向上传播。"""
    if not settings.run_queue_workers or not settings.model_vault or engine.dialect.name != "postgresql":
        yield
        return
    vault = Path(settings.model_vault).resolve()
    stop = Event()
    with OcctPool.from_settings(settings) as pool:
        threads = []
        try:
            for name, work, interval in (
                ("compare", lambda: _compare_one(engine, settings, pool, vault), settings.queue_poll_seconds),
                ("index", lambda: _index_one(engine, pool, vault), settings.queue_poll_seconds),
                ("reaper", lambda: reap_queue(engine, settings, vault), settings.reaper_interval_seconds),
            ):
                worker = Thread(name=f"step-{name}", target=_loop, args=(stop, work, interval))
                worker.start()
                threads.append(worker)
            yield
        finally:
            stop.set()
            for thread in threads:
                thread.join()


def reap_queue(engine, settings, vault: Path) -> bool:
    """按数据库时钟处理预占过期、排队/计算超时和异常退出的索引件，返回 False 以等待下一轮。"""
    reap_expired_trial_quota(engine)
    with engine.connect() as connection:
        now = connection.execute(select(func.clock_timestamp())).scalar_one()
        queued = now - timedelta(seconds=settings.queue_wait_timeout)
        running = now - timedelta(seconds=settings.compare_job_timeout + 5)
        jobs = connection.execute(select(CompareJob.id, CompareJob.status).where(
            ((CompareJob.status == "QUEUED") & (CompareJob.created_at <= queued))
            | ((CompareJob.status == "RUNNING") & (CompareJob.started_at <= running)),
        ).order_by(CompareJob.id).limit(100)).all()
    for job in jobs:
        if job.status == "QUEUED":
            finish_failed_compare(engine, job.id, "QUEUE_WAIT_TIMEOUT", now=now, queued_before=queued)
        else:
            finish_failed_compare(engine, job.id, "COMPARE_TIMEOUT", now=now, running_before=running)
    indexed_before = now - timedelta(seconds=2 * settings.occt_parse_timeout + 30)
    with engine.begin() as connection:
        # 跳过正在发布的模型事务；索引工序以领取时间及状态检查拒绝迟到发布。
        stale = select(Model.id).where(
            Model.status == "INDEXING", Model.indexing_started_at <= indexed_before,
        ).order_by(Model.id).limit(100).with_for_update(skip_locked=True)
        rows = connection.execute(update(Model).where(Model.id.in_(stale)).values(
            status="PARSE_FAILED", reject_or_error_reason="INDEX_WORKER_TIMEOUT",
        ).returning(Model.storage_path, Model.enterprise_id)).all()
    for row in rows:
        discard_model_source(vault, row.storage_path, row.enterprise_id)
    return False


def _compare_one(engine, settings, pool, vault):
    job = claim_compare_job(engine, settings.queue_wait_timeout, stored_only=True)
    if job is None:
        return False
    run_claimed_compare_job(settings, pool, engine, vault, job)
    return True


def _index_one(engine, pool, vault):
    model_id = claim_indexing_model(engine)
    if model_id is None:
        return False
    result = build_model_features(engine, vault, pool, model_id)
    if result.status == "FAILED":
        with engine.connect() as connection:
            row = connection.execute(select(Model.storage_path, Model.enterprise_id).where(
                Model.id == model_id, Model.status == "PARSE_FAILED",
            )).one_or_none()
        if row is not None:
            discard_model_source(vault, row.storage_path, row.enterprise_id)
    return True


def _loop(stop, work, interval):
    while not stop.is_set():
        try:
            if work():
                continue
        except Exception:
            # 不把 SQL 参数和存储路径写到运行日志；回收循环可终结已领取的中断任务。
            _LOG.warning("队列步骤失败，将在下个周期继续")
        stop.wait(interval)
