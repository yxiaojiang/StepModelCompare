"""验证迁移和 PostgreSQL 抢占。依赖 SQLAlchemy、Alembic；真实行锁用单独 PostgreSQL 测试 schema。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.compare.rule import ComparisonRule
from app.compare.trial_job import run_trial_compare_job
from app.config import Settings
from app.db.model_features import (
    FeatureVersionConflict, active_feature_version, load_ready_features, missing_feature_models,
    switch_active_feature_version, vault_path,
)
from app.db.models import (
    Base, CompareJob, CompareResult, Enterprise, FeatureCatalog, InviteCode, Model, ModelFeature,
    SimilarityRule, TrialReservation, User,
)
from app.db.queue import claim_compare_job, claim_indexing_model
from app.db.session import build_engine
from app.geometry_engine.feature_indexing import build_model_features, rebuild_feature_version
from app.geometry_engine.step_input import ParseLimits
from app.geometry_engine.worker_runner import OcctPool
from app.enterprise import EnterpriseError, EnterpriseService
from app.repository.quota import (
    QuotaError, commit_trial_results, enqueue_trial_compare, reap_expired_trial_quota, release_trial_quota,
)
from app.repository.visibility import VisibilityUser

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_POSTGRES_TEST_URL = os.environ.get("STEP_PLATFORM_TEST_POSTGRES_URL")
_DEMO = Path(__file__).resolve().parents[3] / "Demo/samples/rehearsal/queries/SHOW-POSE-INCH.step"
_VERSION_FIXTURE = "1.1.0"
_TEST_VERSIONS = frozenset({"1.0.0", _VERSION_FIXTURE})


def _migration(engine, command_fn, *args):
    config = Config(str(_ALEMBIC_INI))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command_fn(config, *args)


def _seed_user(engine) -> tuple[int, int, int]:
    with engine.begin() as connection:
        enterprises = Enterprise.__table__
        enterprise, other = (
            connection.execute(enterprises.insert().values(
                name=code, code=code, status="ACTIVE", storage_quota_mb=50,
            ).returning(enterprises.c.id)).scalar_one()
            for code in ("first", "other")
        )
        users = User.__table__
        user = connection.execute(users.insert().values(
            username="selftest", password_hash="占位测试哈希", role="ENT_USER", enterprise_id=enterprise,
        ).returning(users.c.id)).scalar_one()
    return user, enterprise, other


def _seed_model(engine, user_id, enterprise_id, path="test/case.step"):
    models = Model.__table__
    with engine.begin() as connection:
        return connection.execute(models.insert().values(
            enterprise_id=enterprise_id, md5="a" * 32, model_name="case", original_filename="case.step",
            file_size_bytes=90, storage_path=path, uploader_id=user_id, status="INDEXING",
        ).returning(models.c.id)).scalar_one()


def _assert_core_schema(case, engine):
    inspector = inspect(engine)
    case.assertEqual(
        set(inspector.get_table_names()), set(Base.metadata.tables) | {"alembic_version"},
    )
    case.assertTrue(
        {"auth_version", "is_trial", "used_count", "in_flight_count"}
        <= {column["name"] for column in inspector.get_columns("users")},
    )
    case.assertIn(
        ["model_id", "feature_version"],
        [item["column_names"] for item in inspector.get_unique_constraints("model_features")],
    )
    case.assertIn(
        ["enterprise_id", "status"], [item["column_names"] for item in inspector.get_indexes("models")],
    )
    case.assertIn(
        ["enterprise_id", "md5"], [item["column_names"] for item in inspector.get_indexes("models")],
    )


def _assert_constraints(case, engine):
    user, enterprise, other = _seed_user(engine)
    model = _seed_model(engine, user, enterprise)
    with engine.connect() as connection:
        counters = connection.execute(select(User.auth_version, User.used_count, User.in_flight_count)).one()
        case.assertEqual(tuple(counters), (1, 0, 0))

    features = ModelFeature.__table__
    with engine.begin() as connection:
        for version in ("1.0.0", "2.0.0"):
            connection.execute(features.insert().values(
                model_id=model, feature_version=version, feature_payload={}, status="READY",
            ))
    with case.assertRaises(IntegrityError), engine.begin() as connection:
        connection.execute(features.insert().values(model_id=model, feature_version="1.0.0", status="FAILED"))
    with case.assertRaises(IntegrityError), engine.begin() as connection:
        connection.execute(features.insert().values(model_id=1_000_000, feature_version="1.0.0", status="FAILED"))

    rules = SimilarityRule.__table__
    rule = rules.insert().values(
        name="规则", version="1", weights_json={}, thresholds_json={}, missing_policy_json={},
        max_refine_k=5, updated_by=user, is_active=True,
    )
    with engine.begin() as connection:
        connection.execute(rule.values(scope="GLOBAL"))
        connection.execute(rule.values(scope="GLOBAL", is_active=False))
        connection.execute(rule.values(scope="ENTERPRISE", enterprise_id=enterprise))
        connection.execute(rule.values(scope="ENTERPRISE", enterprise_id=other))
    with case.assertRaises(IntegrityError), engine.begin() as connection:
        connection.execute(rule.values(scope="GLOBAL"))
    with case.assertRaises(IntegrityError), engine.begin() as connection:
        connection.execute(rule.values(scope="ENTERPRISE", enterprise_id=enterprise))
    with case.assertRaises(IntegrityError), engine.begin() as connection:
        connection.execute(rule.values(scope="ENTERPRISE"))


class _VersionFixturePool:
    """替代几何层，写出占位文件用于真实 PostgreSQL 事务测试。可在解析完成前人为挂起。"""

    def __init__(self, *, started=None, resume=None, reject_insert=False):
        self.started = started
        self.resume = resume
        self.reject_insert = reject_insert
        self.calls = []

    def extract_features(self, path, *, feature_version, mesh_target, glb_target, original_filename,
                         registered_material):
        """记录 path 的提取调用，向暂存路径写入夹具，返回目标版本数据；同步失败抛 TimeoutError。"""
        self.calls.append((path, feature_version, original_filename, registered_material))
        if self.started is not None:
            self.started.set()
            if not self.resume.wait(5):
                raise TimeoutError("等待测试释放提取阻塞超时")
        mesh_target.write_bytes(b"mesh fixture")
        glb_target.write_bytes(b"GLB fixture")
        result = {"feature_version": feature_version, "cache_refs": {}}
        if self.reject_insert:
            result["test_error"] = True
        return result


class SqliteMigrationTests(unittest.TestCase):
    """在临时文件中验证迁移和基础约束，SQLite 明确拒绝队列消费。"""

    def test_upgrade_downgrade_and_constraints(self):
        """验证首版升级、约束、回滚及再次升级，无外部数据库依赖。"""
        with TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory).as_posix()}/test.sqlite"
            engine = build_engine(Settings(database_url=url))
            try:
                _migration(engine, command.upgrade, "head")
                _migration(engine, command.check)
                _assert_core_schema(self, engine)
                _assert_constraints(self, engine)
                with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                    claim_compare_job(engine, 60)
                with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                    claim_indexing_model(engine)
                _migration(engine, command.downgrade, "base")
                self.assertEqual(set(inspect(engine).get_table_names()), {"alembic_version"})
                _migration(engine, command.upgrade, "head")
                _assert_core_schema(self, engine)
            finally:
                engine.dispose()


@unittest.skipUnless(_POSTGRES_TEST_URL, "未提供 PostgreSQL 测试连接，跳过真实并发测试")
class PostgresTests(unittest.TestCase):
    """对真实 PostgreSQL 重放迁移及竞争消费，测试后销毁各自的独立 schema。"""

    def setUp(self):
        """创建隔离 schema 并迁移，测试清理时释放连接并删除该 schema。"""
        base_url = make_url(_POSTGRES_TEST_URL)
        if base_url.drivername != "postgresql+psycopg":
            self.fail("STEP_PLATFORM_TEST_POSTGRES_URL 必须是 PostgreSQL psycopg 连接")
        server = build_engine(Settings(database_url=base_url.render_as_string(hide_password=False)))
        self.addCleanup(server.dispose)
        schema = f"step_plat_test_{uuid4().hex}"
        with server.begin() as conn:
            conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        self.addCleanup(self._drop_schema, server, schema)
        scoped_url = base_url.update_query_dict({"options": f"-csearch_path={schema} -cstatement_timeout=5000"})
        self._engine = build_engine(Settings(database_url=scoped_url.render_as_string(hide_password=False)))
        self.addCleanup(self._engine.dispose)
        _migration(self._engine, command.upgrade, "head")

    def _drop_schema(self, server, schema):
        with server.begin() as conn:
            conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")

    def test_trial_ten_concurrent_admissions_and_late_completion(self):
        """十路 PostgreSQL 并发只能建五个试用任务，解析失败释放，回收与迟到结果竞争不写结果。"""
        engine = self._engine
        user, enterprise, _ = _seed_user(engine)
        model = _seed_model(engine, user, enterprise)
        with engine.begin() as connection:
            connection.execute(update(User).where(User.id == user).values(
                role="TRIAL_USER", is_trial=True, enterprise_id=None,
            ))
            connection.execute(update(Model).where(Model.id == model).values(
                status="APPROVED", is_public_demo=True,
            ))
        actor = VisibilityUser(user, "TRIAL_USER", None, True)
        settings = Settings()
        rule = ComparisonRule(weights={f"D{index}": 100 if index == 5 else 0 for index in range(1, 8)})
        snapshot = {"rule": rule.model_dump(mode="json"), "feature_version": "1.0.0"}
        start = Barrier(10)

        def admit(index):
            start.wait(timeout=10)
            try:
                return enqueue_trial_compare(
                    engine, settings, actor, source="WEB", query_filename=f"q{index}.step",
                    query_md5=f"{index:032x}", rule_snapshot=snapshot, auth_version=1,
                )
            except QuotaError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=10) as workers:
            outcomes = list(workers.map(admit, range(10)))
        self.assertEqual(outcomes.count("TRIAL_QUOTA_EXCEEDED"), 5)
        jobs = [job for job in outcomes if isinstance(job, int)]
        self.assertEqual(len(set(jobs)), 5)
        first = claim_compare_job(engine, settings.queue_wait_timeout)
        self.assertIn(first, jobs)
        with TemporaryDirectory() as directory, OcctPool(settings.parse_limits(), workers=1) as pool:
            broken = Path(directory, "broken.step")
            broken.write_bytes(b"invalid header")
            self.assertFalse(run_trial_compare_job(settings, pool, engine, Path(directory), broken, first))
        self.assertFalse(release_trial_quota(engine, first, "INVALID_STEP_FILE"))
        second = claim_compare_job(engine, settings.queue_wait_timeout)
        self.assertTrue(commit_trial_results(engine, second, [{"model_id": model, "breakdown_json": {}}]))
        late = claim_compare_job(engine, settings.queue_wait_timeout)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=settings.reaper_ttl + 5)
        race = Barrier(2)

        def deliver():
            race.wait(timeout=5)
            return commit_trial_results(engine, late, [{"model_id": model, "breakdown_json": {}}], now=expiry)

        def reap():
            race.wait(timeout=5)
            return reap_expired_trial_quota(engine, expiry)

        with ThreadPoolExecutor(max_workers=2) as workers:
            late_result = workers.submit(deliver)
            reaped = workers.submit(reap)
            self.assertFalse(late_result.result(timeout=10))
            self.assertIn(reaped.result(timeout=10), (2, 3))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(select(CompareResult.job_id)).scalars().all(), [second])
            self.assertEqual(connection.execute(select(CompareJob.error_code)
                                                .where(CompareJob.id == first)).scalar_one(), "ERR_INVALID_STEP_HEADER")
            self.assertEqual(connection.execute(select(CompareJob.status, CompareJob.error_code)
                                                .where(CompareJob.id == late)).one(), ("FAILED", "QUOTA_EXPIRED"))
            self.assertEqual(connection.execute(select(User.used_count, User.in_flight_count)
                                                .where(User.id == user)).one(), (1, 0))
            self.assertCountEqual(connection.execute(select(TrialReservation.status)).scalars(),
                                  ["RELEASED", "COMMITTED", "EXPIRED", "EXPIRED", "EXPIRED"])

    def test_concurrent_invite_binding_consumes_only_winning_code(self):
        """同一试用账号同时绑定不同企业时，仅成功事务扣减邀请码并递增版本。"""
        engine = self._engine
        user, first, second = _seed_user(engine)
        with engine.begin() as connection:
            connection.execute(update(User).where(User.id == user).values(
                role="TRIAL_USER", is_trial=True, enterprise_id=None,
            ))
        service = EnterpriseService(engine)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        for code, enterprise in (("A", first), ("B", second)):
            service.create_invite_code(enterprise, code, 1, expiry, user)
        start = Barrier(2)

        def bind(code):
            start.wait(timeout=5)
            try:
                return service.bind_invite(user, code, auth_version=1)
            except EnterpriseError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(bind, ("A", "B")))
        winner, = [value for value in outcomes if value is not None]
        with engine.connect() as connection:
            self.assertEqual(connection.execute(select(User.enterprise_id, User.auth_version)).one(), (winner, 2))
            self.assertEqual(dict(connection.execute(select(InviteCode.enterprise_id, InviteCode.used_count)).all()),
                             {first: int(winner == first), second: int(winner == second)})

    def test_postgres_schema(self):
        """验证真实 JSONB、约束、ORM 一致性和 PostgreSQL 回滚重建。"""
        engine = self._engine
        _migration(engine, command.check)
        _assert_core_schema(self, engine)
        payload, = [
            col for col in inspect(engine).get_columns("model_features") if col["name"] == "feature_payload"
        ]
        self.assertIsInstance(payload["type"], JSONB)
        _assert_constraints(self, engine)
        _migration(engine, command.downgrade, "base")
        self.assertEqual(set(inspect(engine).get_table_names()), {"alembic_version"})
        _migration(engine, command.upgrade, "head")
        _assert_core_schema(self, engine)

    def test_compare_concurrent_claim(self):
        """实际持有首行锁，竞争线程领走其余任务且无重复，已超时任务不被领用。"""
        engine = self._engine
        user, _, _ = _seed_user(engine)
        jobs = CompareJob.__table__
        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            expired, first, *remaining = [
                conn.execute(jobs.insert().values(
                    user_id=user, source="WEB", query_filename=f"{index}.step", query_md5="f" * 32,
                    rule_version_snapshot={}, created_at=at,
                ).returning(jobs.c.id)).scalar_one()
                for index, at in enumerate([now - timedelta(minutes=5), *([now] * 5)])
            ]
        start = Barrier(8, timeout=5)

        def consume():
            start.wait()
            return claim_compare_job(engine, 60)

        with engine.begin() as locked, ThreadPoolExecutor(max_workers=8) as workers:
            locked.execute(select(CompareJob.id).where(CompareJob.id == first).with_for_update())
            futures = [workers.submit(consume) for _ in range(8)]
            claimed = [future.result(timeout=8) for future in futures]
            self.assertCountEqual([item for item in claimed if item is not None], remaining)
        self.assertEqual(claim_compare_job(engine, 60), first)
        self.assertIsNone(claim_compare_job(engine, 60))
        with engine.connect() as conn:
            states = {job_id: (status, started_at) for job_id, status, started_at in conn.execute(
                select(CompareJob.id, CompareJob.status, CompareJob.started_at),
            )}
        self.assertEqual(states[expired], ("QUEUED", None))
        self.assertTrue(all(states[job][0] == "RUNNING" and states[job][1] for job in [first, *remaining]))

    def test_indexing_claim_skips_locked_row(self):
        """入库队列跳过他人锁，不改变已抢占记录的五态状态。"""
        engine = self._engine
        user, enterprise, _ = _seed_user(engine)
        first, second = [_seed_model(engine, user, enterprise) for _ in range(2)]
        with engine.begin() as locked:
            locked.execute(select(Model.id).where(Model.id == first).with_for_update())
            self.assertEqual(claim_indexing_model(engine), second)
            self.assertIsNone(claim_indexing_model(engine))
        self.assertEqual(claim_indexing_model(engine), first)
        self.assertIsNone(claim_indexing_model(engine))
        with engine.connect() as conn:
            rows = conn.execute(select(Model.status, Model.indexing_started_at)).all()
        self.assertTrue(all(status == "INDEXING" and started_at for status, started_at in rows))


    def test_feature_migration_carries_existing_version_back_and_forth(self):
        """从已有全局规则的首版数据库升级并降级，保留已存的版本行与全局指针。"""
        engine = self._engine
        _migration(engine, command.downgrade, "0001")
        user, enterprise, _ = _seed_user(engine)
        model = _seed_model(engine, user, enterprise)
        with engine.begin() as connection:
            connection.execute(SimilarityRule.__table__.insert().values(
                scope="GLOBAL", name="现有规则", version="1", is_active=True, active_feature_version="1.3.0",
                weights_json={}, thresholds_json={}, missing_policy_json={}, max_refine_k=2, updated_by=user,
            ))
            connection.execute(ModelFeature.__table__.insert().values(
                model_id=model, feature_version="1.3.0", status="READY", feature_payload={},
            ))
        _migration(engine, command.upgrade, "head")
        with engine.begin() as connection:
            self.assertEqual(active_feature_version(connection), "1.3.0")
            connection.execute(update(FeatureCatalog).values(active_feature_version="1.4.0"))
        _migration(engine, command.downgrade, "0001")
        with engine.connect() as connection:
            self.assertEqual(connection.execute(select(SimilarityRule.active_feature_version)).scalar_one(), "1.4.0")
            self.assertEqual(connection.execute(select(ModelFeature.feature_version)).scalar_one(), "1.3.0")

    def test_feature_admission_and_repair(self):
        """真实 OCCT 加真实 PostgreSQL 验证入库幂等、失败隔离、缓存修复及同件跨企分离。"""
        user, first_enterprise, second_enterprise = _seed_user(self._engine)
        first = _seed_model(self._engine, user, first_enterprise)
        other = _seed_model(self._engine, user, second_enterprise)
        bad = _seed_model(self._engine, user, first_enterprise, "test/broken.step")
        with TemporaryDirectory() as directory, OcctPool(ParseLimits(), workers=1) as pool:
            vault = Path(directory)
            (vault / "test").mkdir()
            (vault / "test/case.step").write_bytes(_DEMO.read_bytes())
            (vault / "test/broken.step").write_bytes(b"bad")
            for model_id in (first, first, other):
                result = build_model_features(self._engine, vault, pool, model_id, registered_material="SUS304")
                self.assertEqual((result.feature_version, result.status), ("1.0.0", "READY"))
            result = build_model_features(self._engine, vault, pool, bad)
            self.assertEqual((result.status, result.error_code), ("FAILED", "ERR_INVALID_STEP_HEADER"))
            with self._engine.connect() as connection:
                version = active_feature_version(connection)
                rows = load_ready_features(connection, [first, other, bad], version)
                self.assertEqual([row.model_id for row in rows], [first, other])
                self.assertEqual(rows[0].feature_payload["d1_basic"]["materials"], ["SUS304"])
                self.assertEqual(rows[0].feature_payload["cache_refs"]["glb_path"], rows[0].glb_path)
                self.assertTrue(rows[0].glb_path.startswith(f"cache/enterprise-{first_enterprise}/{first}/1.0.0/"))
                self.assertTrue(rows[1].glb_path.startswith(f"cache/enterprise-{second_enterprise}/{other}/1.0.0/"))
                states = dict(connection.execute(select(Model.id, Model.status)).tuples().all())
                self.assertEqual(states, {first: "APPROVED", other: "APPROVED", bad: "PARSE_FAILED"})
            self.assertEqual(len(list(vault.rglob("preview.glb"))), 2)
            old_key = rows[0].glb_path
            vault_path(vault, old_key).unlink()
            with self._engine.connect() as connection:
                self.assertEqual(missing_feature_models(connection, vault, "1.0.0"), [first])
            with self.assertRaisesRegex(FeatureVersionConflict, "缓存"):
                switch_active_feature_version(self._engine, vault, "1.0.0", "1.0.0")
            self.assertEqual(build_model_features(self._engine, vault, pool, first).status, "READY")
            with self._engine.connect() as connection:
                self.assertEqual(missing_feature_models(connection, vault, "1.0.0"), [])
                fixed, = load_ready_features(connection, [first], "1.0.0")
                self.assertNotEqual(fixed.glb_path, old_key)
                self.assertEqual(fixed.feature_payload["d1_basic"]["materials"], ["SUS304"])

    def test_feature_transaction_failure_removes_unreferenced_attempt(self):
        """在目录发布后注入数据库约束拒绝，验证数据库回滚、产物清理和重试。"""
        user, enterprise, _ = _seed_user(self._engine)
        model = _seed_model(self._engine, user, enterprise)
        with self._engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE model_features ADD CONSTRAINT test_feature_payload "
                "CHECK ((feature_payload ->> 'test_error') IS NULL)"
            )
        with TemporaryDirectory() as directory:
            vault = Path(directory)
            with self.assertRaises(IntegrityError):
                build_model_features(self._engine, vault, _VersionFixturePool(reject_insert=True), model)
            self.assertEqual(list(vault.rglob("*.glb")), [])
            self.assertEqual(list(vault.rglob(".staging-*")), [])
            with self._engine.connect() as connection:
                self.assertEqual(connection.execute(select(Model.status)).scalar_one(), "INDEXING")
                self.assertEqual(connection.execute(select(ModelFeature.id)).all(), [])
            self.assertEqual(build_model_features(self._engine, vault, _VersionFixturePool(), model).status, "READY")

    def test_version_switch_before_inflight_admission(self):
        """旧版提取在切换后返回时重新入队，且旧快照及现行版本均可精确查询。"""
        user, enterprise, _ = _seed_user(self._engine)
        published, inflight = [_seed_model(self._engine, user, enterprise) for _ in range(2)]
        started, resume = Event(), Event()
        with (
            TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as workers,
            patch("app.geometry_engine.feature_versions.SUPPORTED_FEATURE_VERSIONS", _TEST_VERSIONS),
        ):
            vault = Path(directory)
            pool = _VersionFixturePool()
            self.assertEqual(build_model_features(self._engine, vault, pool, published).status, "READY")
            self.assertEqual(claim_indexing_model(self._engine), inflight)
            paused = _VersionFixturePool(started=started, resume=resume)
            remaining = workers.submit(build_model_features, self._engine, vault, paused, inflight)
            try:
                self.assertTrue(started.wait(5))
                rebuild_feature_version(self._engine, vault, pool, expected="1.0.0", target=_VERSION_FIXTURE)
            finally:
                resume.set()
            self.assertEqual(remaining.result(timeout=5).status, "RETRY")
            self.assertEqual(claim_indexing_model(self._engine), inflight)
            self.assertEqual(build_model_features(self._engine, vault, pool, inflight).feature_version, _VERSION_FIXTURE)
            with self._engine.connect() as connection:
                self.assertEqual(active_feature_version(connection), _VERSION_FIXTURE)
                old, = load_ready_features(connection, [published, inflight], "1.0.0")
                current = load_ready_features(connection, [published, inflight], _VERSION_FIXTURE)
                self.assertEqual([row.model_id for row in current], [published, inflight])
                self.assertNotEqual(old.glb_path, current[0].glb_path)
                self.assertTrue(vault_path(vault, old.glb_path).is_file())

    def test_admission_commits_before_waiting_switch(self):
        """另一连接先完成旧版入库，等待的切换方必须在锁释放后发现该模型的缺口。"""
        user, enterprise, _ = _seed_user(self._engine)
        model = _seed_model(self._engine, user, enterprise)
        admitted, resume, switch_waiting = Event(), Event(), Event()

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE models SET status"):
                admitted.set()
                if not resume.wait(5):
                    raise TimeoutError("测试未释放入库事务")

        def observe_switch(conn, cursor, statement, parameters, context, executemany):
            if "FROM feature_catalog" in statement and "FOR UPDATE" in statement:
                switch_waiting.set()

        event.listen(self._engine, "after_cursor_execute", observe)
        event.listen(self._engine, "before_cursor_execute", observe_switch)
        self.addCleanup(event.remove, self._engine, "after_cursor_execute", observe)
        self.addCleanup(event.remove, self._engine, "before_cursor_execute", observe_switch)
        with (
            TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=2) as workers,
            patch("app.geometry_engine.feature_versions.SUPPORTED_FEATURE_VERSIONS", _TEST_VERSIONS),
        ):
            vault = Path(directory)
            pool = _VersionFixturePool()
            building = workers.submit(build_model_features, self._engine, vault, pool, model)
            try:
                self.assertTrue(admitted.wait(5))
                switching = workers.submit(switch_active_feature_version, self._engine, vault, "1.0.0", _VERSION_FIXTURE)
                self.assertTrue(switch_waiting.wait(5))
                self.assertFalse(switching.done())
            finally:
                resume.set()
            self.assertEqual(building.result(timeout=5).status, "READY")
            with self.assertRaisesRegex(FeatureVersionConflict, "未就绪"):
                switching.result(timeout=5)
            with self._engine.connect() as connection:
                self.assertEqual(active_feature_version(connection), "1.0.0")
                self.assertEqual(missing_feature_models(connection, vault, _VERSION_FIXTURE), [model])

    def test_rebuild_catches_models_approved_after_scan_starts(self):
        """完成第一次扫描期间新增的入库，第二次查漏必须在切换前补齐其目标版本。"""
        user, enterprise, _ = _seed_user(self._engine)
        existing = _seed_model(self._engine, user, enterprise)
        started, resume = Event(), Event()
        with (
            TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as workers,
            patch("app.geometry_engine.feature_versions.SUPPORTED_FEATURE_VERSIONS", _TEST_VERSIONS),
        ):
            vault = Path(directory)
            pool = _VersionFixturePool()
            self.assertEqual(build_model_features(self._engine, vault, pool, existing).status, "READY")
            paused = _VersionFixturePool(started=started, resume=resume)
            rebuild = workers.submit(
                rebuild_feature_version, self._engine, vault, paused,
                expected="1.0.0", target=_VERSION_FIXTURE, batch_size=1,
            )
            try:
                self.assertTrue(started.wait(5))
                added = _seed_model(self._engine, user, enterprise)
                self.assertEqual(build_model_features(self._engine, vault, pool, added).feature_version, "1.0.0")
            finally:
                resume.set()
            rebuild.result(timeout=5)
            with self._engine.connect() as connection:
                self.assertEqual(active_feature_version(connection), _VERSION_FIXTURE)
                self.assertEqual([row.model_id for row in load_ready_features(
                    connection, [existing, added], _VERSION_FIXTURE,
                )], [existing, added])
            self.assertEqual(len(paused.calls), 2)
