"""HTTP tests for query progress polling in Studio mode. Temporary Data only."""

import importlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import UUID

SOURCE = Path(__file__).resolve().parent
DEMO = SOURCE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from studio_data import StudioData


SAMPLES = DEMO / "samples"
QUERY_HOLE = SAMPLES / "rehearsal" / "queries" / "SHOW-HOLE.step"
BASE_H01 = SAMPLES / "rehearsal" / "history" / "SHOW-H01.step"
WEB = SOURCE / "web"


@contextmanager
def dummy_pool():
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_request(url, method="GET", data=None, headers=None, timeout=5):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, raw, response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, raw, exc.headers


def start_app_server(data_root):
    os.environ["STEP_STUDIO_DATA"] = str(data_root)
    for name in ("app", "studio_data", "library_loader"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import app as app_module
    importlib.reload(app_module)
    patches = [patch.object(app_module, "worker_pool", dummy_pool)]
    started = []
    for item in patches:
        item.start()
        started.append(item)
    port = free_port()
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        app_module.app, host="127.0.0.1", port=port,
        log_config=None, access_log=False, lifespan="on",
    ))
    thread = Thread(target=server.run, name="query-http-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        for item in started:
            item.stop()
        raise RuntimeError("test server failed to start")
    return app_module, server, thread, started, f"http://127.0.0.1:{port}"


def stop_app_server(server, thread, patches):
    if server is not None:
        server.should_exit = True
    if thread is not None:
        thread.join(timeout=8)
    for item in patches or []:
        item.stop()


class QueryDirectoryTests(unittest.TestCase):
    def test_query_directory_uses_history_when_archived_else_pending(self):
        root = Path(tempfile.mkdtemp(prefix="query-dir-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        data = StudioData(root)
        data.initialize()
        pending = data.query_directory("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(pending, data.pending / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        archived = data.history / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        archived.mkdir()
        self.assertEqual(data.query_directory("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), archived)


class FrontendErrorMessageTests(unittest.TestCase):
    def test_script_does_not_parse_plain_internal_server_error_as_json_failure(self):
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("export function readApiErrorMessage(status, text)", script)
        self.assertIn("await response.text()", script)
        self.assertIn("JSON.parse(text)", script)
        self.assertIn("服务器返回错误（${status}），请重试。", script)
        self.assertNotIn("await response.json();", script.split("async function json")[1].split("function setStatus")[0])


class QueryHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="query-http-"))
        self.addCleanup(self._cleanup)
        self.data = StudioData(self.root)
        self.data.initialize()
        shutil.copy2(BASE_H01, self.data.base / "SHOW-H01.step")
        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(self.root)

    def _cleanup(self):
        stop_app_server(getattr(self, "server", None), getattr(self, "thread", None), getattr(self, "patches", []))
        shutil.rmtree(self.root, ignore_errors=True)

    def test_poll_returns_json_for_in_progress_and_complete_query(self):
        query_id = uuid.uuid4().hex
        directory = self.data.pending / query_id
        directory.mkdir()
        record = {
            "id": query_id,
            "state": "parsing",
            "progress": 8,
            "progress_text": "正在解析 STEP 并生成三维网格",
            "query": {"file_name": "SHOW-HOLE.step"},
        }
        (directory / "status.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        status, raw, headers = http_request(f"{self.base}/api/queries/{query_id}")
        self.assertEqual(status, 200, raw[:500])
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["state"], "parsing")
        self.assertEqual(body["query"]["file_name"], "SHOW-HOLE.step")
        ctype = headers.get("Content-Type", "")
        self.assertIn("json", ctype.lower())

        archived = self.data.history / query_id
        directory.rename(archived)
        (archived / "status.json").write_text(json.dumps({
            **record, "state": "complete", "progress": 100, "progress_text": "比对完成",
            "candidates": [], "review": [],
            "query": {**record["query"], "info": {"size_mm": [1, 1, 1], "area_mm2": 1, "volume_mm3": 1}},
        }, ensure_ascii=False), encoding="utf-8")
        status, raw, _headers = http_request(f"{self.base}/api/queries/{query_id}")
        self.assertEqual(status, 200, raw[:500])
        body = json.loads(raw.decode("utf-8"))
        self.assertEqual(body["state"], "complete")
        self.assertEqual(body["progress"], 100)

    def test_unhandled_query_error_returns_json_not_plain_internal_server_error(self):
        query_id = uuid.uuid4().hex
        with patch.object(self.app_module.STUDIO, "query_directory", side_effect=AttributeError("query_directory")):
            status, raw, headers = http_request(f"{self.base}/api/queries/{query_id}")
        self.assertEqual(status, 500)
        text = raw.decode("utf-8")
        self.assertNotEqual(text.strip()[:12], "Internal Ser")
        body = json.loads(text)
        self.assertEqual(body["detail"], "服务器处理请求失败，请重试。")
        self.assertNotIn("query_directory", text)
        self.assertFalse(re.search(r"[A-Za-z]:\\\\", text))

    def test_upload_show_hole_polls_until_complete_over_http(self):
        boundary = "----QueryTestBoundary"
        payload = QUERY_HOLE.read_bytes()
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="SHOW-HOLE.step"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
        status, raw, _headers = http_request(
            f"{self.base}/api/queries",
            method="POST",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=10,
        )
        self.assertIn(status, (200, 202), raw[:500])
        accepted = json.loads(raw.decode("utf-8"))
        self.assertIn("id", accepted)
        query_id = accepted["id"]
        UUID(query_id)
        done = None
        seen = []
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            poll_status, poll_raw, _ = http_request(f"{self.base}/api/queries/{query_id}", timeout=5)
            self.assertEqual(poll_status, 200, poll_raw[:500])
            snapshot = json.loads(poll_raw.decode("utf-8"))
            seen.append(snapshot.get("state"))
            if snapshot["state"] in {"complete", "failed"}:
                done = snapshot
                break
            time.sleep(0.2)
        self.assertIsNotNone(done, f"query did not finish, states={seen}")
        self.assertEqual(done["state"], "complete", done.get("reason"))
        self.assertEqual(done["progress"], 100)
        self.assertEqual(done["query"]["file_name"], "SHOW-HOLE.step")
        self.assertTrue(done.get("candidates") or done.get("review"))
        archived = self.data.history / (query_id if "-" not in query_id else UUID(query_id).hex)
        self.assertTrue((archived / "status.json").is_file())
        self.assertFalse((self.data.pending / archived.name).exists())


if __name__ == "__main__":
    unittest.main()
