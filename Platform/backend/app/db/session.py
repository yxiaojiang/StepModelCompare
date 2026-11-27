"""创建数据库引擎。依赖 SQLAlchemy 与后端驱动，由调用方负责释放连接池。"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from app.config import Settings


def build_engine(settings: Settings) -> Engine:
    """读取 settings 的地址并返回惰性引擎，驱动无效时由 SQLAlchemy 抛出异常。"""
    url = make_url(settings.database_url.get_secret_value())
    connect_args = (
        {"connect_timeout": 3} if url.get_backend_name() == "postgresql"
        else {"check_same_thread": False}
    )
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        # SQLite 基础测试也需要外键防护，连接池中每条连接均须显式启用。
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()
