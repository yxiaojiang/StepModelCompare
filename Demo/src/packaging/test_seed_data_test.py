"""Temporary-directory tests for Data/test seeds. Does not touch real Studio Data."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGING = Path(__file__).resolve().parent
SOURCE = PACKAGING.parent
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from build_studio import (  # noqa: E402
    TEST_MODELS,
    TEST_SEED,
    prepare_test_models,
    publish_studio_bundle,
    seed_test_into_data,
)
from studio_data import StudioData  # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_file(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


class PrepareAndSeedTestModelsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="data-test-seed-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_prepare_copies_only_authorized_step_models_with_source_hashes(self):
        dest = self.root / "demo-test"
        write_file(dest / "stale.txt", b"remove-me")
        write_file(dest / "SHOW-HOLE.step", b"old-copy")
        prepare_test_models(dest)
        names = sorted(path.name for path in dest.iterdir() if path.is_file())
        self.assertEqual(names, list(TEST_MODELS))
        for name in TEST_MODELS:
            self.assertEqual(digest(dest / name), digest(TEST_SEED / name))
            self.assertIn(Path(name).suffix.lower(), {".step", ".stp"})
        self.assertFalse((dest / "stale.txt").exists())

    def test_first_seed_creates_test_folder_without_touching_base_or_history(self):
        data = self.root / "Data"
        write_file(data / "Base" / "part.step", b"BASE")
        write_file(data / "History" / "q1" / "status.json", b"{}")
        write_file(data / ".cache" / "studio.log", b"log")
        seed = self.root / "seed"
        prepare_test_models(seed)
        copied, skipped = seed_test_into_data(data, seed)
        self.assertEqual(copied, list(TEST_MODELS))
        self.assertEqual(skipped, [])
        self.assertEqual((data / "Base" / "part.step").read_bytes(), b"BASE")
        self.assertEqual((data / "History" / "q1" / "status.json").read_bytes(), b"{}")
        self.assertEqual((data / ".cache" / "studio.log").read_bytes(), b"log")
        for name in TEST_MODELS:
            self.assertEqual(digest(data / "test" / name), digest(TEST_SEED / name))

    def test_upgrade_does_not_overwrite_modified_test_files(self):
        data = self.root / "Data"
        seed = self.root / "seed"
        prepare_test_models(seed)
        seed_test_into_data(data, seed)
        edited = data / "test" / "SHOW-HOLE.step"
        edited.write_bytes(b"USER-CHANGED-HOLE")
        (data / "test" / "SHOW-DAMAGED.stp").unlink()
        copied, skipped = seed_test_into_data(data, seed)
        self.assertEqual(copied, ["SHOW-DAMAGED.stp"])
        self.assertIn("SHOW-HOLE.step", skipped)
        self.assertEqual(edited.read_bytes(), b"USER-CHANGED-HOLE")
        self.assertEqual(digest(data / "test" / "SHOW-DAMAGED.stp"), digest(TEST_SEED / "SHOW-DAMAGED.stp"))
        self.assertEqual(digest(data / "test" / "SHOW-SYMMETRIC.step"), digest(TEST_SEED / "SHOW-SYMMETRIC.step"))

    def test_compile_publish_then_seed_keeps_existing_data(self):
        built = self.root / "built" / "Studio"
        dest = self.root / "dest" / "Studio"
        write_file(built / "Studio.exe", b"exe-v1")
        write_file(built / "_internal" / "app.txt", b"payload")
        write_file(built / "Data" / "test" / "SHOW-HOLE.step", b"SHOULD-NOT-BE-PUBLISHED")
        write_file(dest / "Data" / "Base" / "custom.step", b"USER-BASE")
        write_file(dest / "Data" / "History" / "q1" / "result.json", b"{}")
        write_file(dest / "Data" / ".cache" / "base.sqlite3", b"cache")
        write_file(dest / "Data" / "test" / "SHOW-HOLE.step", b"USER-CHANGED-HOLE")
        publish_studio_bundle(built, dest)
        self.assertEqual((dest / "Studio.exe").read_bytes(), b"exe-v1")
        self.assertFalse((dest / "Data" / "test" / "SHOW-HOLE.step").read_bytes() == b"SHOULD-NOT-BE-PUBLISHED")
        self.assertEqual((dest / "Data" / "test" / "SHOW-HOLE.step").read_bytes(), b"USER-CHANGED-HOLE")
        seed = self.root / "seed"
        prepare_test_models(seed)
        seed_test_into_data(dest / "Data", seed)
        self.assertEqual((dest / "Data" / "Base" / "custom.step").read_bytes(), b"USER-BASE")
        self.assertEqual((dest / "Data" / "History" / "q1" / "result.json").read_bytes(), b"{}")
        self.assertEqual((dest / "Data" / ".cache" / "base.sqlite3").read_bytes(), b"cache")
        self.assertEqual((dest / "Data" / "test" / "SHOW-HOLE.step").read_bytes(), b"USER-CHANGED-HOLE")
        self.assertEqual(digest(dest / "Data" / "test" / "SHOW-SYMMETRIC.step"), digest(TEST_SEED / "SHOW-SYMMETRIC.step"))


class StudioDataTestFolderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="studio-data-test-"))
        self.addCleanup(self._cleanup)
        self.data = StudioData(self.root)
        self.data.initialize()

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_initialize_creates_lowercase_test_directory(self):
        self.assertTrue(self.data.test.is_dir())
        self.assertEqual(self.data.test.name, "test")
        self.assertTrue(self.data.base.is_dir())
        self.assertTrue(self.data.history.is_dir())

    def test_refresh_base_does_not_import_test_models(self):
        from test_library_progress import fake_prepared, write_file as write_step

        write_step(self.data.test / "SHOW-HOLE.step", b"ISO-10303-21; not-in-base")
        write_step(self.data.base / "library-only.step", b"ISO-10303-21; base")
        calls = []

        def prepared(path):
            calls.append(path)
            return fake_prepared(path)

        with patch("studio_data.prepare_model", side_effect=prepared):
            self.data.refresh_base()
        from library import catalog
        entries = catalog(self.data.library)
        names = {item["file_name"] for item in entries}
        self.assertEqual(names, {"library-only.step"})
        self.assertTrue(all(path.is_relative_to(self.data.base) for path in calls))
        self.assertFalse(any(path.is_relative_to(self.data.test) for path in calls))


class InstallerScriptContractTests(unittest.TestCase):
    def test_inno_script_installs_test_models_without_packing_live_data(self):
        iss = (PACKAGING / "Studio.iss").read_text(encoding="utf-8")
        self.assertIn('Name: "{app}\\Data\\test"; Flags: uninsneveruninstall', iss)
        self.assertIn('Source: "{#TestDir}\\*"', iss)
        self.assertIn('DestDir: "{app}\\Data\\test"', iss)
        self.assertIn("onlyifdoesntexist", iss)
        self.assertIn("uninsneveruninstall", iss)
        self.assertNotIn("{#BundleDir}\\Data", iss)
        self.assertNotIn("History", iss.split("[Files]")[1].split("[Icons]")[0])
        self.assertNotIn(".cache", iss.split("[Files]")[1].split("[Icons]")[0])
        source = (PACKAGING / "build_studio.py").read_text(encoding="utf-8")
        self.assertIn("/DTestDir={TEST}", source)
        self.assertIn("seed_test_into_data(BUNDLE / \"Data\", TEST)", source)


if __name__ == "__main__":
    unittest.main()
