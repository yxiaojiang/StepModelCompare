"""Isolated tests for base-library load progress and manifest reuse. Temporary Data only."""

import hashlib
from contextlib import closing
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock, Thread
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from library import SCHEMA, connect, catalog
from library_loader import LibraryLoader
from studio_data import MANIFEST_VERSION, StudioData


def write_file(path, content=b"step"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def fake_prepared(path, error=None):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if error:
        return digest, path.read_bytes(), None, None, None, error
    return digest, path.read_bytes(), {"size_mm": [1, 1, 1]}, b"mesh", b"glb", None


class RefreshBaseProgressTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="library-progress-"))
        self.addCleanup(self._cleanup)
        self.data = StudioData(self.root)
        self.data.initialize()
        self.reports = []

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def record(self, done, total, current=""):
        self.reports.append((done, total, current))

    def pairs(self):
        return [(done, total) for done, total, _current in self.reports]

    def test_scan_is_indeterminate_then_counts_match_processed_files(self):
        for name in ("a.step", "sub/b.stp", "ignore.txt"):
            write_file(self.data.base / name)
        with patch("studio_data.prepare_model", side_effect=lambda path: fake_prepared(path)):
            self.data.refresh_base(self.record)
        self.assertGreaterEqual(len(self.reports), 2)
        self.assertEqual(self.pairs()[0], (0, None))
        scan = [item for item in self.reports if item[1] is None]
        self.assertTrue(scan)
        self.assertTrue(all(item[0] >= 0 for item in scan))
        self.assertEqual(scan[-1][0], 2)
        self.assertIn("b.stp", scan[-1][2])
        known = [item for item in self.reports if item[1] is not None]
        self.assertTrue(known)
        self.assertTrue(all(item[1] == 2 for item in known))
        dones = [item[0] for item in known]
        self.assertEqual(dones, sorted(dones))
        self.assertEqual(known[0][0], 0)
        self.assertEqual(known[-1][:2], (2, 2))
        self.assertEqual(len(catalog(self.data.library)), 2)
        manifest = json.loads(self.data.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], MANIFEST_VERSION)
        self.assertEqual(set(manifest["files"]), {"a.step", "sub/b.stp"})

    def test_empty_library_reports_zero_total_without_fake_percent(self):
        self.data.refresh_base(self.record)
        known = [item for item in self.pairs() if item[1] is not None]
        self.assertEqual(known[-1], (0, 0))
        self.assertTrue(any(total is None for _, total in self.pairs()))
        self.assertEqual(catalog(self.data.library), [])
        manifest = json.loads(self.data.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"], {})

    def test_cached_files_are_counted_without_reprocessing(self):
        write_file(self.data.base / "a.step")
        write_file(self.data.base / "b.step")
        calls = []

        def prepared(path):
            calls.append(path.name)
            return fake_prepared(path)

        with patch("studio_data.prepare_model", side_effect=prepared):
            self.data.refresh_base(self.record)
            first_calls = list(calls)
            self.reports.clear()
            self.data.refresh_base(self.record)
        self.assertEqual(sorted(first_calls), ["a.step", "b.step"])
        self.assertEqual(calls, first_calls)
        known = [item for item in self.pairs() if item[1] is not None]
        self.assertEqual(known[0], (2, 2))
        self.assertEqual(known[-1], (2, 2))

    def test_single_file_failure_is_counted_and_refresh_completes(self):
        write_file(self.data.base / "good.step")
        write_file(self.data.base / "bad.step")

        def prepared(path):
            if path.name == "bad.step":
                return fake_prepared(path, error="invalid step")
            return fake_prepared(path)

        with patch("studio_data.prepare_model", side_effect=prepared):
            self.data.refresh_base(self.record)
        known = [item for item in self.pairs() if item[1] is not None]
        self.assertEqual(known[-1], (2, 2))
        entries = catalog(self.data.library)
        errors = {item["file_name"]: item["error"] for item in entries}
        self.assertIsNone(errors["good.step"])
        self.assertEqual(errors["bad.step"], "invalid step")
        self.assertTrue(self.data.can_reuse_index())

    def test_real_invalid_step_is_recorded_as_import_error(self):
        write_file(self.data.base / "broken.stp", b"not-a-step-file")
        self.data.refresh_base(self.record)
        entries = catalog(self.data.library)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["error"])
        self.assertEqual(self.pairs()[-1], (1, 1))

    def test_failed_refresh_does_not_write_success_manifest(self):
        write_file(self.data.base / "a.step")
        with patch("studio_data.prepare_model", side_effect=RuntimeError("解析失败")):
            with self.assertRaises(RuntimeError):
                self.data.refresh_base(self.record)
        self.assertFalse(self.data.manifest.is_file())
        self.assertFalse(self.data.can_reuse_index())


class LibraryLoaderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="library-loader-"))
        self.addCleanup(self._cleanup)
        self.data = StudioData(self.root)
        self.data.initialize()
        self.lock = Lock()
        self.loader = LibraryLoader(studio=self.data, database=self.data.library, work_lock=self.lock)
        self.addCleanup(self.loader.close)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def wait_for(self, predicate, timeout=5, loader=None):
        loader = loader or self.loader
        deadline = time.monotonic() + timeout
        latest = loader.snapshot()
        while time.monotonic() < deadline:
            latest = loader.snapshot()
            if predicate(latest):
                return latest
            time.sleep(0.01)
        self.fail(f"timed out waiting for condition, last status={latest}")

    def test_empty_library_completes_with_empty_catalog(self):
        started = self.loader.start()
        self.assertIn(started["state"], {"waiting", "scanning", "indexing", "complete"})
        self.assertFalse(started.get("reused"))
        done = self.wait_for(lambda item: item["state"] == "complete")
        self.assertEqual(done["entries"], [])
        self.assertEqual(done["done"], 0)
        self.assertEqual(done["total"], 0)
        self.assertEqual(done["percent"], 100)
        self.assertFalse(done.get("reused"))

    def test_progress_is_visible_before_completion_and_counts_files(self):
        for name in ("one.step", "two.step", "three.step"):
            write_file(self.data.base / name)
        started = Event()
        release = Event()
        processed = []

        def prepared(path):
            processed.append(path.name)
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return fake_prepared(path)

        with patch("studio_data.prepare_model", side_effect=prepared):
            snapshot = self.loader.start()
            self.assertNotEqual(snapshot["state"], "complete")
            self.assertFalse(snapshot.get("reused"))
            self.assertIsNone(snapshot.get("entries"))
            self.assertTrue(started.wait(timeout=2))
            mid = self.wait_for(lambda item: item["state"] == "indexing" and item["total"] == 3 and item["done"] < 3)
            self.assertGreaterEqual(mid["done"], 0)
            self.assertIn(f"{mid['done']}/{mid['total']}", mid["text"])
            self.assertIsInstance(mid["percent"], int)
            self.assertGreaterEqual(mid["percent"], 0)
            self.assertLess(mid["percent"], 100)
            self.assertIn(mid["current"], {"one.step", "two.step", "three.step"})
            joined = self.loader.start()
            self.assertEqual(joined["state"], "indexing")
            self.assertFalse(joined.get("reused"))
            self.assertIsNone(joined.get("entries"))
            release.set()
            done = self.wait_for(lambda item: item["state"] == "complete")
        self.assertEqual(done["done"], 3)
        self.assertEqual(done["total"], 3)
        self.assertEqual(done["percent"], 100)
        self.assertEqual(len(done["entries"]), 3)
        self.assertEqual(sorted(processed), ["one.step", "three.step", "two.step"])

    def test_scan_text_does_not_invent_a_percentage(self):
        gate = Event()
        original = StudioData.refresh_base

        def delayed(instance, progress=None):
            if progress:
                progress(0, None)
                progress(2, None, "b.step")
            self.assertTrue(gate.wait(timeout=2))
            original(instance, progress)

        write_file(self.data.base / "a.step")
        write_file(self.data.base / "b.step")
        with patch.object(StudioData, "refresh_base", delayed):
            self.loader.start()
            mid = self.wait_for(lambda item: item["state"] == "scanning" and item["done"] == 2)
            self.assertIsNone(mid["total"])
            self.assertIsNone(mid["percent"])
            self.assertIn("正在扫描", mid["text"])
            self.assertNotIn("%", mid["text"])
            self.assertEqual(mid["current"], "b.step")
            gate.set()
            done = self.wait_for(lambda item: item["state"] == "complete")
        self.assertEqual(done["total"], 2)

    def test_overall_failure_is_reported_and_can_be_retried(self):
        def boom(progress=None):
            raise ValueError("磁盘不可读")

        with patch.object(self.data, "refresh_base", side_effect=boom):
            self.loader.start()
            failed = self.wait_for(lambda item: item["state"] == "failed")
        self.assertEqual(failed["reason"], "磁盘不可读")
        self.assertIn("磁盘不可读", failed["text"])
        self.assertNotIn("entries", failed)
        self.assertFalse(self.data.manifest.is_file())
        write_file(self.data.base / "ok.step")
        with patch("studio_data.prepare_model", side_effect=lambda path: fake_prepared(path)):
            self.loader.start()
            done = self.wait_for(lambda item: item["state"] == "complete")
        self.assertEqual(len(done["entries"]), 1)
        self.assertTrue(self.data.manifest.is_file())

    def test_waiting_state_when_work_lock_is_busy(self):
        self.lock.acquire()
        try:
            self.loader.start()
            waiting = self.wait_for(lambda item: item["state"] == "waiting")
            self.assertIsNone(waiting["total"])
            self.assertIsNone(waiting["percent"])
            self.assertIn("准备", waiting["text"])
        finally:
            self.lock.release()
        done = self.wait_for(lambda item: item["state"] == "complete")
        self.assertEqual(done["entries"], [])

    def test_current_does_not_restart_a_finished_refresh(self):
        self.loader.start()
        self.wait_for(lambda item: item["state"] == "complete")
        again = self.loader.current(start_if_idle=True)
        self.assertEqual(again["state"], "complete")

    def test_non_studio_mode_returns_existing_catalog(self):
        database = self.root / "existing.sqlite3"
        with closing(connect(database, read_only=False)) as db, db:
            db.execute(SCHEMA)
        loader = LibraryLoader(studio=None, database=database, work_lock=Lock(), catalog_fn=catalog)
        self.addCleanup(loader.close)
        started = loader.start()
        self.assertTrue(started.get("reused"))
        done = self.wait_for(lambda item: item["state"] == "complete", loader=loader)
        self.assertEqual(done["entries"], [])
        self.assertEqual(done["total"], 0)
        self.assertTrue(done["reused"])


class ManifestReuseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="library-manifest-"))
        self.addCleanup(self._cleanup)
        self.data = StudioData(self.root)
        self.data.initialize()
        self.lock = Lock()
        self.calls = []

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def make_loader(self):
        loader = LibraryLoader(studio=self.data, database=self.data.library, work_lock=self.lock)
        self.addCleanup(loader.close)
        return loader

    def wait_for(self, loader, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        latest = loader.snapshot()
        while time.monotonic() < deadline:
            latest = loader.snapshot()
            if predicate(latest):
                return latest
            time.sleep(0.01)
        self.fail(f"timed out waiting for condition, last status={latest}")

    def prepared(self, path):
        self.calls.append(path.name)
        return fake_prepared(path)

    def first_load(self, names=("a.step", "b.step")):
        for name in names:
            write_file(self.data.base / name, f"{name}-content".encode())
        loader = self.make_loader()
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            loader.start()
            done = self.wait_for(loader, lambda item: item["state"] == "complete")
        self.assertFalse(done.get("reused"))
        self.assertEqual(len(done["entries"]), len(names))
        self.assertEqual(sorted(self.calls), sorted(Path(name).name for name in names))
        return loader, done

    def test_same_process_reopen_reuses_without_refresh(self):
        loader, _done = self.first_load()
        before = list(self.calls)
        with patch("studio_data.prepare_model", side_effect=self.prepared) as mocked:
            with patch.object(self.data, "refresh_base", wraps=self.data.refresh_base) as refresh:
                again = loader.start()
        self.assertEqual(again["state"], "complete")
        self.assertTrue(again["reused"])
        self.assertEqual(len(again["entries"]), 2)
        self.assertEqual(self.calls, before)
        refresh.assert_not_called()
        mocked.assert_not_called()

    def test_simulated_restart_reuses_without_refresh(self):
        self.first_load()
        before = list(self.calls)
        restarted = self.make_loader()
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            with patch.object(self.data, "refresh_base", wraps=self.data.refresh_base) as refresh:
                snapshot = restarted.start()
        self.assertEqual(snapshot["state"], "complete")
        self.assertTrue(snapshot["reused"])
        self.assertEqual(len(snapshot["entries"]), 2)
        self.assertEqual(self.calls, before)
        refresh.assert_not_called()

    def test_added_file_triggers_refresh(self):
        loader, _done = self.first_load()
        write_file(self.data.base / "c.step", b"new-model")
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            snapshot = loader.start()
            self.assertFalse(snapshot.get("reused"))
            self.assertNotEqual(snapshot["state"], "complete")
            done = self.wait_for(loader, lambda item: item["state"] == "complete")
        self.assertFalse(done.get("reused"))
        self.assertEqual(len(done["entries"]), 3)
        self.assertIn("c.step", self.calls)

    def test_deleted_file_triggers_refresh(self):
        loader, _done = self.first_load()
        (self.data.base / "a.step").unlink()
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            snapshot = loader.start()
            if snapshot["state"] != "complete":
                snapshot = self.wait_for(loader, lambda item: item["state"] == "complete")
        self.assertFalse(snapshot.get("reused"))
        self.assertEqual(len(snapshot["entries"]), 1)
        self.assertEqual(snapshot["entries"][0]["file_name"], "b.step")

    def test_same_name_modification_triggers_refresh(self):
        loader, _done = self.first_load()
        before = list(self.calls)
        write_file(self.data.base / "a.step", b"changed-content-for-same-name")
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            snapshot = loader.start()
            self.assertNotEqual(snapshot.get("reused"), True)
            done = self.wait_for(loader, lambda item: item["state"] == "complete")
        self.assertFalse(done.get("reused"))
        self.assertEqual(len(done["entries"]), 2)
        self.assertIn("a.step", self.calls[len(before):])

    def test_missing_or_corrupt_manifest_migrates_when_sqlite_matches(self):
        _loader, _done = self.first_load()
        before = list(self.calls)
        self.data.manifest.write_text("{not-json", encoding="utf-8")
        restarted = self.make_loader()
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            with patch.object(self.data, "refresh_base", wraps=self.data.refresh_base) as refresh:
                snapshot = restarted.start()
        self.assertTrue(snapshot["reused"])
        self.assertEqual(snapshot["state"], "complete")
        refresh.assert_not_called()
        self.assertEqual(self.calls, before)
        manifest = json.loads(self.data.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], MANIFEST_VERSION)
        self.assertEqual(set(manifest["files"]), {"a.step", "b.step"})

        self.data.manifest.unlink()
        restarted = self.make_loader()
        with patch.object(self.data, "refresh_base", wraps=self.data.refresh_base) as refresh:
            snapshot = restarted.start()
        self.assertTrue(snapshot["reused"])
        refresh.assert_not_called()
        self.assertTrue(self.data.manifest.is_file())

        payload = json.loads(self.data.manifest.read_text(encoding="utf-8"))
        payload["version"] = 99
        self.data.manifest.write_text(json.dumps(payload), encoding="utf-8")
        restarted = self.make_loader()
        with patch.object(self.data, "refresh_base", wraps=self.data.refresh_base) as refresh:
            snapshot = restarted.start()
        self.assertTrue(snapshot["reused"])
        refresh.assert_not_called()
        self.assertEqual(json.loads(self.data.manifest.read_text(encoding="utf-8"))["version"], MANIFEST_VERSION)

    def test_missing_index_forces_reload(self):
        _loader, _done = self.first_load()
        self.data.library.unlink()
        restarted = self.make_loader()
        self.calls.clear()
        with patch("studio_data.prepare_model", side_effect=self.prepared):
            snapshot = restarted.start()
            self.assertFalse(snapshot.get("reused"))
            done = self.wait_for(restarted, lambda item: item["state"] == "complete")
        self.assertFalse(done.get("reused"))
        self.assertEqual(sorted(self.calls), ["a.step", "b.step"])

    def test_concurrent_starts_join_in_flight_refresh(self):
        write_file(self.data.base / "one.step")
        started = Event()
        release = Event()

        def prepared(path):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return fake_prepared(path)

        loader = self.make_loader()
        with patch("studio_data.prepare_model", side_effect=prepared):
            first = loader.start()
            self.assertTrue(started.wait(timeout=2))
            second = loader.start()
            self.assertIn(first["state"], {"waiting", "scanning", "indexing"})
            self.assertIn(second["state"], {"waiting", "scanning", "indexing"})
            self.assertFalse(second.get("reused"))
            self.assertIsNone(second.get("entries"))
            release.set()
            done = self.wait_for(loader, lambda item: item["state"] == "complete")
        self.assertEqual(len(done["entries"]), 1)


class QueryIndexingCallbackTests(unittest.TestCase):
    def test_indexing_handles_unknown_and_known_totals(self):
        updates = []

        def progress(percent, text):
            updates.append((percent, text))

        def indexing(done, total, current=""):
            if total:
                current_hint = f" · {current}" if current else ""
                progress(17 + 9 * done // total, f"正在更新基础模型库（{done}/{total}）{current_hint}")
            else:
                found = f"（已找到 {done} 个）" if done else ""
                progress(16, f"正在扫描基础模型库{found}")

        indexing(0, None)
        indexing(4, None)
        indexing(1, 4, "SHOW-HOLE.step")
        indexing(4, 4)
        self.assertEqual(updates[0], (16, "正在扫描基础模型库"))
        self.assertEqual(updates[1], (16, "正在扫描基础模型库（已找到 4 个）"))
        self.assertEqual(updates[2][0], 17 + 9 * 1 // 4)
        self.assertIn("SHOW-HOLE.step", updates[2][1])
        self.assertEqual(updates[3], (26, "正在更新基础模型库（4/4）"))
        self.assertTrue(updates[2][0] <= updates[3][0])


if __name__ == "__main__":
    unittest.main()
