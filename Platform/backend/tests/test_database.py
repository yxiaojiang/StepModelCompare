"""验证迁移和 PostgreSQL 抢占。依赖 SQLAlchemy、Alembic；真实行锁用单独 PostgreSQL 测试 schema。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import Base, CompareJob, Enterprise, Model, ModelFeature, SimilarityRule, User
from app.db.queue import claim_compare_job, claim_indexing_model
from app.db.session import build_engine

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_POSTGRES_TEST_URL = os.environ.get("STEP_PLATFORM_TEST_POSTGRES_URL")


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


def _seed_model(engine, user_id, enterprise_id):
    models = Model.__table__
    with engine.begin() as connection:
        return connection.execute(models.insert().values(
            enterprise_id=enterprise_id, md5="a" * 32, model_name="case", original_filename="case.step",
            file_size_bytes=90, storage_path="test/case.step", uploader_id=user_id, status="INDEXING",
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
