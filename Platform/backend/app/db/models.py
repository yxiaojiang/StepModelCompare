"""定义 v1.1 核心持久化契约。依赖 SQLAlchemy，表结构由 Alembic 迁移管理。"""

from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, false, func, text, true,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite 基础迁移用同一结构，自增主键须使用其 INTEGER 类型。
ID = BigInteger().with_variant(Integer(), "sqlite")
JSON_DOCUMENT = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
TIMESTAMP = DateTime(timezone=True)


class Base(DeclarativeBase):
    """持有 SQLAlchemy ORM 元数据，供应用会话与 Alembic 比对使用。"""


class Enterprise(Base):
    """记录企业、存储配额及状态，作为租户外键的长期归属。"""

    __tablename__ = "enterprises"

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(100), unique=True)
    storage_quota_mb: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class User(Base):
    """记录账号、租户及试用计数，版本递增及锁定随鉴权事务管理。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('SYS_ADMIN', 'ENT_ADMIN', 'ENT_USER', 'TRIAL_USER')", name="ck_users_role",
        ),
        CheckConstraint(
            "auth_version >= 1 AND used_count >= 0 AND in_flight_count >= 0 AND failed_login_count >= 0",
            name="ck_users_counters",
        ),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    auth_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    role: Mapped[str] = mapped_column(String(16))
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprises.id"))
    is_trial: Mapped[bool] = mapped_column(server_default=false())
    used_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    in_flight_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class InviteCode(Base):
    """保存企业邀请码、期限及用量，核销随账号绑定事务管理。"""

    __tablename__ = "invite_codes"
    __table_args__ = (
        CheckConstraint(
            "max_uses > 0 AND used_count >= 0 AND used_count <= max_uses",
            name="ck_invite_codes_usage",
        ),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(ForeignKey("enterprises.id"))
    code: Mapped[str] = mapped_column(String(128), unique=True)
    max_uses: Mapped[int] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Model(Base):
    """保存 STEP 原件位置及五态台账，特征随版本单独存档。"""

    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'INDEXING', 'APPROVED', 'PARSE_FAILED', 'REJECTED')",
            name="ck_models_status",
        ),
        Index("ix_models_enterprise_status", "enterprise_id", "status"),
        Index("ix_models_enterprise_md5", "enterprise_id", "md5"),
        Index("ix_models_index_queue", "status", "indexing_started_at", "id"),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprises.id"))
    is_public_demo: Mapped[bool] = mapped_column(server_default=false())
    md5: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'PENDING'"))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    reject_or_error_reason: Mapped[str | None] = mapped_column(Text)
    selected_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # 保持五态，通过独立时间戳区分待抢占与已抢占的索引任务。
    indexing_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class ModelFeature(Base):
    """依赖模型外键，为每个算法版本保留独立特征、网格及预览引用。"""

    __tablename__ = "model_features"
    __table_args__ = (
        UniqueConstraint("model_id", "feature_version", name="uq_model_features_model_version"),
        CheckConstraint("status IN ('READY', 'FAILED')", name="ck_model_features_status"),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    feature_version: Mapped[str] = mapped_column(String(32))
    feature_payload: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    sample_points_path: Mapped[str | None] = mapped_column(Text)
    glb_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    extracted_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class FeatureCatalog(Base):
    """持久化全局特征版本指针。单例记录由迁移初始化，供入库和切换事务共用。"""

    __tablename__ = "feature_catalog"
    __table_args__ = (CheckConstraint("id = 1", name="ck_feature_catalog_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_feature_version: Mapped[str] = mapped_column(String(32))


class SimilarityRule(Base):
    """保存全局或企业规则版本，生效标记受数据库作用域唯一约束。"""

    __tablename__ = "similarity_rules"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'GLOBAL' AND enterprise_id IS NULL)"
            " OR (scope = 'ENTERPRISE' AND enterprise_id IS NOT NULL)",
            name="ck_similarity_rules_scope",
        ),
        # 全局作用域单独索引，避免 NULL 的唯一索引语义放过多条生效规则。
        Index(
            "uq_similarity_rules_global_active", "scope", unique=True,
            postgresql_where=text("is_active AND scope = 'GLOBAL'"),
            sqlite_where=text("is_active AND scope = 'GLOBAL'"),
        ),
        Index(
            "uq_similarity_rules_enterprise_active", "enterprise_id", unique=True,
            postgresql_where=text("is_active AND scope = 'ENTERPRISE'"),
            sqlite_where=text("is_active AND scope = 'ENTERPRISE'"),
        ),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    scope: Mapped[str] = mapped_column(String(12))
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprises.id"))
    name: Mapped[str] = mapped_column(String(150))
    version: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(server_default=false())
    # 保留历史规则字段用于迁移兼容。新任务从 feature_catalog 固定版本快照。
    active_feature_version: Mapped[str] = mapped_column(String(32), server_default=text("'1.0.0'"))
    weights_json: Mapped[dict] = mapped_column(JSON_DOCUMENT)
    thresholds_json: Mapped[dict] = mapped_column(JSON_DOCUMENT)
    missing_policy_json: Mapped[dict] = mapped_column(JSON_DOCUMENT)
    max_refine_k: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())


class CompareJob(Base):
    """独立归档查询、规则快照和消费状态，依赖账号与可空的企业外键。"""

    __tablename__ = "compare_jobs"
    __table_args__ = (
        CheckConstraint("source IN ('WEB', 'CLIENT_API')", name="ck_compare_jobs_source"),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')", name="ck_compare_jobs_status",
        ),
        CheckConstraint(
            "termination IN ('exact_pruned', 'truncated_by_k', 'exact_no_d5')",
            name="ck_compare_jobs_termination",
        ),
        Index("ix_compare_jobs_queue", "status", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    enterprise_id: Mapped[int | None] = mapped_column(ForeignKey("enterprises.id"))
    source: Mapped[str] = mapped_column(String(12))
    query_filename: Mapped[str] = mapped_column(String(255))
    query_md5: Mapped[str] = mapped_column(String(32))
    # 旧任务与工序测试可无原件引用；调度器只消费带受信存储键的任务。
    query_storage_path: Mapped[str | None] = mapped_column(Text)
    rule_version_snapshot: Mapped[dict] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(10), server_default=text("'QUEUED'"))
    termination: Mapped[str | None] = mapped_column(String(16))
    max_untested_upper: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class TrialReservation(Base):
    """按任务保留一条试用预占记录，预占服务负责 CAS 状态变化。"""

    __tablename__ = "trial_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED')",
            name="ck_trial_reservations_status",
        ),
        Index("ix_trial_reservations_reaper", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[int] = mapped_column(ForeignKey("compare_jobs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(12), server_default=text("'RESERVED'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP)


class CompareResult(Base):
    """按任务和模型保存候选及诊断，允许待复核项的排名与分数为空。"""

    __tablename__ = "compare_results"
    __table_args__ = (UniqueConstraint("job_id", "model_id", name="uq_compare_results_job_model"),)

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("compare_jobs.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    rank: Mapped[int | None] = mapped_column(Integer)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(server_default=false())
    review_reason: Mapped[str | None] = mapped_column(String(64))
    breakdown_json: Mapped[dict | list] = mapped_column(JSON_DOCUMENT)
    align_matrix_json: Mapped[list | None] = mapped_column(JSON_DOCUMENT)
    is_selected: Mapped[bool] = mapped_column(server_default=false())


class AuditLog(Base):
    """持久化已脱敏的行为记录，支持无账号归属的失败事件。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(ID, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    detail_json: Mapped[dict] = mapped_column(JSON_DOCUMENT)
    ip_address: Mapped[str | None] = mapped_column(String(45).with_variant(INET(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
