"""Run the five development STEP files through the production geometry pipeline."""

import json
from time import perf_counter

import numpy as np

from alignment import apply, compare
from geometry import ROOT, Surface, export_glb, read_step
from make_samples import DEMO_SOURCE, SAMPLES, write_json


def run():
    source = SAMPLES / "development"
    output = ROOT / "dist" / "development"
    output.mkdir(parents=True, exist_ok=True)
    cases = json.loads((source / "cases.json").read_text(encoding="utf-8"))
    models = {}
    surfaces = {}
    for path in sorted(source.glob("*.step")):
        started = perf_counter()
        mesh = read_step(path.read_bytes(), path.name)
        glb = export_glb(mesh)
        (output / f"{path.stem}.glb").write_bytes(glb)
        surfaces[path.stem] = Surface(mesh)
        models[path.stem] = {**mesh.info, "source": DEMO_SOURCE, "glb": f"./development/{path.stem}.glb",
                             "seconds": perf_counter() - started, "glb_bytes": len(glb)}
    results = []
    for case in cases:
        new, old = (surfaces[case[key]] for key in ("new", "old"))
        started = perf_counter()
        result = compare(new, old, case.get("hole"),
                         output / "hole-samples.npz" if "hole" in case else None)
        result["seconds"] = perf_counter() - started
        if case.get("references") and result["state"] == "aligned":
            ref = case["references"]
            aligned = apply(np.array(ref["old_mm"]), np.array(result["transform_mm"]))
            result["reference_error_mm"] = np.linalg.norm(aligned - ref["new_mm"], axis=1).tolist()
        results.append({**case, "result": result})
        print(case["id"], result["state"], "accepted", len(result["accepted"]),
              "RMS", round(result["rank_mm"], 5), "sec", round(result["seconds"], 3),
              "hole", (result["hole"] or {}).get("max_mm"), flush=True)
    write_json(output / "results.json", {"models": models, "cases": results})
    for case in results:
        result = case["result"]
        assert result["state"] == case["state"], case["id"]
        if "references" in case:
            assert max(result["reference_error_mm"]) <= case["max_error_mm"]
            assert result["rank_mm"] <= case["max_rms_mm"]
        if "hole" in case:
            assert result["hole"]["status"] == "ok"
            low, high = case["hole_range_mm"]
            assert low <= result["hole"]["max_mm"] <= high
        if case["state"] == "ambiguous":
            assert len(result["accepted"]) >= 2 and result["disagreements"]
            assert result["scene_matrix"] is None


if __name__ == "__main__":
    run()
