"""验证阶段 3 首切片的鉴权、企业绑定、隔离守卫和试用配额 CAS。"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from app.auth.cache import AuthSuccessCache
from app.auth.rate_limit import AuthRateLimiter
from app.auth.service import AuthService, AuthenticationError, AuthenticationRateLimited
from app.config import Settings
from app.db.models import CompareJob, CompareResult, Enterprise, Model, ModelFeature, User
from app.db.session import build_engine
from app.enterprise import EnterpriseError, EnterpriseService
from app.main import create_app
from app.repository.quota import (
    QuotaError, commit_trial_results, enqueue_trial_compare, reap_expired_trial_quota, release_trial_quota,
)
from app.repository.visibility import VisibilityError, VisibilityUser, apply_visibility_scope


_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _migration(engine, command_fn, *args):
    config = Config(str(_ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command_fn(config, *args)


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class AuthAndEnterpriseTests(unittest.TestCase):
    """使用迁移后的 SQLite 数据库验证凭证和企业邀请码业务状态。"""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.engine = build_engine(Settings(database_url=f"sqlite:///{Path(self.directory.name, 'db.sqlite')}"))
        _migration(self.engine, command.upgrade, "head")

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_cache_is_bounded_and_ttl_expired(self):
        """验证成功缓存的 LRU 容量和 TTL，不保存失败项。"""
        clock = _Clock()
        cache = AuthSuccessCache(capacity=2, ttl_seconds=10, clock=clock)
        from app.auth.cache import AuthCacheValue
        value = AuthCacheValue(1, "u", 1, "TRIAL_USER", None, True)
        cache.put("a", value)
        cache.put("b", value)
        self.assertIs(cache.get("a"), value)
        cache.put("c", value)
        self.assertIsNone(cache.get("b"))
        clock.value += 10
        self.assertIsNone(cache.get("a"))

    def test_auth_version_invalidates_cached_success_and_failed_password_not_cached(self):
        """验证 Argon2id 成功缓存、改密版本失效和错误密码双限流。"""
        clock = _Clock()
        service = AuthService(
            self.engine, "pepper", AuthSuccessCache(8, 60, clock), AuthRateLimiter(2, 60, clock),
        )
        registered = service.register("trial", "correct-password")
        self.assertEqual(registered.role, "TRIAL_USER")
        self.assertEqual(service.authenticate("trial", "correct-password", "10.0.0.1").user_id, registered.user_id)
        with self.assertRaises(AuthenticationError):
            service.authenticate("trial", "wrong-password", "10.0.0.1")
        with self.assertRaises(AuthenticationRateLimited):
            service.authenticate("trial", "wrong-password", "10.0.0.1")
        with self.assertRaises(AuthenticationRateLimited):
            service.authenticate("trial", "wrong-password", "10.0.0.1")
        self.assertEqual(len(service.cache), 1)
        service.set_password(registered.user_id, "new-password")
        with self.assertRaises(AuthenticationRateLimited):
            service.authenticate("trial", "correct-password", "10.0.0.2")
        clock.value += 60
        self.assertEqual(service.authenticate("trial", "new-password", "10.0.0.2").auth_version, 2)
        with self.assertRaises(AuthenticationError):
            service.authenticate("trial", "correct-password", "10.0.0.2")

    def test_lockout_persists_and_invalidates_cached_credentials(self):
        """五次失败后锁定事务已提交，旧缓存和令牌版本立即失效。"""
        service = AuthService(self.engine, "pepper", limiter=AuthRateLimiter(10))
        identity = service.register("trial", "correct-password")
        service.authenticate("trial", "correct-password", "10.0.0.1")
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                service.authenticate("trial", "wrong-password", "10.0.0.1")
        with self.engine.connect() as connection:
            user = connection.execute(select(User).where(User.id == identity.user_id)).one()
        self.assertEqual((user.auth_version, user.failed_login_count), (2, 0))
        self.assertIsNotNone(user.locked_until)
        with self.assertRaises(AuthenticationError):
            service.authenticate("trial", "correct-password", "10.0.0.1")
        with self.assertRaises(AuthenticationError):
            service.resolve_token_user(identity.user_id, 1)

    def test_both_rate_limit_dimensions_and_capacity(self):
        """验证独立 IP/账号维度以及满容量拒绝新桶，过期后恢复。"""
        clock = _Clock()
        limiter = AuthRateLimiter(1, 60, clock, capacity=3)
        self.assertTrue(limiter.allow("ip:a", "user:a"))
        self.assertFalse(limiter.allow("ip:a", "user:b"))
        self.assertFalse(limiter.allow("ip:b", "user:a"))
        self.assertFalse(limiter.allow("ip:b", "user:b"))
        clock.value += 60
        self.assertTrue(limiter.allow("ip:b", "user:b"))

    def test_invite_binding_increments_auth_version(self):
        """验证邀请码限次核销、试用转企业用户和权限版本递增。"""
        auth = AuthService(self.engine, "pepper")
        trial = auth.register("trial", "correct-password")
        admin = auth.register("admin", "correct-password", is_trial=False)
        with self.engine.begin() as connection:
            connection.execute(User.__table__.update().where(User.id == admin.user_id).values(role="SYS_ADMIN"))
        enterprise = EnterpriseService(self.engine).create_enterprise("企业 A", "ent-a")
        invite = EnterpriseService(self.engine).create_invite_code(
            enterprise, "INVITE-A", 1, datetime.now(timezone.utc) + timedelta(days=1), admin.user_id,
        )
        self.assertGreater(invite, 0)
        self.assertEqual(EnterpriseService(self.engine).bind_invite(trial.user_id, "INVITE-A"), enterprise)
        with self.engine.connect() as connection:
            user = connection.execute(select(User).where(User.id == trial.user_id)).one()
            self.assertEqual((user.enterprise_id, user.role, user.is_trial, user.auth_version), (enterprise, "ENT_USER", False, 2))
        with self.assertRaises(EnterpriseError):
            EnterpriseService(self.engine).bind_invite(trial.user_id, "INVITE-A")


class VisibilityAndQuotaTests(unittest.TestCase):
    """验证 Repository 强制隔离和预占 CAS 迟到结果拦截。"""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.engine = build_engine(Settings(database_url=f"sqlite:///{Path(self.directory.name, 'db.sqlite')}"))
        _migration(self.engine, command.upgrade, "head")
        with self.engine.begin() as connection:
            enterprises = Enterprise.__table__
            self.enterprise_a = connection.execute(enterprises.insert().values(
                name="A", code="a", storage_quota_mb=100, status="ACTIVE",
            ).returning(enterprises.c.id)).scalar_one()
            self.enterprise_b = connection.execute(enterprises.insert().values(
                name="B", code="b", storage_quota_mb=100, status="ACTIVE",
            ).returning(enterprises.c.id)).scalar_one()
            users = User.__table__
            self.user = connection.execute(users.insert().values(
                username="trial", password_hash="hash", role="TRIAL_USER", is_trial=True,
            ).returning(users.c.id)).scalar_one()
            self.uploader = connection.execute(users.insert().values(
                username="uploader", password_hash="hash", role="ENT_USER", enterprise_id=self.enterprise_a,
            ).returning(users.c.id)).scalar_one()
            models = Model.__table__
            self.model_a = connection.execute(models.insert().values(
                enterprise_id=self.enterprise_a, is_public_demo=False, md5="a" * 32, model_name="a",
                original_filename="a.step", file_size_bytes=1, storage_path="a", status="APPROVED",
                uploader_id=self.uploader,
            ).returning(models.c.id)).scalar_one()
            self.model_b = connection.execute(models.insert().values(
                enterprise_id=self.enterprise_b, is_public_demo=True, md5="b" * 32, model_name="b",
                original_filename="b.step", file_size_bytes=1, storage_path="b", status="APPROVED",
                uploader_id=self.uploader,
            ).returning(models.c.id)).scalar_one()
            self.pending = connection.execute(models.insert().values(
                enterprise_id=self.enterprise_a, is_public_demo=True, md5="c" * 32, model_name="c",
                original_filename="c.step", file_size_bytes=1, storage_path="c", status="PENDING",
                uploader_id=self.uploader,
            ).returning(models.c.id)).scalar_one()

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_visibility_scope_cannot_be_widened_by_target_enterprise(self):
        """验证企业用户忽略越权企业参数，试用用户不见他企非公开或待审模型。"""
        with self.assertRaises(VisibilityError):
            apply_visibility_scope(select(aliased(Model)), VisibilityUser(self.uploader, "ENT_USER", self.enterprise_a))
        with self.engine.connect() as connection:
            rows = connection.execute(apply_visibility_scope(
                select(Model), VisibilityUser(self.uploader, "ENT_USER", self.enterprise_a), self.enterprise_b,
            )).all()
            self.assertEqual([row.id for row in rows], [self.model_a])
            trial_rows = connection.execute(apply_visibility_scope(
                select(Model), VisibilityUser(self.user, "TRIAL_USER", None, True), self.enterprise_a,
            )).all()
            self.assertEqual([row.id for row in trial_rows], [self.model_b])
            for role, enterprise, target, expected in (
                ("ENT_ADMIN", self.enterprise_a, self.enterprise_b, [self.model_a]),
                ("SYS_ADMIN", None, self.enterprise_b, [self.model_b]),
                ("SYS_ADMIN", None, None, [self.model_a, self.model_b, self.pending]),
            ):
                statement = apply_visibility_scope(select(Model.id), VisibilityUser(self.uploader, role, enterprise), target)
                self.assertEqual(connection.execute(statement.order_by(Model.id)).scalars().all(), expected)

    def test_five_reservations_and_late_commit_are_blocked(self):
        """验证第五次预占成功、第六次拒绝，回收后迟到提交不写 compare_results。"""
        now = datetime(2026, 11, 28, tzinfo=timezone.utc)
        jobs = [self._enqueue(index, now) for index in range(5)]
        with self.assertRaisesRegex(QuotaError, "TRIAL_QUOTA_EXCEEDED"):
            self._enqueue(5, now)
        with self.engine.begin() as connection:
            connection.execute(update(CompareJob).where(CompareJob.id == jobs[0]).values(status="RUNNING"))
        self.assertEqual(reap_expired_trial_quota(self.engine, now + timedelta(seconds=212)), 5)
        self.assertFalse(commit_trial_results(self.engine, jobs[0], [], now=now + timedelta(seconds=213)))
        with self.engine.connect() as connection:
            self.assertEqual(len(connection.execute(select(CompareJob.id)).all()), 5)
            self.assertEqual(connection.execute(select(CompareResult).where(CompareResult.job_id == jobs[0])).all(), [])
            job = connection.execute(select(CompareJob).where(CompareJob.id == jobs[0])).one()
            user = connection.execute(select(User).where(User.id == self.user)).one()
            self.assertEqual((job.status, job.error_code, user.used_count, user.in_flight_count), ("FAILED", "QUOTA_EXPIRED", 0, 0))

    def test_release_visibility_and_idempotent_completion(self):
        """验证队列满不预占，越界结果回滚 CAS，重复完成不改变已落库结果。"""
        now = datetime.now(timezone.utc)
        failed = self._enqueue(1, now)
        with self.assertRaisesRegex(QuotaError, "QUEUE_FULL"):
            self._enqueue(2, now, depth=1)
        self.assertTrue(release_trial_quota(self.engine, failed, "INVALID_STEP_FILE"))
        self.assertFalse(release_trial_quota(self.engine, failed, "INVALID_STEP_FILE"))
        job_id = self._enqueue(3, now)
        with self.engine.begin() as connection:
            connection.execute(update(CompareJob).where(CompareJob.id == job_id).values(status="RUNNING"))
        with self.assertRaisesRegex(QuotaError, "RESULT_OUTSIDE_VISIBILITY_SCOPE"):
            commit_trial_results(self.engine, job_id, [{"model_id": self.model_a, "breakdown_json": {}}])
        self.assertTrue(commit_trial_results(self.engine, job_id, [{"model_id": self.model_b, "breakdown_json": {}}]))
        self.assertFalse(commit_trial_results(self.engine, job_id, []))
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(CompareJob.status).where(CompareJob.id == job_id)).scalar_one(), "DONE")
            self.assertEqual(connection.execute(select(CompareResult.model_id)).scalar_one(), self.model_b)
            self.assertEqual(connection.execute(select(User.used_count, User.in_flight_count)
                                                .where(User.id == self.user)).one(), (1, 0))

    def _enqueue(self, index, now, depth=32):
        return enqueue_trial_compare(
            self.engine, Settings(max_queue_depth=depth), VisibilityUser(self.user, "TRIAL_USER", None),
            source="WEB", query_filename=f"q{index}.step", query_md5=f"{index:032x}", rule_snapshot={},
            auth_version=1, now=now,
        )

    def test_http_scope_credentials_and_invite(self):
        """通过实际 ASGI 路由验证 Basic、Bearer、管理边界、列表/详情/GLB 隔离以及绑定后的失效。"""
        vault = Path(self.directory.name, "vault")
        vault.mkdir()
        (vault / "preview.glb").write_bytes(b"glb test fixture")
        overrides = {
            "STEP_PLATFORM_DATABASE_URL": self.engine.url.render_as_string(),
            "STEP_PLATFORM_AUTH_PEPPER": "p" * 32,
            "STEP_PLATFORM_JWT_SECRET": "t" * 32,
            "STEP_PLATFORM_MODEL_VAULT": str(vault),
        }
        auth = AuthService(self.engine, "p" * 32)
        member = auth.register("member", "member-password")
        admin = auth.register("admin", "admin-password")
        trial = auth.register("trial2", "trial-password")
        auth.set_role_or_enterprise(member.user_id, role="ENT_USER", enterprise_id=self.enterprise_a)
        auth.set_role_or_enterprise(admin.user_id, role="SYS_ADMIN", enterprise_id=None)
        with self.engine.begin() as connection:
            for model_id in (self.model_a, self.model_b):
                connection.execute(ModelFeature.__table__.insert().values(
                    model_id=model_id, feature_version="1.0.0", status="READY", glb_path="preview.glb",
                ))
        with patch.dict(os.environ, overrides), TestClient(create_app()) as client:
            def login(name, password):
                response = client.post("/api/v1/auth/token", json={"username": name, "password": password})
                self.assertEqual(response.status_code, 200, response.text)
                return {"Authorization": "Bearer " + response.json()["access_token"]}

            member_headers = login("member", "member-password")
            admin_headers = login("admin", "admin-password")
            trial_headers = login("trial2", "trial-password")
            self.assertEqual(client.get("/api/v1/admin/enterprises", headers=member_headers).status_code, 403)
            self.assertEqual(client.get("/api/v1/models", params={"enterprise_id": self.enterprise_b},
                                        headers=member_headers).json()[0]["id"], self.model_a)
            self.assertEqual(client.get(f"/api/v1/models/{self.model_b}", headers=member_headers).status_code, 404)
            self.assertEqual(client.get(f"/api/v1/models/{self.model_b}/glb", headers=member_headers).status_code, 404)
            self.assertEqual(client.get(f"/api/v1/models/{self.model_a}/glb", auth=("member", "member-password")).content,
                             b"glb test fixture")
            self.assertEqual([row["id"] for row in client.get("/api/v1/models", headers=trial_headers).json()],
                             [self.model_b])
            invalid = client.post("/api/v1/auth/token", json={"username": "trial2", "password": "leak-marker" * 110})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn("leak-marker", invalid.text)
            invite = client.post("/api/v1/admin/invite-codes", headers=admin_headers, json={
                "enterprise_id": self.enterprise_a, "code": "INVITE-HTTP", "max_uses": 1,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            })
            self.assertEqual(invite.status_code, 200, invite.text)
            self.assertNotIn("INVITE-HTTP", client.get("/api/v1/admin/invite-codes", headers=admin_headers).text)
            bound = client.post("/api/v1/auth/bind-invite", headers=trial_headers, json={"code": "INVITE-HTTP"})
            self.assertEqual(bound.status_code, 200, bound.text)
            self.assertEqual(client.get("/api/v1/models", headers=trial_headers).status_code, 401)
            headers = login("trial2", "trial-password")
            self.assertEqual([row["id"] for row in client.get("/api/v1/models", headers=headers).json()],
                             [self.model_a])
