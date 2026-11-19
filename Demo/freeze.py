"""Fingerprint exactly the program, local assets, library and registered rehearsal files."""

import argparse
import hashlib
import json
import platform
from pathlib import Path

from geometry import ROOT, SETTINGS
from library import LIBRARY
from make_samples import SAMPLES, digest, write_json


def snapshot():
    catalog = SAMPLES / "catalog.json"
    registered = SAMPLES / "rehearsal/cases.json"
    manifest = json.loads(catalog.read_text(encoding="utf-8"))
    queries = json.loads(registered.read_text(encoding="utf-8"))
    dist = ROOT / "dist"
    inputs = [LIBRARY, catalog, registered, ROOT / "requirements.txt", ROOT / "settings.json",
              *ROOT.glob("*.py"), *dist.glob("*.js"), *dist.glob("*.html"),
              *dist.glob("*.css"), *(p for p in (dist / "vendor").rglob("*") if p.is_file()),
              *(SAMPLES / item["file"] for item in manifest),
              *(registered.parent / "queries" / item["file"] for item in queries),
              registered.parent / "queries/SHOW-POSE-RETRY.stp"]
    hashes = {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(inputs)}
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"version_sha256": version, "python": platform.python_version(),
            "settings": SETTINGS, "library_count": len(manifest), "sha256": hashes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    current = snapshot()
    if args.verify:
        previous = json.loads(args.record.read_text(encoding="utf-8"))
        if previous != current:
            changes = set(previous["sha256"].items()) ^ set(current["sha256"].items())
            raise SystemExit(f"Freeze mismatch: {sorted({key for key, _ in changes})}")
        print("Freeze verified:", current["version_sha256"])
    else:
        if args.record.exists():
            raise SystemExit("Freeze record exists. Create a new record to preserve previous evidence.")
        write_json(args.record, current)
        print("Freeze created:", current["version_sha256"])
