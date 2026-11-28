"""复测阶段 3 上传、审核、规则、对比 API 和常驻调度。真实 PostgreSQL 用独立测试 schema 与 Demo 夹具。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url

from app.auth.service import AuthService
from app.config import Settings
from app.db.models import AuditLog, CompareJob, CompareResult, FeatureCatalog, Model, TrialReservation, User
from app.db.session import build_engine
from app.enterprise import EnterpriseService
from app.main import create_app

_PG = os.environ.get("STEP_PLATFORM_TEST_POSTGRES_URL")
_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_DEMO = Path(__file__).resolve().parents[3] / "Demo/samples/rehearsal/queries/SHOW-POSE-INCH.step"


@unittest.skipUnless(_PG, "未配置 PostgreSQL 测试地址")
class WorkflowTests(unittest.TestCase):
    """对 PostgreSQL 与 ASGI 生命周期运行端到端验证，每例隔离文件和数据库对象。"""

    def setUp(self):
        """建立迁移完毕的独立测试库作用域与四种账号，资源随用例关闭。"""
        base = make_url(_PG)
        self.assertEqual(base.drivername, "postgresql+psycopg")
        server = build_engine(Settings(database_url=_PG))
        self.addCleanup(server.dispose)
        schema = f"workflow_{uuid4().hex}"
        with server.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        self.addCleanup(self._drop, server, schema)
        url = base.update_query_dict({"options": f"-csearch_path={schema} -cstatement_timeout=5000"})
        self.url = url.render_as_string(hide_password=False)
        self.engine = build_engine(Settings(database_url=self.url))
        self.addCleanup(self.engine.dispose)
        migration = Config(str(_INI))
        with self.engine.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
        self.vault = Path(self.enterContext(TemporaryDirectory()))
        auth = AuthService(self.engine, "p" * 32)
        admin = auth.register("admin", "test-admin-password")
        auth.set_role_or_enterprise(admin.user_id, role="SYS_ADMIN", enterprise_id=None)
        self.admin = ("admin", "test-admin-password")
        self.teams = [
            EnterpriseService(self.engine).create_enterprise(f"企业 {index}", f"test-{index}") for index in range(2)
        ]
        self.members = []
        for index, team in enumerate(self.teams):
            name = f"user-{index}"
            identity = auth.register(name, "test-user-password")
            auth.set_role_or_enterprise(identity.user_id, role="ENT_USER", enterprise_id=team)
            self.members.append((name, "test-user-password"))
        self.trial = ("trial", "test-trial-password")
        self.trial_id = auth.register(*self.trial).user_id
        self.env = {
            "STEP_PLATFORM_DATABASE_URL": self.url, "STEP_PLATFORM_MODEL_VAULT": str(self.vault),
            "STEP_PLATFORM_AUTH_PEPPER": "p" * 32, "STEP_PLATFORM_JWT_SECRET": "s" * 32,
            "STEP_PLATFORM_QUEUE_POLL_SECONDS": "0.05", "STEP_PLATFORM_REAPER_INTERVAL_SECONDS": "0.1",
            "STEP_PLATFORM_AUTH_RATE_LIMIT_MAX_ATTEMPTS": "60", "STEP_PLATFORM_RUN_QUEUE_WORKERS": "true",
            "STEP_PLATFORM_UPLOAD_MAX_ATTEMPTS": "60",
        }

    @staticmethod
    def _drop(server, schema):
        with server.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")

    def _rule(self, client, name, dimension, enterprise=None):
        response = client.post("/api/v1/admin/rules", auth=self.admin, json={
            "name": name, "enterprise_id": enterprise,
            "rule": {"weights": {f"D{index}": 100 if index == dimension else 0 for index in range(1, 8)}},
        })
        self.assertEqual(response.status_code, 201, response.text)
        rule = response.json()
        active = client.post(f"/api/v1/admin/rules/{rule['rule_id']}/activate", auth=self.admin)
        self.assertEqual(active.status_code, 200, active.text)
        return rule

    def _ingest(self, client, payload, *, name="part.step", user=0, status=202):
        response = client.post("/api/v1/client/models/batch-upload", auth=self.members[user],
                               files={"files[]": (name, payload)}, params={"enterprise_id": self.teams[1 - user]})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def _model_state(self, model, target):
        until = monotonic() + 25
        seen = None
        while monotonic() < until:
            with self.engine.connect() as connection:
                seen = connection.execute(select(Model.status).where(Model.id == model)).scalar_one()
            if seen == target:
                return
            if seen in {"REJECTED", "PARSE_FAILED", "APPROVED"}:
                break
            sleep(0.05)
        self.fail(f"模型没有进入 {target}，最终观察到 {seen}")

    def _poll(self, client, url, actor):
        until = monotonic() + 25
        while monotonic() < until:
            response = client.get(url, auth=actor)
            if response.status_code != 202:
                return response
            sleep(0.05)
        self.fail("轮询超时")

    def test_five_state_and_hybrid_workflow(self):
        """真实 Demo 原件经两层 Worker 入库与对比，覆盖五态、租户隔离、规则快照和 200/202。"""
        payload = _DEMO.read_bytes()
        with patch.dict(os.environ, self.env), TestClient(create_app()) as client:
            self.assertEqual(client.post("/api/v1/client/models/batch-upload", auth=self.trial,
                                         files={"files[]": ("part.step", payload)}).status_code, 403)
            rejected = self._ingest(client, payload)["accepted"][0]["model_id"]
            self.assertEqual(client.get(f"/api/v1/models/{rejected}", auth=self.members[0]).status_code, 404)
            response = client.post(f"/api/v1/admin/models/{rejected}/reject", auth=self.admin, json={"reason": "重新提交"})
            self.assertEqual(response.json()["status"], "REJECTED", response.text)
            first_row = self._ingest(client, payload)["accepted"][0]
            self.assertEqual(first_row["previous_status"], "REJECTED")
            first = first_row["model_id"]
            self.assertEqual(self._ingest(client, payload)["skipped_duplicates"][0]["existing_model_id"], first)
            other = self._ingest(client, payload, user=1)["accepted"][0]["model_id"]
            self.assertNotEqual(first, other)
            broken = self._ingest(client, b"ISO-10303-21;\ninvalid", name="bad.step")["accepted"][0]["model_id"]
            for model in (first, other, broken):
                response = client.post(f"/api/v1/admin/models/{model}/approve", auth=self.admin,
                                       json={"public_demo": model == first})
                self.assertEqual(response.json()["status"], "INDEXING", response.text)
            self._model_state(first, "APPROVED")
            self._model_state(other, "APPROVED")
            self._model_state(broken, "PARSE_FAILED")
            self.assertEqual(client.get(f"/api/v1/models/{broken}", auth=self.members[0]).status_code, 404)
            self.assertEqual(client.get(f"/api/v1/models/{other}/glb", auth=self.members[0]).status_code, 404)
            feature = client.get(f"/api/v1/models/{first}", auth=self.members[0]).json()["feature_payload"]
            self.assertEqual(feature["feature_version"], "1.0.0")
            self.assertNotIn("cache_refs", feature)

            global_rule = self._rule(client, "全局", 1)
            team_rule = self._rule(client, "企业 A", 2, self.teams[0])
            self.assertEqual(client.get("/api/v1/rules/active", auth=self.trial).json()["version"], global_rule["version"])
            self.assertEqual(client.get("/api/v1/rules/active", auth=self.members[0]).json()["version"], team_rule["version"])
            complete = client.post("/api/v1/client/compare", auth=self.members[0],
                                   files={"file": ("part.step", payload)}, params={"enterprise_id": self.teams[1]})
            self.assertEqual(complete.status_code, 200, complete.text)
            self.assertEqual(complete.json()["rule_version"], team_rule["version"])
            self.assertCountEqual([row["model_id"] for row in
                                   complete.json()["candidates"] + complete.json()["needs_review_items"]], [first])
            chosen = f"/api/v1/client/compare/{complete.json()['job_id']}/select"
            self.assertEqual(client.post(chosen, auth=self.members[0], json={"model_id": first}).json(), {"selected": True})
            self.assertEqual(client.post(chosen, auth=self.members[0], json={"model_id": first}).json(), {"selected": False})

            client.app.state.settings.sync_wait_timeout = 0.001
            queued = client.post("/api/v1/client/compare", data={"username": self.trial[0], "password": self.trial[1]},
                                 files={"file": ("part.step", payload)})
            self.assertEqual(queued.status_code, 202, queued.text)
            path = queued.headers["Location"]
            self.assertEqual(client.get(path, auth=self.members[0]).status_code, 404)
            self._rule(client, "新全局", 4)
            done = self._poll(client, path, self.trial)
            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual(done.json()["rule_version"], global_rule["version"])
            self.assertEqual(done.json()["trial_remaining"], 4)
            self.assertCountEqual([row["model_id"] for row in
                                   done.json()["candidates"] + done.json()["needs_review_items"]], [first])
            client.app.state.settings.sync_wait_timeout = 25
            damaged = client.post("/api/v1/compare/upload", auth=self.trial, data={"mode": "hybrid"},
                                  files={"file": ("broken.step", b"invalid")})
            self.assertEqual(damaged.status_code, 422, damaged.text)
            self.assertEqual(damaged.json()["error_code"], "INVALID_STEP_FILE")
            damaged_preview = f"/api/v1/compare/jobs/{damaged.json()['job_id']}/query/glb"
            self.assertEqual(client.get(damaged_preview, auth=self.trial).status_code, 404)
            web = client.post("/api/v1/compare/upload", auth=self.members[1], files={"file": ("part.step", payload)})
            self.assertEqual(web.status_code, 202, web.text)
            self.assertEqual(self._poll(client, web.headers["Location"], self.members[1]).status_code, 200)
            with self.engine.begin() as connection:
                connection.execute(update(Model).where(Model.id == first).values(is_public_demo=False))
            revoked = client.get(path, auth=self.trial).json()
            self.assertEqual((revoked["candidates"], revoked["needs_review_items"]), ([], []))
            with self.engine.connect() as connection:
                self.assertEqual(connection.execute(select(User.used_count, User.in_flight_count)
                                                    .where(User.id == self.trial_id)).one(), (1, 0))
                self.assertEqual(connection.execute(select(Model.selected_count).where(Model.id == first)).scalar_one(), 1)
                self.assertIn("MODEL_REJECTED", connection.execute(select(AuditLog.action)).scalars().all())

    def test_concurrent_upload_and_mixed_queue_capacity_and_reaping(self):
        """并发重复件只建一条台账，企业与试用并发共用队列容量，启动定时器后释放超时预占。"""
        payload = _DEMO.read_bytes()
        off = dict(self.env, STEP_PLATFORM_RUN_QUEUE_WORKERS="false", STEP_PLATFORM_MAX_QUEUE_DEPTH="2")
        with patch.dict(os.environ, off), TestClient(create_app()) as client:
            self._rule(client, "全局", 1)
            upload_gate = Barrier(2)

            def upload(_):
                upload_gate.wait(timeout=5)
                return self._ingest(client, payload)

            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(upload, range(2)))
            self.assertEqual(sum(len(row["accepted"]) for row in outcomes), 1)
            self.assertEqual(sum(len(row["skipped_duplicates"]) for row in outcomes), 1)
            compare_gate = Barrier(3)

            def compare(actor):
                compare_gate.wait(timeout=5)
                return client.post("/api/v1/compare/upload", auth=actor, files={"file": ("part.step", payload)})

            with ThreadPoolExecutor(max_workers=3) as workers:
                responses = list(workers.map(compare, (self.trial, self.trial, self.members[0])))
            self.assertEqual(sorted(response.status_code for response in responses), [202, 202, 503])
            self.assertTrue(any(response.json().get("error_code") == "QUEUE_FULL" for response in responses))
            with self.engine.begin() as connection:
                trial_pending = connection.execute(select(TrialReservation.job_id)).scalars().all()
                self.assertGreaterEqual(len(trial_pending), 1)
                connection.execute(update(CompareJob).values(created_at=func.now() - timedelta(minutes=5)))
                connection.execute(update(TrialReservation).values(expires_at=func.now() - timedelta(seconds=2)))
                abandoned = connection.execute(update(Model).values(
                    status="INDEXING", indexing_started_at=func.now() - timedelta(minutes=5),
                ).returning(Model.id)).scalar_one()
        with patch.dict(os.environ, self.env), TestClient(create_app()) as client:
            for job in trial_pending:
                result = self._poll(client, f"/api/v1/compare/jobs/{job}", self.trial)
                self.assertEqual((result.status_code, result.json()["error_code"]), (409, "QUOTA_EXPIRED"))
            self._model_state(abandoned, "PARSE_FAILED")
            with self.engine.connect() as connection:
                self.assertEqual(connection.execute(select(User.used_count, User.in_flight_count)
                                                    .where(User.id == self.trial_id)).one(), (0, 0))
                self.assertEqual(connection.execute(select(CompareResult.id)).all(), [])

    def test_ten_http_submissions_share_one_trial_quota(self):
        """十路 HTTP 并发同时跨 Web/客户端入口提交，数据库只允许五次预占。"""
        overrides = dict(self.env, STEP_PLATFORM_RUN_QUEUE_WORKERS="false")
        with patch.dict(os.environ, overrides), TestClient(create_app()) as client:
            self._rule(client, "全局", 1)
            self.assertEqual(client.get("/api/v1/rules/active", auth=self.trial).status_code, 200)
            gate = Barrier(10)

            def submit(index):
                path = "/api/v1/compare/upload" if index % 2 else "/api/v1/client/compare"
                gate.wait(timeout=5)
                return client.post(path, auth=self.trial, data={"mode": "async"},
                                   files={"file": ("part.step", b"ISO-10303-21;")})

            with ThreadPoolExecutor(max_workers=10) as workers:
                responses = list(workers.map(submit, range(10)))
            self.assertEqual(sorted(response.status_code for response in responses), [202] * 5 + [403] * 5)
            self.assertTrue(all(response.json()["error_code"] == "TRIAL_QUOTA_EXCEEDED"
                                for response in responses if response.status_code == 403))
            with self.engine.connect() as connection:
                self.assertEqual(connection.execute(select(User.used_count, User.in_flight_count)
                                                    .where(User.id == self.trial_id)).one(), (0, 5))

    def test_web_previews_follow_job_owner_visibility_and_feature_snapshot(self):
        """真实 GLB 使用任务冻结版本，其他账号、客户端任务、非结果和撤回候选均不可读取。"""
        payload = _DEMO.read_bytes()
        with patch.dict(os.environ, self.env), TestClient(create_app()) as client:
            visible = self._ingest(client, payload)["accepted"][0]["model_id"]
            private = self._ingest(client, payload, user=1)["accepted"][0]["model_id"]
            for model in (visible, private):
                approved = client.post(f"/api/v1/admin/models/{model}/approve", auth=self.admin,
                                       json={"public_demo": model == visible})
                self.assertEqual(approved.status_code, 200, approved.text)
                self._model_state(model, "APPROVED")
            self._rule(client, "预览回归", 5)

            submitted = client.post("/api/v1/compare/upload", auth=self.trial, files={"file": ("part.step", payload)})
            self.assertEqual(submitted.status_code, 202, submitted.text)
            path = submitted.headers["Location"]
            result = self._poll(client, path, self.trial)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["feature_version"], "1.0.0")
            query_path = f"{path}/query/glb"
            candidate_path = f"{path}/models/{visible}/glb"
            for preview in (query_path, candidate_path):
                loaded = client.get(preview, auth=self.trial)
                self.assertEqual((loaded.status_code, loaded.content[:4]), (200, b"glTF"))
                self.assertEqual(loaded.headers["content-type"], "model/gltf-binary")
                self.assertEqual(loaded.headers["cache-control"], "private, no-store")
                for other in (self.members[0], self.members[1], self.admin):
                    self.assertEqual(client.get(preview, auth=other).status_code, 404)
                self.assertEqual(client.get(preview).status_code, 401)
            self.assertEqual(client.get(f"{path}/models/{private}/glb", auth=self.trial).status_code, 404)
            part = client.get(query_path, auth=self.trial, headers={"Range": "bytes=0-3"})
            self.assertEqual((part.status_code, part.content), (206, b"glTF"))
            self.assertEqual(part.headers["cache-control"], "private, no-store")

            client_job = client.post("/api/v1/client/compare", auth=self.members[0],
                                     files={"file": ("part.step", payload)}, data={"mode": "async"})
            self.assertEqual(client_job.status_code, 202, client_job.text)
            self.assertEqual(self._poll(client, client_job.headers["Location"], self.members[0]).status_code, 200)
            client_id = client_job.json()["job_id"]
            self.assertEqual(client.get(f"/api/v1/compare/jobs/{client_id}/query/glb", auth=self.members[0]).status_code, 404)

            with self.engine.begin() as connection:
                # 模拟特征指针切换，工作台必须继续读取冻结的旧版本。
                connection.execute(update(FeatureCatalog).values(active_feature_version="9.9.9"))
            self.assertEqual(client.get(candidate_path, auth=self.trial).status_code, 200)
            chosen = client.post(f"{path}/select", auth=self.trial, json={"model_id": str(visible)})
            self.assertEqual(chosen.json(), {"selected": True})
            with self.engine.begin() as connection:
                connection.execute(update(Model).where(Model.id == visible).values(is_public_demo=False))
            self.assertEqual(client.get(candidate_path, auth=self.trial).status_code, 404)
            self.assertEqual(client.get(query_path, auth=self.trial).status_code, 200)

    def test_invite_limits_and_int64_cursors(self):
        """P2 回归覆盖邀请码独立限流与过大分页游标的规范 422。"""
        overrides = dict(self.env, STEP_PLATFORM_INVITE_BIND_MAX_ATTEMPTS="2", STEP_PLATFORM_RUN_QUEUE_WORKERS="false")
        with patch.dict(os.environ, overrides), TestClient(create_app()) as client:
            for path in ("/admin/enterprises", "/admin/invite-codes", "/models", "/admin/models/pending", "/admin/rules"):
                response = client.get("/api/v1" + path, auth=self.admin, params={"after_id": 2**63})
                self.assertEqual((response.status_code, response.json()), (422, {"error_code": "INVALID_REQUEST"}))
            for status in (400, 400, 429):
                response = client.post("/api/v1/auth/bind-invite", auth=self.trial, json={"code": "invalid"})
                self.assertEqual(response.status_code, status, response.text)

    def test_direct_upload_and_chunked_body_cap(self):
        """系统管理员直传可入队，超限的分块上传在写台账前返回 413。"""
        overrides = dict(self.env, STEP_PLATFORM_RUN_QUEUE_WORKERS="false")
        with patch.dict(os.environ, overrides), TestClient(create_app()) as client:
            parameters = {"enterprise_id": self.teams[0]}
            payload = _DEMO.read_bytes()
            response = client.post("/api/v1/models/batch-upload", auth=self.members[0], params=parameters,
                                   files={"files[]": ("part.step", payload)})
            self.assertEqual(response.status_code, 403)
            response = client.post("/api/v1/models/batch-upload", auth=self.admin, params=parameters,
                                   files={"files[]": ("part.step", payload)})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()["accepted"][0]["status"], "INDEXING")
            client.app.state.settings.max_upload_request_bytes = 300
            chunks = [b'--test\r\nContent-Disposition: form-data; name="files[]"; filename="big.step"\r\n\r\n',
                      b"x" * 512, b"\r\n--test--\r\n"]
            response = client.post("/api/v1/models/batch-upload", auth=self.admin, params=parameters,
                                   headers={"content-type": "multipart/form-data; boundary=test"}, content=iter(chunks))
            self.assertEqual((response.status_code, response.json()), (413, {"error_code": "UPLOAD_TOO_LARGE"}))
