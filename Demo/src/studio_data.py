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


class StudioData:
    def __init__(self, data_path):
        self.base = Path(data_path) / "Base"
        self.history = Path(data_path) / "History"
        self.pending = Path(data_path) / ".work"
        self.library = Path(data_path) / ".cache" / "base.sqlite3"
        self.samples = {}
        if SAMPLE_METADATA.is_file():
            self.samples = json.loads(SAMPLE_METADATA.read_text(encoding="utf-8"))

    def initialize(self):
        for directory in (self.base, self.history, self.pending, self.library.parent):
            directory.mkdir(parents=True, exist_ok=True)

    def refresh_base(self, progress=None):
        files = {}
        root = self.base.resolve()
        for path in sorted(root.rglob("*")):
            if (path.suffix.lower() not in STEP_SUFFIXES or not path.is_file()
                    or not path.resolve().is_relative_to(root)):
                continue
            files[path.relative_to(root).as_posix().casefold()] = path
        fingerprint_inputs = json.dumps([
            1, ocp_version, SETTINGS["mesh_chord_mm"], SETTINGS["mesh_angle_rad"], self.samples
        ], sort_keys=True)
        fingerprint_version = hashlib.sha256(fingerprint_inputs.encode("utf-8")).hexdigest()
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
                previous = indexed.get(key)
                if (previous and previous["size"] == stat.st_size
                        and previous["mtime"] == stat.st_mtime_ns and previous["version"] == fingerprint_version):
                    continue
                changes.append((key, path, stat, previous))
            for idx, (key, path, stat, previous) in enumerate(changes):
                if progress:
                    progress(idx, len(changes))
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
            for key in indexed.keys() - files.keys():
                db.execute("DELETE FROM models WHERE id=?", (indexed[key]["id"],))
                db.execute("DELETE FROM base_files WHERE relative_path=?", (key,))

    def query_directory(self, query_id):
        archive = self.history / query_id
        return archive if archive.is_dir() else self.pending / query_id
