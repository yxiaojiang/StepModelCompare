"""${message}

迁移 ${up_revision}，前置版本 ${down_revision | comma,n}。
依赖 Alembic 操作上下文，由迁移命令执行。
"""

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    """升级当前数据库，无参数或返回值，数据库操作失败时传播异常。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚当前数据库，无参数或返回值，数据库操作失败时传播异常。"""
    ${downgrades if downgrades else "pass"}
