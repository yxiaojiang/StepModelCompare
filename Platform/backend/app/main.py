"""提供 FastAPI 入口与数据库健康检查。依赖 Settings、SQLAlchemy 和 ASGI 安全中间件。"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import build_engine
from app.http_security import RejectUrlCredentials, enable_log_redaction

_LOG = logging.getLogger("step_platform")


def create_app() -> FastAPI:
    """创建带凭证拦截的 API 实例，无参数，启动阶段配置错误将阻止应用启动。"""
    enable_log_redaction()
    application = FastAPI(title="STEP 模型相似度平台", lifespan=_lifespan)
    application.add_middleware(RejectUrlCredentials)
    application.add_api_route("/health", health, methods=["GET"])
    return application


@asynccontextmanager
async def _lifespan(application: FastAPI):
    # 自定义服务日志配置可能在应用导入后载入，启动时再补装一次过滤器。
    enable_log_redaction()
    settings = Settings()
    engine = build_engine(settings)
    application.state.settings = settings
    application.state.engine = engine
    try:
        yield
    finally:
        engine.dispose()


def health(request: Request) -> JSONResponse:
    """用 request 所属应用的数据库连接做探活，返回 200，连接或查询故障返回 503。"""
    try:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        _LOG.warning("健康检查未能连接或查询数据库")
        return JSONResponse({"status": "unavailable"}, 503)
    return JSONResponse({"status": "ok"})


app = create_app()
