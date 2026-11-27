"""在调用进程编排索引与重提取。依赖 PostgreSQL、受控存储卷及第一层 OCCT 池，单次处理一个模型。"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

from app.db.model_features import (
    FeatureVersionConflict, active_feature_version, assets_exist, feature_write_engine,
    missing_feature_models, switch_active_feature_version, vault_path,
)
from app.db.models import Model, ModelFeature
from app.geometry_engine.feature_versions import require_feature_version
from app.geometry_engine.step_input import StepError

if TYPE_CHECKING:
    from app.geometry_engine.worker_runner import OcctPool


@dataclass(frozen=True)
class FeatureBuildResult:
    """返回指定版本索引结果及固定诊断码。提交切换竞争或模型变更时由调用方重新调度。"""

    feature_version: str
    status: str
    error_code: str | None = None


def build_model_features(
    engine: Engine, vault: Path, pool: "OcctPool", model_id: int, *,
    feature_version: str | None = None, registered_material: str | None = None,
) -> FeatureBuildResult:
    """按 model_id 从 vault 读取可信台账原件并通过 pool 提取特征与两种缓存。

    feature_version 省略时使用数据库指针，并尝试完成 INDEXING 模型的入库；指定版本用于重提取。
    registered_material 为调用方从台账取出的牌号，可省略。返回 READY、FAILED、RETRY 或 SKIPPED；
    不支持的版本抛 ValueError，数据库、磁盘或解析设施故障向上传播。
    """
    writer = feature_write_engine(engine)
    with writer.connect() as connection:
        active = active_feature_version(connection)
        version = active if feature_version is None else feature_version
        require_feature_version(version)
        model = _model(connection, model_id)
        if registered_material is None and model is not None and model.status == "APPROVED":
            registered_material = _registered_material(connection, model_id, active)
    if model is None or model.status not in ("INDEXING", "APPROVED"):
        return FeatureBuildResult(version, "SKIPPED")
    if feature_version is not None and model.status != "APPROVED":
        return FeatureBuildResult(version, "SKIPPED")

    # 现成的同版本完整产物可幂等复用。实际写入与入库流转时以模型行锁和当前指针重新确认。
    reused = _finish(writer, vault, model, version, feature_version is None)
    if reused is not None:
        return reused

    scope = f"enterprise-{model.enterprise_id}" if model.enterprise_id is not None else (
        "public-demo" if model.is_public_demo else "unassigned"
    )
    source = vault_path(vault, model.storage_path)
    parent = vault_path(vault, f"cache/{scope}/{model.id}/{version}")
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".staging-", dir=parent) as temporary:
        staged = Path(temporary)
        mesh, glb = staged / "surface.npz", staged / "preview.glb"
        try:
            payload = pool.extract_features(
                source, feature_version=version, mesh_target=mesh, glb_target=glb,
                original_filename=model.original_filename, registered_material=registered_material,
            )
        except StepError as error:
            if error.code in ("ERR_PARSE_POOL_BUSY", "ERR_PARSE_WORKER_INIT", "ERR_FEATURE_PROCESSOR_UNAVAILABLE"):
                raise
            return _finish(writer, vault, model, version, feature_version is None, error_code=error.code)
        if payload.get("feature_version") != version:
            raise RuntimeError("特征版本与提取请求不一致")
        for artifact in (mesh, glb):
            if not artifact.stat().st_size:
                raise OSError("解析进程发布了空产物")
            with artifact.open("rb+") as handle:
                os.fsync(handle.fileno())
        return _finish(writer, vault, model, version, feature_version is None, staged=staged, payload=payload)


def rebuild_feature_version(
    engine: Engine, vault: Path, pool: "OcctPool", *, expected: str, target: str, batch_size: int = 100,
) -> None:
    """使用 pool 为 vault 中所有已批准模型补齐 target，增量复扫后将全局指针从 expected 切换。

    batch_size 控制每批读取大小。遇到提取失败或并发产生的新缺口抛 FeatureVersionConflict，
    已完成的版本行可以重试时复用。非法版本或参数抛 ValueError，数据库和文件错误向上传播。
    """
    require_feature_version(target)
    if batch_size <= 0:
        raise ValueError("重提取批大小须为正数")
    engine = feature_write_engine(engine)
    with engine.connect() as connection:
        if active_feature_version(connection) != expected:
            raise FeatureVersionConflict("当前特征版本已变化")
    # 每次固定 ID 上界，避免持续入库拖长扫描；第二次查漏从头扫描，处理窗口内批准的模型。
    for _ in range(2):
        with engine.connect() as connection:
            upper = connection.execute(select(func.max(Model.id)).where(Model.status == "APPROVED")).scalar_one()
        cursor = 0
        while upper is not None and cursor < upper:
            with engine.connect() as connection:
                batch = missing_feature_models(
                    connection, vault, target, after_id=cursor, through_id=upper, limit=batch_size,
                )
            if not batch:
                break
            for model_id in batch:
                result = build_model_features(engine, vault, pool, model_id, feature_version=target)
                if result.status == "FAILED":
                    raise FeatureVersionConflict(f"模型 {model_id} 重提取失败：{result.error_code}")
            cursor = batch[-1]
    switch_active_feature_version(engine, vault, expected, target)


def _model(connection, model_id, *, lock=False):
    statement = select(*Model.__table__.c).where(Model.id == model_id)
    return connection.execute(statement.with_for_update() if lock else statement).one_or_none()


def _registered_material(connection, model_id, version):
    basic = connection.execute(select(ModelFeature.feature_payload["d1_basic"]).where(
        ModelFeature.model_id == model_id, ModelFeature.feature_version == version, ModelFeature.status == "READY",
    )).scalar_one_or_none()
    if isinstance(basic, dict) and basic.get("material_source") == "REGISTERED":
        materials = basic.get("materials")
        if isinstance(materials, list) and materials and isinstance(materials[0], str):
            return materials[0]
    return None


def _source_identity(model):
    return model.enterprise_id, model.is_public_demo, model.md5, model.storage_path, model.original_filename


def _finish(engine, vault, previous, version, admit, *, staged=None, payload=None, error_code=None):
    published = None
    commit_started = False
    try:
        with engine.connect() as connection, connection.begin():
            # 所有入库终态先锁全局指针，再锁模型；后台重提取只改该模型的版本行。
            active = active_feature_version(connection, shared_lock=True) if admit else None
            current = _model(connection, previous.id, lock=True)
            if (current is None or current.status not in ("APPROVED", "INDEXING")
                    or _source_identity(current) != _source_identity(previous)):
                return FeatureBuildResult(version, "SKIPPED")
            if current.status == "INDEXING" and current.indexing_started_at != previous.indexing_started_at:
                return FeatureBuildResult(version, "SKIPPED")
            if admit and active != version:
                if current.status == "INDEXING":
                    connection.execute(update(Model).where(Model.id == current.id).values(indexing_started_at=None))
                return FeatureBuildResult(version, "RETRY", "FEATURE_VERSION_CHANGED")
            existing = connection.execute(
                select(*ModelFeature.__table__.c).where(
                    ModelFeature.model_id == current.id, ModelFeature.feature_version == version,
                )
            ).one_or_none()
            if (
                existing is not None and existing.status == "READY" and isinstance(existing.feature_payload, dict)
                and existing.feature_payload.get("feature_version") == version
                and assets_exist(vault, existing.sample_points_path, existing.glb_path)
            ):
                if admit:
                    _complete_admission(connection, current, None)
                return FeatureBuildResult(version, "READY")
            if staged is None and error_code is None:
                return None
            if not admit and current.status != "APPROVED":
                return FeatureBuildResult(version, "SKIPPED")
            if error_code is not None and existing is not None and existing.status == "READY":
                # 活跃版本与运行中任务可能仍引用旧缓存；修复失败保留已有记录，等待再次重提取。
                return FeatureBuildResult(version, "FAILED", error_code)

            values = {"status": "FAILED", "feature_payload": {"feature_version": version, "error_code": error_code}}
            if staged is not None:
                location = staged.parent / uuid4().hex
                mesh = (location / "surface.npz").relative_to(vault.resolve()).as_posix()
                glb = (location / "preview.glb").relative_to(vault.resolve()).as_posix()
                payload["cache_refs"] = {**payload["cache_refs"], "sample_points_path": mesh, "glb_path": glb}
                # 整个目录一次重命名；数据库只引用已完成的、不可变的一对产物。
                staged.rename(location)
                published = location
                values = {"status": "READY", "feature_payload": payload, "sample_points_path": mesh, "glb_path": glb}
            if existing is None:
                connection.execute(ModelFeature.__table__.insert().values(
                    model_id=current.id, feature_version=version, **values,
                ))
            else:
                connection.execute(update(ModelFeature).where(ModelFeature.id == existing.id).values(
                    **values, extracted_at=func.now(),
                ))
            if admit:
                _complete_admission(connection, current, error_code)
            commit_started = True
    except BaseException:
        # 若提交回执丢失，宁可保留孤立目录，也不能删除可能已经被数据库引用的缓存。
        if published is not None and not commit_started:
            shutil.rmtree(published)
        raise
    return FeatureBuildResult(version, values["status"], error_code)


def _complete_admission(connection, current, error_code):
    if current.status != "INDEXING":
        return
    values = (
        {"status": "APPROVED", "approved_at": func.now(), "reject_or_error_reason": None}
        if error_code is None else {"status": "PARSE_FAILED", "reject_or_error_reason": error_code}
    )
    connection.execute(update(Model).where(Model.id == current.id).values(**values))
