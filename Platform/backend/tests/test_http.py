"""验证启动配置、健康检查和凭证保护。依赖 unittest 与 httpx，数据库使用 SQLite 基础替身。"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.http_security import CredentialLogFilter, enable_log_redaction
from app.main import create_app


@asynccontextmanager
async def _client():
    application = create_app()
    async with application.router.lifespan_context(application):
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
            yield client


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    """验证阶段一配置与启动严格校验，环境变量仅在单项测试期间替换。"""

    def test_strict_ttl_inequality(self):
        """用指定时长测试等号和小于被拒绝，严格大于通过。"""
        for ttl in (209, 210):
            with self.subTest(ttl=ttl), self.assertRaises(ValidationError):
                Settings(queue_wait_timeout=60, compare_job_timeout=120, reaper_ttl=ttl)
        self.assertEqual(Settings(queue_wait_timeout=60, compare_job_timeout=120, reaper_ttl=211).reaper_ttl, 211)

    def test_overrides(self):
        """覆盖精排、版本及解析上限，验证配置可按环境变量冻结。"""
        overrides = {
            "STEP_PLATFORM_MAX_REFINE_K": "22",
            "STEP_PLATFORM_ACTIVE_FEATURE_VERSION": "2.0.0",
            "STEP_PLATFORM_MAX_BREP_FACES": "17",
            "STEP_PLATFORM_MAX_MESH_TRIANGLES": "250",
        }
        with patch.dict(os.environ, overrides):
            settings = Settings()
        self.assertEqual(settings.max_refine_k, 22)
        self.assertEqual(settings.active_feature_version, "2.0.0")
        self.assertEqual(settings.parse_limits().max_brep_faces, 17)
        self.assertEqual(settings.parse_limits().max_mesh_triangles, 250)

    async def test_invalid_config_fails_startup(self):
        """使回收期等于边界，确认 FastAPI 启动抛出配置异常。"""
        overrides = {
            "STEP_PLATFORM_REAPER_TTL": "210",
            "STEP_PLATFORM_COMPARE_JOB_TIMEOUT": "120",
            "STEP_PLATFORM_QUEUE_WAIT_TIMEOUT": "60",
        }
        with patch.dict(os.environ, overrides), self.assertRaisesRegex(ValidationError, "reaper_ttl"):
            async with _client():
                self.fail("非法配置下应用进入运行状态")

    def test_database_url_secret_is_hidden(self):
        """凭证输入和模型显示经掩码，校验错误不带原始连接地址。"""
        with self.assertRaises(ValidationError) as raised:
            Settings(database_url="invalid-driver://name:private-value@localhost/db")
        self.assertNotIn("private-value", str(raised.exception))


class HttpTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 ASGI 入口拦截与健康检查，日志测试使用 Uvicorn 的访问记录形状。"""

    async def test_url_credentials(self):
        """普通查询通过，编码、大小写、无值和嵌套凭证键在路由前被拒绝。"""
        with patch.dict(os.environ, {"STEP_PLATFORM_DATABASE_URL": "sqlite://"}):
            async with _client() as client:
                for query in (
                    "password=raw-value", "P%57D=raw-value", "%74oken=raw-value", "token",
                    "auth%5Baccess-token%5D=raw-value", "ok=1;api_key=raw-value",
                    "refresh_token=raw-value", "password=&password=raw-value",
                ):
                    with self.subTest(query=query):
                        response = await client.get(f"/health?{query}")
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response.json()["error_code"], "CREDENTIALS_IN_URL_FORBIDDEN")
                        self.assertNotIn("raw-value", response.text)
                self.assertEqual((await client.get("/health?search=token")).json(), {"status": "ok"})
                self.assertEqual((await client.get("/missing?password=raw-value")).status_code, 400)
                oversized = "&".join(["a=1"] * 1025)
                self.assertEqual((await client.get(f"/health?{oversized}")).json()["error_code"], "INVALID_QUERY")

    async def test_health_unavailable(self):
        """数据库连接失败时返回 503，并保持错误响应为固定内容。"""
        with TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory).as_posix()}/missing/db.sqlite"
            with patch.dict(os.environ, {"STEP_PLATFORM_DATABASE_URL": url}):
                async with _client() as client:
                    response = await client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    async def test_redaction_restored_after_logger_reconfiguration(self):
        """模拟应用导入后替换 logger 过滤器，验证启动补装且重复启动不增加过滤器。"""
        application = create_app()
        logger = logging.getLogger("uvicorn.access")
        with patch.object(logger, "filters", []), patch.dict(os.environ, {"STEP_PLATFORM_DATABASE_URL": "sqlite://"}):
            for _ in range(2):
                async with application.router.lifespan_context(application):
                    with self.assertLogs(logger, level="INFO") as access:
                        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", "/health?token=private", "1.1", 400)
                    self.assertIn('GET /health HTTP/1.1" 400', access.output[0])
                    self.assertNotIn("private", access.output[0])
                    self.assertEqual(sum(isinstance(item, CredentialLogFilter) for item in logger.filters), 1)

    def test_redaction_before_formatting(self):
        """验证访问日志保留状态但丢弃查询值，其他凭证及异常消息写成固定文本。"""
        enable_log_redaction()
        with self.assertLogs("uvicorn.access", level="INFO") as access:
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET",
                "/health?%74oken=url-private", "1.1", 400,
            )
        self.assertIn('GET /health HTTP/1.1" 400', access.output[0])
        self.assertNotIn("url-private", access.output[0])
        with self.assertNoLogs("uvicorn.asgi", level="DEBUG"):
            logging.getLogger("uvicorn.asgi").debug("ASGI %r", [(b"cookie", b"session=scope-private")])
        with self.assertLogs("step_platform", level="INFO") as captured:
            logger = logging.getLogger("step_platform")
            logger.info("请求凭证 %r", {"password": "body-private"})
            logger.info("请求头 Authorization: Basic %s", "header-private")
            try:
                raise ValueError("exception-private")
            except ValueError:
                logger.exception("处理异常")
        self.assertNotIn("-private", " ".join(captured.output))
