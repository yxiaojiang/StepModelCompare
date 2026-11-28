"""实现企业批量上传、同企去重和人工审核流转。依赖 PostgreSQL 行锁与独立原件存储卷。"""

import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy import exists, func, select, update

from app.db.model_features import vault_path
from app.db.models import AuditLog, Enterprise, Model, User
from app.geometry_engine.step_input import StepError, validate_step_file
from app.uploads import UploadError, save_step

_LIVE = ("PENDING", "INDEXING", "APPROVED")
_LOG = logging.getLogger("step_platform")


def upload_scope(actor, requested: int | None, *, direct: bool) -> int:
    """确定可信上传租户；直传限系统管理员，试用或无企业角色抛带状态的 UploadError。"""
    if actor.role == "TRIAL_USER":
        raise UploadError("TRIAL_UPLOAD_FORBIDDEN", 403)
    if actor.role == "SYS_ADMIN":
        if requested is None:
            raise UploadError("ENTERPRISE_REQUIRED")
        return requested
    if direct or actor.role not in ("ENT_USER", "ENT_ADMIN") or actor.enterprise_id is None:
        raise UploadError("FORBIDDEN", 403)
    return actor.enterprise_id


def accept_model_batch(engine, vault: Path, actor, enterprise_id: int, files, *, direct: bool, file_limit: int) -> dict:
    """逐件封存上传文件并写待审或直传台账，返回 accepted/skipped_duplicates/rejected 分组。

    files 是已限定件数的表单上传文件。合法件独立提交；文件格式或存储配额不足只拒绝该件。
    账号/企业状态变化抛 UploadError，存储或数据库故障向上传播。
    """
    result = {"batch_id": uuid4().hex, "accepted": [], "skipped_duplicates": [], "rejected": []}
    for file in files:
        try:
            step = save_step(vault, f"models/enterprise-{enterprise_id}", file.filename or "", file.file, file_limit)
        except UploadError as error:
            result["rejected"].append({"filename": Path((file.filename or "").replace("\\", "/")).name[:255],
                                       "reason": str(error)})
            continue
        try:
            validate_step_file(step.path, file_limit)
            kind, data = _admit(engine, actor, enterprise_id, step, direct)
        except StepError:
            kind, data = "rejected", {"filename": step.filename, "reason": "INVALID_EXTENSION_OR_HEADER"}
        except UploadError as error:
            if error.status == 403:
                step.path.unlink(missing_ok=True)
                raise
            kind, data = "rejected", {"filename": step.filename, "reason": str(error)}
        if kind != "accepted":
            step.path.unlink(missing_ok=True)
        result[kind].append(data)
    return result


def review_model(engine, vault: Path | None, actor, model_id: int, *, approve: bool,
                 reason: str | None = None, public_demo: bool = False) -> dict | None:
    """按 model_id 原子审核待审件并写审计。返回新状态，不存在或已处理返回 None，数据库异常向上传播。"""
    if actor.role != "SYS_ADMIN":
        raise UploadError("FORBIDDEN", 403)
    status = "INDEXING" if approve else "REJECTED"
    with engine.begin() as connection:
        row = connection.execute(update(Model).where(
            Model.id == model_id, Model.status == "PENDING", exists(select(User.id).where(
                User.id == actor.user_id, User.role == "SYS_ADMIN", User.auth_version == actor.auth_version,
                User.is_active.is_(True), (User.locked_until.is_(None) | (User.locked_until <= func.now())),
            )),
        ).values(
            status=status, reviewer_id=actor.user_id, reviewed_at=func.now(),
            reject_or_error_reason=reason if not approve else None,
            is_public_demo=approve and public_demo,
        ).returning(Model.storage_path, Model.enterprise_id)).one_or_none()
        if row is None:
            return None
        connection.execute(AuditLog.__table__.insert().values(
            actor_id=actor.user_id, action="MODEL_APPROVED_FOR_INDEXING" if approve else "MODEL_REJECTED",
            target_type="model", target_id=model_id, detail_json={},
        ))
    result = {"model_id": model_id, "status": status}
    if not approve and vault is not None:
        try:
            discard_model_source(vault, row.storage_path, row.enterprise_id)
        except (OSError, ValueError):
            # 状态和审计已经提交，清理失败须单独提示，避免客户端误认为审核可重试。
            _LOG.warning("驳回模型 %s 后未能清理原件", model_id)
            result["warning_code"] = "MODEL_SOURCE_CLEANUP_FAILED"
    return result


def discard_model_source(vault: Path, key: str, enterprise_id: int) -> None:
    """清理本流程生成的隔离原件；历史路径由其存储维护流程处理，IO 错误向上传播。"""
    if key.startswith(f"models/enterprise-{enterprise_id}/"):
        vault_path(vault, key).unlink(missing_ok=True)


def _admit(engine, actor, enterprise_id, step, direct):
    with engine.begin() as connection:
        # 锁企业行覆盖去重、容量统计与写入，避免两次并发上传都看不到对方的未提交记录。
        enterprise = connection.execute(select(Enterprise.storage_quota_mb).where(
            Enterprise.id == enterprise_id, Enterprise.status == "ACTIVE",
        ).with_for_update()).one_or_none()
        if enterprise is None:
            raise UploadError("ENTERPRISE_NOT_AVAILABLE", 403)
        current = connection.execute(select(User.role, User.enterprise_id).where(
            User.id == actor.user_id, User.auth_version == actor.auth_version, User.is_active.is_(True),
            (User.locked_until.is_(None) | (User.locked_until <= func.now())),
        )).one_or_none()
        if current is None or (current.role, current.enterprise_id) != (actor.role, actor.enterprise_id):
            raise UploadError("FORBIDDEN", 403)
        live = Model.enterprise_id == enterprise_id, Model.status.in_(_LIVE)
        existing = connection.execute(select(Model.id, Model.status, Model.model_name).where(
            *live, Model.md5 == step.digest,
        ).order_by(Model.id).limit(1)).one_or_none()
        if existing is not None:
            return "skipped_duplicates", {"model_name": existing.model_name, "md5": step.digest,
                                         "existing_model_id": existing.id, "existing_status": existing.status}
        previous = connection.execute(select(Model.status, Model.reject_or_error_reason).where(
            Model.enterprise_id == enterprise_id, Model.md5 == step.digest,
            Model.status.in_(("REJECTED", "PARSE_FAILED")),
        ).order_by(Model.id.desc()).limit(1)).one_or_none()
        used = connection.execute(select(func.coalesce(func.sum(Model.file_size_bytes), 0)).where(*live)).scalar_one()
        if used + step.size > enterprise.storage_quota_mb * 1_000_000:
            raise UploadError("ENTERPRISE_STORAGE_QUOTA")
        status = "INDEXING" if direct else "PENDING"
        model_id = connection.execute(Model.__table__.insert().values(
            enterprise_id=enterprise_id, model_name=Path(step.filename).stem, original_filename=step.filename,
            md5=step.digest, file_size_bytes=step.size, storage_path=step.key, uploader_id=actor.user_id, status=status,
            reviewer_id=actor.user_id if direct else None, reviewed_at=func.now() if direct else None,
        ).returning(Model.id)).scalar_one()
        if direct:
            connection.execute(AuditLog.__table__.insert().values(
                actor_id=actor.user_id, action="MODEL_DIRECT_UPLOADED",
                target_type="model", target_id=model_id, detail_json={},
            ))
        data = {"model_id": model_id, "model_name": Path(step.filename).stem, "md5": step.digest, "status": status}
        if previous is not None:
            data.update(previous_status=previous.status, previous_reason=previous.reject_or_error_reason)
        return "accepted", data
