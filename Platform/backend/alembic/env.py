"""配置 Alembic 在线和离线迁移。依赖 Settings 与 ORM，迁移后释放自建连接。"""

from alembic import context
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.models import Base
from app.db.session import build_engine


def _run_online(settings: Settings) -> None:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        _migrate_connection(supplied_connection)
        return

    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            _migrate_connection(connection)
    finally:
        engine.dispose()


def _migrate_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_offline(settings: Settings) -> None:
    driver = make_url(settings.database_url.get_secret_value()).drivername
    context.configure(dialect_name=driver, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    _run_offline(Settings())
else:
    _run_online(Settings())
