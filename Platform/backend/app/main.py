"""提供 FastAPI 入口与数据库健康检查。依赖 Settings、SQLAlchemy 和 ASGI 安全中间件。"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import build_engine
from app.http_security import RejectUrlCredentials, enable_log_redaction
from app.api import build_api_router
from app.auth.cache import AuthSuccessCache
from app.auth.rate_limit import AuthRateLimiter
from app.auth.service import AuthService
from app.auth.tokens import TokenService
from app.queue_runtime import queue_workers

_LOG = logging.getLogger("step_platform")


def create_app() -> FastAPI:
    """创建带凭证拦截的 API 实例，无参数，启动阶段配置错误将阻止应用启动。"""
    enable_log_redaction()
    application = FastAPI(title="STEP 模型相似度平台", lifespan=_lifespan)
    application.add_middleware(RejectUrlCredentials)
    application.add_api_route("/health", health, methods=["GET"])
    application.include_router(build_api_router())
    application.add_exception_handler(RequestValidationError, _invalid_request)
    return application


@asynccontextmanager
async def _lifespan(application: FastAPI):
    # 自定义服务日志配置可能在应用导入后载入，启动时再补装一次过滤器。
    enable_log_redaction()
    settings = Settings()
    engine = build_engine(settings)
    application.state.settings = settings
    application.state.engine = engine
    def action_limiter(max_attempts):
        return AuthRateLimiter(
            max_attempts, settings.auth_rate_limit_window,
            capacity=settings.auth_rate_limit_capacity,
        )
    try:
        application.state.auth_service = None
        application.state.token_service = None
        application.state.invite_limiter = action_limiter(settings.invite_bind_max_attempts)
        application.state.upload_limiter = action_limiter(settings.upload_max_attempts)
        if settings.auth_pepper is not None and settings.jwt_secret is not None:
            application.state.auth_service = AuthService(
                engine, settings.auth_pepper.get_secret_value(),
                cache=AuthSuccessCache(settings.auth_cache_capacity, settings.auth_cache_ttl),
                limiter=AuthRateLimiter(
                    settings.auth_rate_limit_max_attempts, settings.auth_rate_limit_window,
                    capacity=settings.auth_rate_limit_capacity,
                ),
            )
            application.state.token_service = TokenService(settings.jwt_secret.get_secret_value(), settings.jwt_ttl)
        workers = queue_workers(engine, settings)
        workers.__enter__()
        try:
            yield
        finally:
            await run_in_threadpool(workers.__exit__, None, None, None)
    finally:
        engine.dispose()


async def _invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
    # 验证错误的输入快照可能包含密码或邀请码，统一使用固定响应。
    return JSONResponse({"error_code": "INVALID_REQUEST"}, 422)


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
