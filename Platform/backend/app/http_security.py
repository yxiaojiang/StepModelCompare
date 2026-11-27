"""在 ASGI 入口拦截 URL 凭证，过滤平台与 Uvicorn 日志。依赖 Starlette，随应用启动启用。"""

import logging
import re
from urllib.parse import parse_qsl, unquote

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_CREDENTIAL_KEY = re.compile(
    r"(?:(?:client|proxy)?authorization|(?:set)?cookie|.*(?:password|passwd|token|secret)"
    r"|pass|pwd|passphrase|(?:x)?apikey|invitecode|session(?:id)?|credentials?)",
    re.IGNORECASE,
)
_LOG_CREDENTIAL = re.compile(
    r"(?i)(?:(?:password(?:[_ -]?hash)?|passwd|pwd|passphrase|token|secret|auth(?:orization)?|api[_ -]?key"
    r"|invite[_ -]?code|cookie|session[_ -]?id)['\"]?\s*[:=]"
    r"|\b(?:bearer|basic)\s+\S|\b[\w+.-]+://[^\s/]+:[^\s/]+@)"
)


class RejectUrlCredentials:
    """依赖下游 ASGI 应用。只检查 HTTP 查询键，命中时在路由前中止。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """检查 scope 的查询键，通过 receive/send 转发请求或直接返回 400 JSON，无返回值。"""
        if scope["type"] == "http":
            raw = scope.get("query_string", b"")
            try:
                keys = _query_keys(raw)
            except ValueError:
                await JSONResponse({"error_code": "INVALID_QUERY"}, 400)(scope, receive, send)
                return
            if any(_is_credential(key) for key in keys):
                await JSONResponse(
                    {"error_code": "CREDENTIALS_IN_URL_FORBIDDEN"}, 400,
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _query_keys(raw: bytes) -> list[str]:
    # 丢弃值且不读请求体，避免把凭证带入错误响应。兼容已有客户端常见的分号分隔。
    return [
        key for key, _ in parse_qsl(
            raw.decode("latin-1").replace(";", "&"), keep_blank_values=True, max_num_fields=1024,
        )
    ]


def _is_credential(key: str) -> bool:
    pieces = re.split(r"[.\[\]]+", key.strip())
    return any(
        _CREDENTIAL_KEY.fullmatch(re.sub(r"[^a-z0-9]", "", piece.lower()))
        for piece in pieces
    )


class CredentialLogFilter(logging.Filter):
    """处理平台日志记录后再交给格式化器，禁止异常堆栈、原始查询串和凭证消息外泄。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """脱敏 record 后返回 True，携带原始请求作用域的 ASGI 跟踪记录返回 False。"""
        if record.name == "uvicorn.asgi":
            # 该跟踪记录可能序列化原始请求头与作用域，逐项识别无法覆盖所有凭证格式。
            return False
        if record.name == "uvicorn.access":
            # Uvicorn 的第三个参数含原始请求目标，应用中间件拦截后仍会产生访问日志。
            if isinstance(record.args, tuple) and len(record.args) == 5:
                client, method, target, protocol, status = record.args
                record.args = client, method, re.split(r"[?#]", str(target), maxsplit=1)[0], protocol, status
            else:
                record.msg, record.args = "收到 HTTP 请求", ()

        if record.exc_info or record.exc_text or record.stack_info:
            record.msg, record.args = "请求或服务处理异常（详情已脱敏）", ()
            record.exc_info = record.exc_text = record.stack_info = None
            return True

        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            record.msg, record.args = "日志格式无效（内容已脱敏）", ()
            return True
        if _LOG_CREDENTIAL.search(unquote(message)):
            record.msg, record.args = "日志包含凭证字段（内容已脱敏）", ()
        return True


def enable_log_redaction() -> None:
    """为平台及 Uvicorn 日志安装幂等过滤器，无参数或返回值。"""
    for name in ("step_platform", "uvicorn", "uvicorn.access", "uvicorn.error", "uvicorn.asgi"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, CredentialLogFilter) for item in logger.filters):
            logger.addFilter(CredentialLogFilter())
