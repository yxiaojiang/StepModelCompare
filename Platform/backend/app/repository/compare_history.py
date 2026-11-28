"""按任务所属账号和模型范围查询结果及记录选中次数，依赖 SQLAlchemy 短事务。"""

from sqlalchemy import exists, func, select, update

from app.db.models import CompareJob, CompareResult, Model, User
from app.repository.visibility import apply_visibility_scope


def read_comparison(engine, actor, job_id: int, source: str) -> dict | None:
    """读取 actor 所属来源的任务快照及当前可见结果；越权或不存在统一返回 None。"""
    with engine.connect() as connection:
        job = _owned_job(connection, actor, job_id, source)
        if job is None:
            return None
        body = {"job_id": job.id, "status": job.status}
        if job.status == "FAILED":
            return dict(body, error_code=job.error_code)
        if job.status != "DONE":
            return body
        rows = connection.execute(_results(actor, job).order_by(
            CompareResult.needs_review, CompareResult.rank, CompareResult.model_id,
        )).all()
        remaining = None
        if actor.role == "TRIAL_USER":
            used, in_flight = connection.execute(select(User.used_count, User.in_flight_count)
                                                  .where(User.id == actor.user_id)).one()
            remaining = max(5 - used - in_flight, 0)
        candidates = [_serialize(row) for row in rows if not row.needs_review]
        return dict(
            body, rule_version=job.rule_version_snapshot.get("version"),
            feature_version=job.rule_version_snapshot.get("feature_version"), termination=job.termination,
            max_untested_upper=job.max_untested_upper, trial_remaining=remaining,
            candidates=candidates, best_match=candidates[0] if candidates else None,
            needs_review_items=[_serialize(row) for row in rows if row.needs_review],
        )


def select_comparison_model(engine, actor, job_id: int, source: str, model_id: int) -> bool | None:
    """为当前可见且属于本次结果的模型累计一次选中数，返回是否首次；越权或缺失返回 None。"""
    with engine.begin() as connection:
        job = _owned_job(connection, actor, job_id, source, lock=True)
        if job is None or job.status != "DONE":
            return None
        row = connection.execute(_results(actor, job).where(Model.id == model_id)
                                 .with_for_update(of=Model)).one_or_none()
        if row is None:
            return None
        changed = connection.execute(update(CompareResult).where(
            CompareResult.job_id == job_id, CompareResult.model_id == model_id,
            CompareResult.is_selected.is_(False),
        ).values(is_selected=True)).rowcount
        if changed == 1:
            connection.execute(update(Model).where(Model.id == model_id).values(selected_count=Model.selected_count + 1))
        return changed == 1


def _owned_job(connection, actor, job_id, source, *, lock=False):
    statement = select(CompareJob).where(
        CompareJob.id == job_id, CompareJob.user_id == actor.user_id, CompareJob.source == source,
        CompareJob.query_storage_path.is_not(None),
        # 混合等待期间账号可能已改权；每次回读都以数据库复核发起请求的身份。
        exists(select(User.id).where(
            User.id == actor.user_id, User.role == actor.role, User.enterprise_id == actor.enterprise_id,
            User.auth_version == actor.auth_version, User.is_active.is_(True),
            (User.locked_until.is_(None) | (User.locked_until <= func.now())),
        )),
    )
    row = connection.execute(statement.with_for_update() if lock else statement).one_or_none()
    if row is None:
        return None
    scope = row.rule_version_snapshot
    if (scope.get("role"), scope.get("actor_enterprise_id")) != (actor.role, actor.enterprise_id):
        return None
    return row


def _results(actor, job):
    # 结果和预览走同一租户边界。已经撤出可见范围的模型不进入 HTTP 结果及选中写操作。
    return apply_visibility_scope(select(
        CompareResult.model_id, CompareResult.rank, CompareResult.similarity_score, CompareResult.needs_review,
        CompareResult.review_reason, CompareResult.breakdown_json, CompareResult.align_matrix_json,
        Model.model_name, Model.md5,
    ).join(Model, Model.id == CompareResult.model_id).where(
        CompareResult.job_id == job.id, Model.status == "APPROVED",
    ), actor, job.enterprise_id)


def _serialize(row):
    payload = row.breakdown_json
    dimensions = payload.get("dimensions", {})
    return {
        "model_id": row.model_id, "model_name": row.model_name, "md5": row.md5, "rank": row.rank,
        "similarity_score": row.similarity_score, "needs_review": row.needs_review, "review_reason": row.review_reason,
        "missing_warnings": payload.get("warnings", []),
        "breakdown": [dict(value, dim=key) for key, value in sorted(dimensions.items())],
        "align_matrix": None if row.needs_review else row.align_matrix_json,
    }
