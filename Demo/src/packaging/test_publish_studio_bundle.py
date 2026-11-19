"""Regression tests for preserving Studio/Data across compile publishes."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_studio import PUBLISH_PREFIX, publish_studio_bundle  # noqa: E402


def write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def digest_tree(root):
    snapshot = {}
    root = Path(root)
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative + "/"] = "dir"
        elif path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


class PublishStudioBundleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="studio-publish-test-"))
        self.addCleanup(self._cleanup)
        self.built = self.root / "built" / "Studio"
        self.dest = self.root / "dest" / "Studio"

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def seed_build(self, version):
        write_file(self.built / "Studio.exe", f"exe-{version}")
        write_file(self.built / "_internal" / "payload.txt", f"payload-{version}")
        write_file(self.built / "_internal" / "extra" / "lib.txt", f"lib-{version}")

    def seed_data(self, dest=None):
        dest = dest or self.dest
        data = dest / "Data"
        write_file(data / "Base" / "custom" / "part.step", b"USER-BASE-MODEL")
        write_file(data / "Base" / ".hidden-model.stp", b"HIDDEN-BASE")
        write_file(data / "History" / "q1" / "status.json", '{"id": "q1"}')
        write_file(data / ".cache" / "studio.log", "keep-this-log")
        (data / "Base" / "empty-dir").mkdir(parents=True, exist_ok=True)
        (data / "History" / "empty-history").mkdir(parents=True, exist_ok=True)
        return data

    def test_first_publish_creates_program_without_inventing_data(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        self.assertEqual((self.dest / "Studio.exe").read_text(encoding="utf-8"), "exe-v1")
        self.assertEqual((self.dest / "_internal" / "payload.txt").read_text(encoding="utf-8"), "payload-v1")
        self.assertFalse((self.dest / "Data").exists())

    def test_existing_data_survives_consecutive_publishes_with_same_hashes(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        data = self.seed_data()
        before = digest_tree(data)
        self.seed_build("v2")
        publish_studio_bundle(self.built, self.dest)
        self.assertEqual((self.dest / "Studio.exe").read_text(encoding="utf-8"), "exe-v2")
        self.assertEqual((self.dest / "_internal" / "payload.txt").read_text(encoding="utf-8"), "payload-v2")
        self.assertEqual(digest_tree(data), before)
        self.seed_build("v3")
        publish_studio_bundle(self.built, self.dest)
        self.assertEqual((self.dest / "Studio.exe").read_text(encoding="utf-8"), "exe-v3")
        self.assertEqual(digest_tree(data), before)
        self.assertTrue((data / "Base" / "empty-dir").is_dir())
        self.assertTrue((data / "History" / "empty-history").is_dir())
        self.assertEqual((data / "Base" / ".hidden-model.stp").read_bytes(), b"HIDDEN-BASE")

    def test_build_data_does_not_overwrite_or_merge_into_existing_data(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        data = self.seed_data()
        write_file(data / "Base" / "shared.step", b"ORIGINAL")
        before = digest_tree(data)
        write_file(self.built / "Data" / "Base" / "shared.step", b"FROM-BUILD")
        write_file(self.built / "Data" / "Base" / "new-from-build.step", b"SHOULD-NOT-APPEAR")
        self.seed_build("v2")
        write_file(self.built / "Data" / "Base" / "shared.step", b"FROM-BUILD")
        write_file(self.built / "Data" / "Base" / "new-from-build.step", b"SHOULD-NOT-APPEAR")
        publish_studio_bundle(self.built, self.dest)
        self.assertEqual((self.dest / "Studio.exe").read_text(encoding="utf-8"), "exe-v2")
        self.assertEqual(digest_tree(data), before)
        self.assertEqual((data / "Base" / "shared.step").read_bytes(), b"ORIGINAL")
        self.assertFalse((data / "Base" / "new-from-build.step").exists())

    def test_stale_program_files_are_removed_but_data_is_kept(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        data = self.seed_data()
        before = digest_tree(data)
        write_file(self.dest / "orphan.dll", b"stale")
        write_file(self.dest / "_internal" / "payload.txt", "payload-v1")
        (self.built / "_internal" / "extra" / "lib.txt").unlink()
        (self.built / "_internal" / "extra").rmdir()
        write_file(self.built / "Studio.exe", "exe-v2")
        write_file(self.built / "_internal" / "payload.txt", "payload-v2")
        publish_studio_bundle(self.built, self.dest)
        self.assertFalse((self.dest / "orphan.dll").exists())
        self.assertFalse((self.dest / "_internal" / "extra").exists())
        self.assertEqual(digest_tree(data), before)

    def test_replace_failure_keeps_data_and_restores_previous_program(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        data = self.seed_data()
        before = digest_tree(data)
        self.seed_build("v2")
        real_rename = Path.rename

        def failing_rename(path, target):
            if Path(path).name.startswith(f"{PUBLISH_PREFIX}new-Studio.exe"):
                raise OSError("simulated swap failure")
            return real_rename(path, target)

        with patch.object(Path, "rename", failing_rename):
            with self.assertRaises(OSError):
                publish_studio_bundle(self.built, self.dest)
        self.assertEqual(digest_tree(data), before)
        self.assertEqual((self.dest / "Studio.exe").read_text(encoding="utf-8"), "exe-v1")

    def test_restore_failure_keeps_data_and_leaves_locatable_backup(self):
        self.seed_build("v1")
        publish_studio_bundle(self.built, self.dest)
        data = self.seed_data()
        before = digest_tree(data)
        self.seed_build("v2")
        real_rename = Path.rename

        def failing_rename(path, target):
            name = Path(path).name
            if name.startswith(f"{PUBLISH_PREFIX}new-Studio.exe") or name.startswith(f"{PUBLISH_PREFIX}old-Studio.exe"):
                raise OSError("simulated restore failure")
            return real_rename(path, target)

        with patch.object(Path, "rename", failing_rename):
            with self.assertRaises(RuntimeError) as caught:
                publish_studio_bundle(self.built, self.dest)
        message = str(caught.exception)
        self.assertIn("The previous item was left at", message)
        self.assertEqual(digest_tree(data), before)
        backups = list(self.dest.glob(f"{PUBLISH_PREFIX}old-Studio.exe-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "exe-v1")
        self.assertIn(str(backups[0]), message)


class PyInstallerCleanDirectoryTests(unittest.TestCase):
    def test_noconfirm_onedir_collect_deletes_data_inside_dist_name(self):
        from PyInstaller.config import CONF
        from PyInstaller.building.utils import _make_clean_directory

        root = Path(tempfile.mkdtemp(prefix="studio-pyinstaller-clean-"))
        try:
            bundle = root / "Studio"
            write_file(bundle / "Studio.exe", b"exe")
            write_file(bundle / "Data" / "History" / "keep.json", b"user-data")
            previous = {key: CONF[key] for key in ("noconfirm", "workpath", "specpath") if key in CONF}
            CONF["noconfirm"] = True
            CONF["workpath"] = str(root / "work")
            CONF["specpath"] = str(root / "spec")
            try:
                _make_clean_directory(str(bundle))
            finally:
                for key in ("noconfirm", "workpath", "specpath"):
                    if key in previous:
                        CONF[key] = previous[key]
                    else:
                        CONF.pop(key, None)
            self.assertTrue(bundle.is_dir())
            self.assertFalse((bundle / "Data").exists())
            self.assertFalse((bundle / "Studio.exe").exists())
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
