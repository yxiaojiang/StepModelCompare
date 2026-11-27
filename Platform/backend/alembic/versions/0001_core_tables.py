"""平台核心表

迁移 0001，首版无前置版本。
依赖 Alembic 操作上下文，由迁移命令执行。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
_JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), 'postgresql')


def upgrade() -> None:
    """升级当前数据库，无参数或返回值，数据库操作失败时传播异常。"""
    op.create_table(
        'enterprises',
        sa.Column('id', _ID, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('code', sa.String(length=100), nullable=False),
        sa.Column('storage_quota_mb', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code')
    )
    op.create_table(
        'users',
        sa.Column('id', _ID, nullable=False),
        sa.Column('username', sa.String(length=150), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('auth_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('enterprise_id', _ID, nullable=True),
        sa.Column('is_trial', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('used_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('in_flight_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('failed_login_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('SYS_ADMIN', 'ENT_ADMIN', 'ENT_USER', 'TRIAL_USER')", name='ck_users_role'),
        sa.CheckConstraint('auth_version >= 1 AND used_count >= 0 AND in_flight_count >= 0 AND failed_login_count >= 0', name='ck_users_counters'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username')
    )
    op.create_table(
        'audit_logs',
        sa.Column('id', _ID, nullable=False),
        sa.Column('actor_id', _ID, nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=64), nullable=False),
        sa.Column('target_id', sa.BigInteger(), nullable=True),
        sa.Column('detail_json', _JSON, nullable=False),
        sa.Column('ip_address', sa.String(length=45).with_variant(postgresql.INET(), 'postgresql'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'compare_jobs',
        sa.Column('id', _ID, nullable=False),
        sa.Column('user_id', _ID, nullable=False),
        sa.Column('enterprise_id', _ID, nullable=True),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('query_filename', sa.String(length=255), nullable=False),
        sa.Column('query_md5', sa.String(length=32), nullable=False),
        sa.Column('rule_version_snapshot', _JSON, nullable=False),
        sa.Column('status', sa.String(length=10), server_default=sa.text("'QUEUED'"), nullable=False),
        sa.Column('termination', sa.String(length=16), nullable=True),
        sa.Column('max_untested_upper', sa.Float(), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('WEB', 'CLIENT_API')", name='ck_compare_jobs_source'),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')", name='ck_compare_jobs_status'),
        sa.CheckConstraint("termination IN ('exact_pruned', 'truncated_by_k', 'exact_no_d5')", name='ck_compare_jobs_termination'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_compare_jobs_queue', 'compare_jobs', ['status', 'created_at', 'id'], unique=False)
    op.create_table(
        'invite_codes',
        sa.Column('id', _ID, nullable=False),
        sa.Column('enterprise_id', _ID, nullable=False),
        sa.Column('code', sa.String(length=128), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=False),
        sa.Column('used_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('created_by', _ID, nullable=False),
        sa.CheckConstraint('max_uses > 0 AND used_count >= 0 AND used_count <= max_uses', name='ck_invite_codes_usage'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code')
    )
    op.create_table(
        'models',
        sa.Column('id', _ID, nullable=False),
        sa.Column('enterprise_id', _ID, nullable=True),
        sa.Column('is_public_demo', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('md5', sa.String(length=32), nullable=False),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('storage_path', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column('uploader_id', _ID, nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('reviewer_id', _ID, nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reject_or_error_reason', sa.Text(), nullable=True),
        sa.Column('selected_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('indexing_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'INDEXING', 'APPROVED', 'PARSE_FAILED', 'REJECTED')", name='ck_models_status'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_models_enterprise_md5', 'models', ['enterprise_id', 'md5'], unique=False)
    op.create_index('ix_models_enterprise_status', 'models', ['enterprise_id', 'status'], unique=False)
    op.create_index('ix_models_index_queue', 'models', ['status', 'indexing_started_at', 'id'], unique=False)
    op.create_table(
        'similarity_rules',
        sa.Column('id', _ID, nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('enterprise_id', _ID, nullable=True),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('version', sa.String(length=40), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('active_feature_version', sa.String(length=32), server_default=sa.text("'1.0.0'"), nullable=False),
        sa.Column('weights_json', _JSON, nullable=False),
        sa.Column('thresholds_json', _JSON, nullable=False),
        sa.Column('missing_policy_json', _JSON, nullable=False),
        sa.Column('max_refine_k', sa.Integer(), nullable=False),
        sa.Column('updated_by', _ID, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("(scope = 'GLOBAL' AND enterprise_id IS NULL) OR (scope = 'ENTERPRISE' AND enterprise_id IS NOT NULL)", name='ck_similarity_rules_scope'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_similarity_rules_enterprise_active', 'similarity_rules', ['enterprise_id'], unique=True, postgresql_where=sa.text("is_active AND scope = 'ENTERPRISE'"), sqlite_where=sa.text("is_active AND scope = 'ENTERPRISE'"))
    op.create_index('uq_similarity_rules_global_active', 'similarity_rules', ['scope'], unique=True, postgresql_where=sa.text("is_active AND scope = 'GLOBAL'"), sqlite_where=sa.text("is_active AND scope = 'GLOBAL'"))
    op.create_table(
        'compare_results',
        sa.Column('id', _ID, nullable=False),
        sa.Column('job_id', _ID, nullable=False),
        sa.Column('model_id', _ID, nullable=False),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('similarity_score', sa.Float(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('review_reason', sa.String(length=64), nullable=True),
        sa.Column('breakdown_json', _JSON, nullable=False),
        sa.Column('align_matrix_json', _JSON, nullable=True),
        sa.Column('is_selected', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['compare_jobs.id'], ),
        sa.ForeignKeyConstraint(['model_id'], ['models.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'model_id', name='uq_compare_results_job_model')
    )
    op.create_table(
        'model_features',
        sa.Column('id', _ID, nullable=False),
        sa.Column('model_id', _ID, nullable=False),
        sa.Column('feature_version', sa.String(length=32), nullable=False),
        sa.Column('feature_payload', _JSON, nullable=True),
        sa.Column('sample_points_path', sa.Text(), nullable=True),
        sa.Column('glb_path', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('extracted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('READY', 'FAILED')", name='ck_model_features_status'),
        sa.ForeignKeyConstraint(['model_id'], ['models.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_id', 'feature_version', name='uq_model_features_model_version')
    )
    op.create_table(
        'trial_reservations',
        sa.Column('id', _ID, nullable=False),
        sa.Column('user_id', _ID, nullable=False),
        sa.Column('job_id', _ID, nullable=False),
        sa.Column('status', sa.String(length=12), server_default=sa.text("'RESERVED'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED')", name='ck_trial_reservations_status'),
        sa.ForeignKeyConstraint(['job_id'], ['compare_jobs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_trial_reservations_reaper', 'trial_reservations', ['status', 'expires_at'], unique=False)


def downgrade() -> None:
    """回滚当前数据库，无参数或返回值，数据库操作失败时传播异常。"""
    op.drop_index('ix_trial_reservations_reaper', table_name='trial_reservations')
    op.drop_table('trial_reservations')
    op.drop_table('model_features')
    op.drop_table('compare_results')
    op.drop_index('uq_similarity_rules_global_active', table_name='similarity_rules')
    op.drop_index('uq_similarity_rules_enterprise_active', table_name='similarity_rules')
    op.drop_table('similarity_rules')
    op.drop_index('ix_models_index_queue', table_name='models')
    op.drop_index('ix_models_enterprise_status', table_name='models')
    op.drop_index('ix_models_enterprise_md5', table_name='models')
    op.drop_table('models')
    op.drop_table('invite_codes')
    op.drop_index('ix_compare_jobs_queue', table_name='compare_jobs')
    op.drop_table('compare_jobs')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_table('enterprises')
