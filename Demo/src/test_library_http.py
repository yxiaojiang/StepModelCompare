"""HTTP and frontend-contract tests for library load reuse. Temporary Data only."""

import importlib
import json
import os
import socket
import sys
import tempfile
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from test_library_progress import fake_prepared, write_file


WEB = SOURCE / "web"


@contextmanager
def dummy_pool():
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(url, method="GET", timeout=3):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
        return response.status, body


def should_show_library_load_progress(data):
    return data["state"] in {"waiting", "scanning", "indexing", "failed"}


class FrontendDisplayConditionTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.css = (WEB / "style.css").read_text(encoding="utf-8")
        self.script = (WEB / "app.js").read_text(encoding="utf-8")

    def test_load_dialog_mirrors_comparison_structure_without_extra_status(self):
        self.assertIn('id="library-load-progress"', self.html)
        self.assertIn('id="library-load-title"', self.html)
        self.assertIn("正在加载基础模型库", self.html)
        self.assertIn('id="library-load-percent"', self.html)
        self.assertIn('id="library-load-file"', self.html)
        self.assertIn('id="library-load-meter"', self.html)
        self.assertNotIn('id="library-progress"', self.html)
        self.assertNotIn("library-progress-count", self.html)
        self.assertNotIn("library-progress-stage", self.html)
        self.assertIn('id="comparison-progress"', self.html)
        self.assertIn("#comparison-progress,\n#library-load-progress", self.css)
        self.assertIn("#progress-percent,\n#library-load-percent", self.css)
        self.assertIn("#progress-file,\n#library-load-file", self.css)
        self.assertIn("#progress-meter,\n#library-load-meter", self.css)

    def test_script_shows_progress_only_when_refresh_is_running_or_failed(self):
        self.assertIn("export function shouldShowLibraryLoadProgress(data)", self.script)
        self.assertIn("if (data.state === 'complete')", self.script)
        self.assertIn("openLibraryTable(data)", self.script)
        self.assertIn("shouldShowLibraryLoadProgress(data)", self.script)
        self.assertIn("libraryLoadDialog.showModal()", self.script)
        click = self.script.split("$('#open-library').addEventListener('click'")[1]
        click = click.split("$('#open-library-folder')")[0]
        self.assertIn("/api/library/refresh", click)
        self.assertNotIn("resetLibraryDialog()", click)
        self.assertLess(click.find("json('/api/library/refresh'"), click.find("showLibraryLoadProgress"))
        self.assertNotIn("dialog.showModal()", click)
        cases = [
            ({"state": "complete", "reused": True}, False),
            ({"state": "complete", "reused": False}, False),
            ({"state": "waiting"}, True),
            ({"state": "scanning"}, True),
            ({"state": "indexing"}, True),
            ({"state": "failed"}, True),
        ]
        for data, expected in cases:
            self.assertEqual(should_show_library_load_progress(data), expected, data)
            self.assertEqual(
                should_show_library_load_progress(data),
                ("waiting" in data["state"] or "scanning" in data["state"]
                 or "indexing" in data["state"] or data["state"] == "failed"),
            )


def start_app_server(data_root, prepared, extra_patches=()):
    os.environ["STEP_STUDIO_DATA"] = str(data_root)
    for name in ("app", "library_loader", "studio_data"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import app as app_module
    importlib.reload(app_module)
    patches = [
        patch.object(app_module, "worker_pool", dummy_pool),
        patch("studio_data.prepare_model", side_effect=prepared),
        *extra_patches,
    ]
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
    thread = Thread(target=server.run, name="library-http-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
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
        thread.join(timeout=5)
    for item in patches:
        item.stop()


class LibraryHttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="library-http-"))
        self.addCleanup(self._cleanup)
        for name in ("one.step", "two.step", "three.step"):
            write_file(self.root / "Base" / name)
        self.block = Event()
        self.started = Event()
        self.calls = []

        def prepared(path):
            self.calls.append(path.name)
            self.started.set()
            self.assertTrue(self.block.wait(timeout=5))
            return fake_prepared(path)

        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, prepared
        )

    def _cleanup(self):
        import shutil
        if getattr(self, "block", None):
            self.block.set()
        stop_app_server(getattr(self, "server", None), getattr(self, "thread", None), getattr(self, "patches", []))
        shutil.rmtree(self.root, ignore_errors=True)

    def poll_until(self, predicate, timeout=4):
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, snapshot = http_json(f"{self.base}/api/library", timeout=1)
            seen.append(snapshot)
            if predicate(snapshot):
                return snapshot
            time.sleep(0.05)
        self.fail(f"no matching snapshot: {seen}")

    def test_post_returns_promptly_and_polls_observe_mid_progress(self):
        started = time.monotonic()
        status, body = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=1)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.8, f"POST blocked for {elapsed:.3f}s: {body}")
        self.assertIn(status, (200, 202))
        self.assertIn(body["state"], {"waiting", "scanning", "indexing"})
        self.assertNotEqual(body["state"], "complete")
        self.assertFalse(body.get("reused"))
        self.assertNotIn("entries", body)
        self.assertTrue(should_show_library_load_progress(body))
        self.assertTrue(self.started.wait(timeout=3))
        mid = self.poll_until(lambda item: item["state"] == "indexing" and item.get("total") == 3 and item.get("done", 0) < 3)
        self.assertIn(f"{mid['done']}/{mid['total']}", mid["text"])
        self.assertIsInstance(mid["percent"], int)
        self.assertGreaterEqual(mid["percent"], 0)
        self.assertLess(mid["percent"], 100)
        self.assertIn(mid["current"], {"one.step", "two.step", "three.step"})
        self.assertTrue(should_show_library_load_progress(mid))
        self.block.set()
        done = self.poll_until(lambda item: item["state"] == "complete")
        self.assertEqual(done["total"], 3)
        self.assertEqual(done["done"], 3)
        self.assertEqual(done["percent"], 100)
        self.assertEqual(len(done["entries"]), 3)
        self.assertFalse(should_show_library_load_progress(done))
        self.assertTrue(all("error" in entry for entry in done["entries"]))

    def test_second_post_reuses_catalog_without_prepare(self):
        http_json(f"{self.base}/api/library/refresh", method="POST", timeout=1)
        self.assertTrue(self.started.wait(timeout=3))
        self.block.set()
        self.poll_until(lambda item: item["state"] == "complete")
        before = list(self.calls)
        started = time.monotonic()
        status, body = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=1)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.8)
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "complete")
        self.assertTrue(body["reused"])
        self.assertEqual(len(body["entries"]), 3)
        self.assertFalse(should_show_library_load_progress(body))
        self.assertEqual(self.calls, before)

    def test_static_library_page_has_load_dialog_markup(self):
        with urllib.request.urlopen(f"{self.base}/", timeout=3) as response:
            headers = response.headers
            page = response.read().decode("utf-8")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn('id="library-load-progress"', page)
        self.assertIn("正在加载基础模型库", page)
        self.assertIn('id="library-load-percent"', page)
        self.assertIn('id="library-load-file"', page)
        self.assertIn('id="library-load-meter"', page)
        self.assertNotIn('id="library-progress"', page)
        with urllib.request.urlopen(f"{self.base}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertIn("/api/library/refresh", script)
        self.assertIn("shouldShowLibraryLoadProgress", script)
        self.assertIn("library-load-progress", script)
        self.assertNotIn("正在读取…", script)


class LibraryHttpReuseAndFallbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="library-http-reuse-"))
        self.addCleanup(self._cleanup)
        for name in ("one.step", "two.step"):
            write_file(self.root / "Base" / name, f"{name}-v1".encode())
        self.calls = []

        def prepared(path):
            self.calls.append(path.name)
            return fake_prepared(path)

        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, prepared
        )

    def _cleanup(self):
        import shutil
        stop_app_server(getattr(self, "server", None), getattr(self, "thread", None), getattr(self, "patches", []))
        shutil.rmtree(self.root, ignore_errors=True)

    def wait_complete(self, timeout=5):
        _, latest = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=2)
        deadline = time.monotonic() + timeout
        while latest["state"] not in {"complete", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
            _, latest = http_json(f"{self.base}/api/library", timeout=2)
        if latest["state"] not in {"complete", "failed"}:
            self.fail(f"refresh did not finish: {latest}")
        return latest

    def test_simulated_restart_reuses_and_changes_force_refresh(self):
        first = self.wait_complete()
        self.assertEqual(first["state"], "complete")
        self.assertFalse(first.get("reused"))
        self.assertFalse(should_show_library_load_progress(first))
        manifest = self.root / ".cache" / "library-manifest.json"
        self.assertTrue(manifest.is_file())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertIn("fingerprint_version", payload)
        self.assertEqual(set(payload["files"]), {"one.step", "two.step"})
        self.assertIn("size", payload["files"]["one.step"])
        self.assertIn("mtime_ns", payload["files"]["one.step"])

        stop_app_server(self.server, self.thread, self.patches)
        before = list(self.calls)
        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, lambda path: self.calls.append(path.name) or fake_prepared(path)
        )
        status, reused = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=2)
        self.assertEqual(status, 200)
        self.assertEqual(reused["state"], "complete")
        self.assertTrue(reused["reused"])
        self.assertFalse(should_show_library_load_progress(reused))
        self.assertEqual(self.calls, before)

        write_file(self.root / "Base" / "three.step", b"added")
        added = self.wait_complete()
        self.assertFalse(added.get("reused"))
        self.assertEqual(len(added["entries"]), 3)
        self.assertIn("three.step", self.calls)

        (self.root / "Base" / "two.step").unlink()
        removed = self.wait_complete()
        self.assertFalse(removed.get("reused"))
        self.assertEqual(len(removed["entries"]), 2)

        write_file(self.root / "Base" / "one.step", b"same-name-but-changed")
        changed = self.wait_complete()
        self.assertFalse(changed.get("reused"))
        names = [entry["file_name"] for entry in changed["entries"]]
        self.assertIn("one.step", names)

        manifest.write_text("{broken", encoding="utf-8")
        status, migrated = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=2)
        self.assertEqual(status, 200)
        self.assertEqual(migrated["state"], "complete")
        self.assertTrue(migrated["reused"])
        self.assertFalse(should_show_library_load_progress(migrated))
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["version"], 1)

        (self.root / ".cache" / "base.sqlite3").unlink()
        reloaded = self.wait_complete()
        self.assertFalse(reloaded.get("reused"))
        self.assertEqual(len(reloaded["entries"]), 2)

    def test_failed_refresh_is_not_marked_success_and_concurrent_posts_join(self):
        stop_app_server(self.server, self.thread, self.patches)
        block = Event()
        started = Event()

        def prepared(path):
            self.calls.append(path.name)
            started.set()
            if not block.wait(timeout=5):
                raise RuntimeError("blocked too long")
            return fake_prepared(path)

        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, prepared
        )
        results = []

        def post():
            results.append(http_json(f"{self.base}/api/library/refresh", method="POST", timeout=2)[1])

        first = Thread(target=post)
        second = Thread(target=post)
        first.start()
        self.assertTrue(started.wait(timeout=3))
        second.start()
        first.join(timeout=3)
        second.join(timeout=3)
        self.assertEqual(len(results), 2)
        for body in results:
            self.assertIn(body["state"], {"waiting", "scanning", "indexing"})
            self.assertFalse(body.get("reused"))
            self.assertTrue(should_show_library_load_progress(body))
        block.set()
        deadline = time.monotonic() + 4
        done = None
        while time.monotonic() < deadline:
            _, done = http_json(f"{self.base}/api/library", timeout=1)
            if done["state"] == "complete":
                break
            time.sleep(0.05)
        self.assertIsNotNone(done)
        self.assertEqual(done["state"], "complete")

        stop_app_server(self.server, self.thread, self.patches)
        manifest = self.root / ".cache" / "library-manifest.json"
        if manifest.is_file():
            manifest.unlink()
        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, lambda path: (_ for _ in ()).throw(RuntimeError("磁盘不可读"))
        )
        # Index already matches, so a healthy sqlite would reuse without prepare.
        # Remove sqlite so the next load must refresh and hit the failure.
        sqlite = self.root / ".cache" / "base.sqlite3"
        if sqlite.is_file():
            sqlite.unlink()
        if manifest.is_file():
            manifest.unlink()
        stop_app_server(self.server, self.thread, self.patches)
        self.app_module, self.server, self.thread, self.patches, self.base = start_app_server(
            self.root, lambda path: (_ for _ in ()).throw(RuntimeError("磁盘不可读"))
        )
        _, failed = http_json(f"{self.base}/api/library/refresh", method="POST", timeout=2)
        deadline = time.monotonic() + 4
        while failed["state"] not in {"failed", "complete"} and time.monotonic() < deadline:
            time.sleep(0.05)
            _, failed = http_json(f"{self.base}/api/library", timeout=1)
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(should_show_library_load_progress(failed))
        self.assertFalse(manifest.is_file())


if __name__ == "__main__":
    unittest.main()
