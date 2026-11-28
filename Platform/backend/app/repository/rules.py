"""管理不可变的规则版本及激活指针，依赖 SQLAlchemy，由后台操作及任务入队事务调用。"""

from uuid import uuid4

from sqlalchemy import func, select, update

from app.compare.rule import ComparisonRule
from app.db.model_features import active_feature_version
from app.db.models import AuditLog, Enterprise, SimilarityRule


class RuleUnavailable(ValueError):
    """表示当前范围没有可用于计算的已激活规则。"""


def active_rule_snapshot(connection, enterprise_id: int | None) -> dict:
    """读取企业覆盖或全局生效规则和统一特征版本，返回完整可重放快照，缺失时抛 RuleUnavailable。"""
    table = SimilarityRule
    scope = table.enterprise_id.is_(None)
    if enterprise_id is not None:
        scope = scope | (table.enterprise_id == enterprise_id)
    row = connection.execute(select(table).where(table.is_active.is_(True), scope)
                             .order_by(table.enterprise_id.is_(None)).limit(1)).one_or_none()
    if row is None:
        raise RuleUnavailable("RULE_NOT_CONFIGURED")
    return {**rule_record(row), "feature_version": active_feature_version(connection)}


def rule_record(row) -> dict:
    """把规则数据库行转换为 API 与任务共用的数据；无效旧版规则抛 RuleUnavailable。"""
    try:
        rule = ComparisonRule.model_validate(dict(
            row.missing_policy_json, weights=row.weights_json, thresholds=row.thresholds_json,
            max_refine_k=row.max_refine_k,
        ))
    except (TypeError, ValueError):
        raise RuleUnavailable("RULE_INVALID") from None
    return {
        "rule_id": row.id, "version": row.version, "name": row.name, "scope": row.scope,
        "enterprise_id": row.enterprise_id, "is_active": row.is_active, "rule": rule.model_dump(mode="json"),
    }


def save_rule(engine, actor_id: int, name: str, enterprise_id: int | None, rule: ComparisonRule) -> dict:
    """保存一份未生效模板版本及审计，返回配置记录。企业不存在抛 RuleUnavailable。"""
    values = rule.model_dump(mode="json")
    weights, thresholds, max_k = (values.pop(key) for key in ("weights", "thresholds", "max_refine_k"))
    with engine.begin() as connection:
        if enterprise_id is not None and connection.execute(select(Enterprise.id).where(
            Enterprise.id == enterprise_id,
        )).scalar_one_or_none() is None:
            raise RuleUnavailable("ENTERPRISE_NOT_FOUND")
        row = connection.execute(SimilarityRule.__table__.insert().values(
            scope="GLOBAL" if enterprise_id is None else "ENTERPRISE", enterprise_id=enterprise_id,
            name=name, version=uuid4().hex, is_active=False, weights_json=weights, thresholds_json=thresholds,
            missing_policy_json=values, max_refine_k=max_k, updated_by=actor_id,
        ).returning(*SimilarityRule.__table__.c)).one()
        _audit(connection, actor_id, row.id, "RULE_CREATED")
        return rule_record(row)


def activate_rule(engine, actor_id: int, rule_id: int) -> dict | None:
    """原子切换同一作用域的生效版本，返回规则；不存在返回 None，旧格式非法抛 RuleUnavailable。"""
    table = SimilarityRule
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # 首次激活可能没有现有生效行可锁，因此用事务锁串行化低频激活动作。
            connection.execute(select(func.pg_advisory_xact_lock(781_340_022)))
        row = connection.execute(select(table).where(table.id == rule_id)).one_or_none()
        if row is None:
            return None
        result = rule_record(row)
        if not row.is_active:
            connection.execute(update(table).where(
                table.enterprise_id == row.enterprise_id, table.is_active.is_(True),
            ).values(is_active=False))
            connection.execute(update(table).where(table.id == rule_id).values(is_active=True))
            _audit(connection, actor_id, rule_id, "RULE_ACTIVATED")
        return dict(result, is_active=True)


def _audit(connection, actor_id, rule_id, action):
    connection.execute(AuditLog.__table__.insert().values(
        actor_id=actor_id, action=action, target_type="similarity_rule", target_id=rule_id, detail_json={},
    ))
