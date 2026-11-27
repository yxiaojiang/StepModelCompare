"""管理特征版本读取、查漏与切换。依赖 SQLAlchemy 与本机缓存卷，写入和切换使用 PostgreSQL 行锁。"""

from collections.abc import Iterable
from itertools import islice
from pathlib import Path

from sqlalchemy import and_, exists, select, update
from sqlalchemy.engine import Connection, Engine

from app.db.models import FeatureCatalog, Model, ModelFeature
from app.geometry_engine.feature_versions import require_feature_version


class FeatureVersionConflict(RuntimeError):
    """报告版本指针已变化或目标仍有缺口。调用方据此重新查漏，错误不含原件路径。"""


def active_feature_version(connection: Connection, *, shared_lock: bool = False) -> str:
    """从 connection 读取全局版本；shared_lock 为真时持共享行锁到事务结束。

    返回数据库中的指针，可作为整场任务的快照。指针缺失或数据库失败抛 SQLAlchemyError。
    """
    statement = select(FeatureCatalog.active_feature_version).where(FeatureCatalog.id == 1)
    if shared_lock:
        statement = statement.with_for_update(read=True)
    return connection.execute(statement).scalar_one()


def load_ready_features(connection: Connection, model_ids: Iterable[int], feature_version: str) -> list:
    """按已授权 model_ids 读取指定 feature_version 中已批准且就绪的特征行，按模型 ID 排序。

    调用方负责候选可见性及整场任务固定版本；无该版本的模型不回退。数据库错误向上传播。
    """
    return connection.execute(
        select(*ModelFeature.__table__.c)
        .join(Model, Model.id == ModelFeature.model_id)
        .where(Model.id.in_(tuple(model_ids)), Model.status == "APPROVED", *_ready(feature_version))
        .order_by(Model.id)
    ).all()


def missing_feature_models(
    connection: Connection, vault: Path, feature_version: str, *,
    after_id: int = 0, through_id: int | None = None, limit: int = 100,
) -> list[int]:
    """分页查找 vault 中缺少有效特征或缓存的 APPROVED 模型 ID，供全量重提取与末次查漏。

    after_id 是已处理的尾部 ID，through_id 可锁定本次扫描的 ID 上界，limit 为本页最大条数。
    非法分页抛 ValueError，数据库或文件系统错误向上传播。
    """
    if after_id < 0 or limit <= 0 or (through_id is not None and through_id < 0):
        raise ValueError("特征查漏分页参数无效")
    statement = (
        select(Model.id, ModelFeature.sample_points_path, ModelFeature.glb_path)
        .outerjoin(ModelFeature, (ModelFeature.model_id == Model.id) & and_(*_ready(feature_version)))
        .where(Model.status == "APPROVED", Model.id > after_id)
        .order_by(Model.id)
        .execution_options(yield_per=256)
    )
    if through_id is not None:
        statement = statement.where(Model.id <= through_id)
    with connection.execute(statement) as rows:
        missing = (model_id for model_id, mesh, glb in rows if not assets_exist(vault, mesh, glb))
        return list(islice(missing, limit))


def switch_active_feature_version(engine: Engine, vault: Path, expected: str, target: str) -> None:
    """在 engine 上将当前指针从 expected 切至 target，核对所有已批准模型及 vault 产物。

    旧版本保留给运行中的任务。指针变化或目标缺口抛 FeatureVersionConflict，版本未知抛 ValueError，
    非 PostgreSQL 抛 RuntimeError，数据库与文件系统错误向上传播。
    """
    require_feature_version(target)
    with feature_write_engine(engine).begin() as connection:
        # 入库完成持同一指针的共享锁。获得排他锁后再用新快照检查，每个并发入库只能在切换前或后完成。
        locked = select(FeatureCatalog.active_feature_version).where(FeatureCatalog.id == 1).with_for_update()
        if connection.execute(locked).scalar_one() != expected:
            raise FeatureVersionConflict("当前特征版本已变化")
        if connection.execute(_missing(target).limit(1)).first() is not None:
            raise FeatureVersionConflict("目标版本仍有未就绪的模型")
        files = (
            select(ModelFeature.sample_points_path, ModelFeature.glb_path)
            .join(Model, Model.id == ModelFeature.model_id)
            .where(Model.status == "APPROVED", *_ready(target))
        )
        with connection.execute(files.execution_options(yield_per=256)) as rows:
            if any(not assets_exist(vault, mesh, glb) for mesh, glb in rows):
                raise FeatureVersionConflict("目标版本仍有缺失的缓存文件")
        connection.execute(
            update(FeatureCatalog).where(FeatureCatalog.id == 1).values(active_feature_version=target),
        )


def feature_write_engine(engine: Engine) -> Engine:
    """返回显式使用 READ COMMITTED 的 engine 视图，非 PostgreSQL 抛 RuntimeError。"""
    if engine.dialect.name != "postgresql":
        raise RuntimeError("特征写入与切换需要 PostgreSQL 行锁")
    return engine.execution_options(isolation_level="READ COMMITTED")


def vault_path(vault: Path, key: str) -> Path:
    """将受控相对 key 解析到 vault 内，返回绝对路径；越界、绝对路径或空值抛 ValueError。"""
    root = vault.resolve()
    relative = Path(key)
    if not relative.parts or relative.drive or relative.root:
        raise ValueError("缓存路径必须相对于存储根目录")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("缓存路径超出存储根目录")
    return path


def assets_exist(vault: Path, mesh: str | None, glb: str | None) -> bool:
    """检查 vault 中 mesh 与 glb 是否为非空的成对文件，路径无效或文件缺失时返回 False。"""
    if not mesh or not glb:
        return False
    try:
        a, b = vault_path(vault, mesh), vault_path(vault, glb)
        return (
            a.parent == b.parent and a.suffix == ".npz" and b.suffix == ".glb"
            and a.is_file() and b.is_file() and a.stat().st_size > 0 and b.stat().st_size > 0
        )
    except (FileNotFoundError, ValueError):
        return False


def _ready(version: str) -> tuple:
    return (
        ModelFeature.feature_version == version, ModelFeature.status == "READY",
        ModelFeature.feature_payload["feature_version"].as_string() == version,
        ModelFeature.sample_points_path != "", ModelFeature.glb_path != "",
    )


def _missing(version: str):
    return select(Model.id).where(
        Model.status == "APPROVED",
        ~exists(select(ModelFeature.id).where(ModelFeature.model_id == Model.id, *_ready(version))),
    ).order_by(Model.id)
