"""SQLite history catalog. Source STEP, mesh and GLB are committed together."""

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from time import perf_counter

import numpy as np

from geometry import ROOT, SETTINGS, export_glb, read_step

LIBRARY = ROOT / "data/library.sqlite3"
SCHEMA = """CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY, file_name TEXT NOT NULL, source TEXT NOT NULL, part_number TEXT,
    revision TEXT, sha256 TEXT, step BLOB, info TEXT, mesh BLOB, glb BLOB, error TEXT,
    CHECK (error IS NOT NULL OR (step IS NOT NULL AND mesh IS NOT NULL AND glb IS NOT NULL))
)"""


def connect(database, read_only=True):
    location = f"{database.resolve().as_uri()}?mode=ro" if read_only else str(database)
    db = sqlite3.connect(location, uri=read_only)
    db.row_factory = sqlite3.Row
    return db


def import_catalog(manifest, database):
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    ids = [item["id"] for item in entries]
    if len(ids) != len(set(ids)) or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,96}", id) for id in ids):
        raise ValueError("模型标识重复，或包含不支持的字符")
    database.parent.mkdir(parents=True, exist_ok=True)
    imported = []
    root = manifest.resolve().parent
    with closing(connect(database, read_only=False)) as db, db:
        db.execute(SCHEMA)
        for item in entries:
            path = (root / item["file"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("清单中的 STEP 路径必须位于清单目录内")
            started = perf_counter()
            step = sha256 = info = mesh_blob = glb = error = None
            try:
                step = path.read_bytes()
                sha256 = hashlib.sha256(step).hexdigest()
                mesh = read_step(step, path.name)
                info = mesh.info
                mesh_blob, glb = mesh.packed(), export_glb(mesh)
            except (OSError, RuntimeError, ValueError) as exc:
                error = str(exc)
            db.execute("""INSERT INTO models VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET file_name=excluded.file_name, source=excluded.source,
                part_number=excluded.part_number, revision=excluded.revision, sha256=excluded.sha256,
                step=excluded.step, info=excluded.info, mesh=excluded.mesh, glb=excluded.glb, error=excluded.error""", (
                    item["id"], path.name, item["source"], item.get("part_number"), item.get("revision"),
                    sha256, step, json.dumps(info) if info else None, mesh_blob, glb, error,
                ))
            imported.append({"id": item["id"], "file": path.name, "sha256": sha256, "info": info,
                             "error": error, "seconds": perf_counter() - started})
    return imported


def public_entry(row):
    return {"id": row["id"], "file_name": row["file_name"], "source": row["source"],
            "part_number": row["part_number"], "revision": row["revision"] or "未知",
            "info": json.loads(row["info"]) if row["info"] else None, "error": row["error"]}


def catalog(database):
    with closing(connect(database)) as db:
        return [public_entry(row) for row in db.execute(
            "SELECT id, file_name, source, part_number, revision, info, error FROM models ORDER BY id"
        )]


def coarse_order(new, entries):
    all_candidates = []
    for entry in entries:
        if not entry["error"]:
            info = entry["info"]
            features = [*info["size_mm"], info["area_mm2"], info["volume_mm3"]]
            delta = np.abs(np.log(np.asarray(features) / new.fingerprint))
            score = (delta[:3].mean() + SETTINGS["coarse_area_weight"] * delta[3]
                     + SETTINGS["coarse_volume_weight"] * delta[4])
            all_candidates.append({**entry, "coarse_score": float(score)})
    return sorted(all_candidates, key=lambda entry: (entry["coarse_score"], entry["id"]))


def mesh_blob(database, model_id):
    with closing(connect(database)) as db:
        return db.execute("SELECT mesh FROM models WHERE id=? AND error IS NULL", (model_id,)).fetchone()["mesh"]


def glb_blob(database, model_id):
    with closing(connect(database)) as db:
        row = db.execute("SELECT glb FROM models WHERE id=? AND error IS NULL", (model_id,)).fetchone()
    return row["glb"] if row else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, default=LIBRARY)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = import_catalog(args.manifest, args.db)
    if args.report:
        args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = sum(bool(item["error"]) for item in results)
    print(f"SQLite: {len(results) - failed}/{len(results)}; failed: {failed}; {args.db}", flush=True)
    for item in results:
        if item["error"]:
            print(item["id"], item["error"], flush=True)
