"""Index the installed Base folder and keep completed comparison archives separate."""

import hashlib
import json
from contextlib import closing
from pathlib import Path

from OCP import __version__ as ocp_version

from geometry import SETTINGS
from library import SCHEMA, STEP_SUFFIXES, connect, prepare_model, save_model
from paths import ROOT

SAMPLE_METADATA = ROOT / "demo-models.json"
MANIFEST_VERSION = 1
MANIFEST_NAME = "library-manifest.json"


class StudioData:
    def __init__(self, data_path):
        self.base = Path(data_path) / "Base"
        self.history = Path(data_path) / "History"
        self.pending = Path(data_path) / ".work"
        self.library = Path(data_path) / ".cache" / "base.sqlite3"
        self.manifest = Path(data_path) / ".cache" / MANIFEST_NAME
        self.samples = {}
        if SAMPLE_METADATA.is_file():
            self.samples = json.loads(SAMPLE_METADATA.read_text(encoding="utf-8"))

    def initialize(self):
        for directory in (self.base, self.history, self.pending, self.library.parent):
            directory.mkdir(parents=True, exist_ok=True)

    def index_version(self):
        payload = json.dumps([
            1, ocp_version, SETTINGS["mesh_chord_mm"], SETTINGS["mesh_angle_rad"], self.samples
        ], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def scan_base_inventory(self):
        files = {}
        root = self.base.resolve()
        if not root.is_dir():
            return files
        for path in sorted(root.rglob("*")):
            if (path.suffix.lower() not in STEP_SUFFIXES or not path.is_file()
                    or not path.resolve().is_relative_to(root)):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            key = path.relative_to(root).as_posix().casefold()
            files[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return files

    def load_manifest(self):
        try:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
            return None
        if not isinstance(data.get("fingerprint_version"), str) or not isinstance(data.get("files"), dict):
            return None
        files = {}
        for key, item in data["files"].items():
            if not isinstance(key, str) or not isinstance(item, dict):
                return None
            if not isinstance(item.get("size"), int) or not isinstance(item.get("mtime_ns"), int):
                return None
            files[key] = {"size": item["size"], "mtime_ns": item["mtime_ns"]}
        return {
            "version": data["version"],
            "fingerprint_version": data["fingerprint_version"],
            "files": files,
        }

    def write_manifest(self, inventory, fingerprint_version):
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": MANIFEST_VERSION,
            "fingerprint_version": fingerprint_version,
            "files": {
                key: {"size": item["size"], "mtime_ns": item["mtime_ns"]}
                for key, item in inventory.items()
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self.manifest.with_name(self.manifest.name + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(self.manifest)

    def sqlite_matches_inventory(self, inventory, fingerprint_version):
        if not self.library.is_file():
            return False
        try:
            with closing(connect(self.library, read_only=True)) as db:
                rows = db.execute(
                    "SELECT relative_path, size, mtime, version FROM base_files"
                ).fetchall()
        except Exception:
            return False
        indexed = {row["relative_path"]: row for row in rows}
        if set(indexed) != set(inventory):
            return False
        for key, item in inventory.items():
            row = indexed[key]
            if row["version"] != fingerprint_version:
                return False
            if row["mtime"] == -1:
                continue
            if row["size"] != item["size"] or row["mtime"] != item["mtime_ns"]:
                return False
        return True

    def can_reuse_index(self):
        version = self.index_version()
        inventory = self.scan_base_inventory()
        if not self.sqlite_matches_inventory(inventory, version):
            return False
        manifest = self.load_manifest()
        if (manifest is None or manifest["fingerprint_version"] != version
                or manifest["files"] != inventory):
            self.write_manifest(inventory, version)
        return True

    def refresh_base(self, progress=None):
        def report(done, total=None, current=""):
            if progress:
                progress(done, total, current)

        files = {}
        root = self.base.resolve()
        found = 0
        report(0, None)
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if (path.suffix.lower() not in STEP_SUFFIXES or not path.is_file()
                        or not path.resolve().is_relative_to(root)):
                    continue
                files[path.relative_to(root).as_posix().casefold()] = path
                found += 1
                report(found, None, path.relative_to(root).as_posix())
        fingerprint_version = self.index_version()
        inventory = {}
        with closing(connect(self.library, read_only=False)) as db, db:
            db.execute(SCHEMA)
            db.execute("""CREATE TABLE IF NOT EXISTS base_files (
                relative_path TEXT PRIMARY KEY, id TEXT NOT NULL, size INTEGER NOT NULL,
                mtime INTEGER NOT NULL, version TEXT NOT NULL
            )""")
            indexed = {row["relative_path"]: row for row in db.execute("SELECT * FROM base_files")}
            changes = []
            for key, path in list(files.items()):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    del files[key]
                    continue
                inventory[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
                previous = indexed.get(key)
                if (previous and previous["size"] == stat.st_size
                        and previous["mtime"] == stat.st_mtime_ns and previous["version"] == fingerprint_version):
                    continue
                changes.append((key, path, stat, previous))
            total = len(files)
            done = total - len(changes)
            report(done, total)
            for offset, (key, path, stat, previous) in enumerate(changes, start=1):
                current = path.relative_to(root).as_posix()
                report(done + offset - 1, total, current)
                prepared = prepare_model(path)
                sample = self.samples.get(key)
                if sample and sample["sha256"] == prepared[0]:
                    entry = sample
                else:
                    file_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
                    entry = {"id": f"BASE-{file_id}", "source": "基础模型"}
                if previous and previous["id"] != entry["id"]:
                    db.execute("DELETE FROM models WHERE id=?", (previous["id"],))
                save_model(db, entry, path.relative_to(root).as_posix(), prepared)
                db.execute("INSERT OR REPLACE INTO base_files VALUES (?, ?, ?, ?, ?)", (
                    key, entry["id"], stat.st_size, stat.st_mtime_ns if prepared[0] else -1, fingerprint_version
                ))
                report(done + offset, total, current)
            for key in indexed.keys() - files.keys():
                db.execute("DELETE FROM models WHERE id=?", (indexed[key]["id"],))
                db.execute("DELETE FROM base_files WHERE relative_path=?", (key,))
            report(total, total)
        self.write_manifest(inventory, fingerprint_version)
        return inventory
