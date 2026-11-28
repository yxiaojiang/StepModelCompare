"""保存已入队对比任务的查询原件引用，供常驻消费者跨进程读取。"""

from alembic import op
import sqlalchemy as sa

revision = "0003_compare_source"
down_revision = "0002_feature_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """以可空列保留旧任务，数据库异常向上传播。"""
    op.add_column("compare_jobs", sa.Column("query_storage_path", sa.Text(), nullable=True))


def downgrade() -> None:
    """删除原件引用列，数据库异常向上传播，原件文件由存储生命周期管理。"""
    op.drop_column("compare_jobs", "query_storage_path")
