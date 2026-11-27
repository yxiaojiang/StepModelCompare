"""增加全局特征版本指针，并继承已配置的全局规则版本。"""

from alembic import op
import sqlalchemy as sa

revision = "0002_feature_catalog"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建单例指针并初始化。无参数或返回值，数据库异常由 Alembic 处理。"""
    op.create_table(
        "feature_catalog",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("active_feature_version", sa.String(32), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_feature_catalog_singleton"),
    )
    op.execute(sa.text(
        "INSERT INTO feature_catalog (id, active_feature_version) "
        "SELECT 1, COALESCE((SELECT active_feature_version FROM similarity_rules "
        "WHERE scope = 'GLOBAL' AND is_active), '1.0.0')"
    ))


def downgrade() -> None:
    """将全局指针回写旧规则后删除单例表。无参数或返回值，数据库异常向上传播。"""
    op.execute(sa.text(
        "UPDATE similarity_rules SET active_feature_version = "
        "(SELECT active_feature_version FROM feature_catalog WHERE id = 1) "
        "WHERE scope = 'GLOBAL' AND is_active"
    ))
    op.drop_table("feature_catalog")
