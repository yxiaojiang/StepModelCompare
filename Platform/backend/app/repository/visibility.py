"""在 Repository 层集中注入模型可见性条件。

该模块是跨企业零越权的最后一道数据层守卫。调用方传入的 `target_enterprise_id` 不能削弱普通用户条件，
试用用户只能看公开且已审核模型；系统管理员才可以显式指定企业或查看全库。
"""

from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.sql.selectable import Join

from app.db.models import Model, ModelFeature


class VisibilityError(ValueError):
    """表示用户角色或企业归属不满足数据访问规则。"""


@dataclass(frozen=True, slots=True)
class VisibilityUser:
    """Repository 层需要的最小用户身份摘要，不携带密码等敏感字段。"""

    user_id: int
    role: str
    enterprise_id: int | None
    is_trial: bool = False


def apply_visibility_scope(
    query: Select,
    current_user: VisibilityUser,
    target_enterprise_id: int | None = None,
) -> Select:
    """为模型查询强制注入角色范围，返回不可扩大权限的新 Select。

    企业用户仅能看本企业 APPROVED；试用用户只能看公开 APPROVED；
    系统管理员可看全库或指定企业。查询未直接使用模型基表、角色未知或企业用户缺归属时抛 VisibilityError。
    """
    # 对别名误加基表谓词会隐式生成笛卡尔积。拒绝此查询形状，避免谓词约束了另一张表实例。
    sources = list(query.get_final_froms())
    uses_model = False
    while sources:
        source = sources.pop()
        if isinstance(source, Join):
            sources.extend((source.left, source.right))
        elif source.compare(Model.__table__):
            uses_model = True
        elif source.is_derived_from(Model.__table__):
            raise VisibilityError("模型可见性查询不能使用模型表别名或子查询")
    if not uses_model:
        raise VisibilityError("模型可见性查询须直接使用模型基表")

    if current_user.role == "SYS_ADMIN":
        if target_enterprise_id is not None:
            return query.where(Model.enterprise_id == target_enterprise_id)
        return query
    if current_user.role == "TRIAL_USER":
        return query.where(Model.is_public_demo.is_(True), Model.status == "APPROVED")
    if current_user.role not in {"ENT_USER", "ENT_ADMIN"} or current_user.enterprise_id is None:
        raise VisibilityError("账号没有可用企业范围")
    # 企业管理员审核权限待签认，暂与企业普通用户采用相同的可见范围。
    return query.where(Model.enterprise_id == current_user.enterprise_id, Model.status == "APPROVED")


def visible_model_query(
    current_user: VisibilityUser,
    model_id: int | None = None,
    target_enterprise_id: int | None = None,
) -> Select:
    """构造列表或详情查询；详情仍先套用同一隔离守卫，找不到越权对象时返回空结果。"""
    query = select(Model)
    if model_id is not None:
        query = query.where(Model.id == model_id)
    return apply_visibility_scope(query, current_user, target_enterprise_id)


def visible_candidate_query(current_user, target_enterprise_id=None, *, model_ids=None) -> Select:
    """构造已批准候选 ID 查询，可按模型 ID 集收敛；未知角色抛 VisibilityError。"""
    query = select(Model.id).where(Model.status == "APPROVED").order_by(Model.id)
    if model_ids is not None:
        query = query.where(Model.id.in_(model_ids))
    return apply_visibility_scope(query, current_user, target_enterprise_id)


def visible_glb_query(current_user, model_id: int, feature_version: str) -> Select:
    """构造指定模型及版本的就绪预览引用查询；不可见行为空，未知角色抛 VisibilityError。"""
    query = select(ModelFeature.glb_path).join(Model, Model.id == ModelFeature.model_id).where(
        Model.id == model_id, ModelFeature.feature_version == feature_version, ModelFeature.status == "READY",
    )
    return apply_visibility_scope(query, current_user)
