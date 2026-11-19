"""Generate reproducible demonstration STEP files with OCCT."""

import argparse
import hashlib
import json

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_SOLID
from scipy.spatial.transform import Rotation

from geometry import ROOT, entities

SAMPLES = ROOT / "samples"
DEMO_SOURCE = "演示样例"


def box(x, y, z, w, h, t):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), w, h, t).Shape()


def bore(x, y, z, r, depth):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, depth).Shape()


def plate(w, h, t, holes, notches=(), pockets=()):
    shape = box(0, 0, 0, w, h, t)
    for x, y, wid, ht in notches:
        shape = BRepAlgoAPI_Cut(shape, box(x, y, -1, wid, ht, t + 2)).Shape()
    for x, y, r in holes:
        shape = BRepAlgoAPI_Cut(shape, bore(x, y, -1, r, t + 2)).Shape()
    for x, y, r in pockets:
        shape = BRepAlgoAPI_Cut(shape, bore(x, y, t - 3.5, r, 4.5)).Shape()
    # Boolean operations can wrap the resulting solid in a single-element compound.
    shape, = entities(shape, TopAbs_SOLID)
    return shape


def transformed(shape, axis, angle, translation):
    transform = gp_Trsf()
    transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), float(np.radians(angle)))
    transform.SetTranslationPart(gp_Vec(*translation))
    rotation = Rotation.from_rotvec(np.radians(angle) * np.array(axis) / np.linalg.norm(axis))
    return BRepBuilderAPI_Transform(shape, transform, True).Shape(), rotation


def write_step(shape, directory, stem, inch=False):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.step"
    writer = STEPControl_Writer()
    if not Interface_Static.SetCVal_s("write.step.unit", "INCH" if inch else "MM"):
        raise RuntimeError("无法设置 STEP 输出单位")
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError(f"STEP 传输失败：{path.name}")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP 写入失败：{path.name}")
    return path


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hole_spec(x, y, dx, radius, height):
    margin = radius + 2
    return {"bounds_mm": [[x - margin, y - margin, -1], [x + dx + margin, y + margin, height + 1]],
            "center_mm": [x + dx / 2, y, height], "size_mm": [dx + 2 * margin, 2 * margin],
            "view_mm": [0, 0, 1], "up_mm": [0, 1, 0]}


def reference_pair(points, rotation, shift):
    return {"old_mm": points, "new_mm": (rotation.apply(points) + shift).tolist(),
            "length_mm": float(np.linalg.norm(np.subtract(points[0], points[1])))}


def make_development():
    directory = SAMPLES / "development"
    holes = [(21, 19, 6), (103, 22, 4), (48, 62, 5)]
    notch, pocket = [(110, 59, 25, 30)], [(93, 53, 11)]
    old = plate(130, 80, 12, holes, notch, pocket)
    moved, rotation = transformed(old, (1, 2, -1), 62, (83, -37, 109))
    changed = plate(130, 80, 12, [(27, 19, 6), *holes[1:]], notch, pocket)
    symmetric = plate(118, 70, 9, [(28, 35, 6), (90, 35, 6)])
    symmetric_moved, _ = transformed(symmetric, (2, 1, 2), -39, (10, 31, -17))
    for shape, name, inch in [
        (old, "DEV-BASE", False), (moved, "DEV-POSE-INCH", True), (changed, "DEV-HOLE", False),
        (symmetric, "DEV-SYMMETRIC", False), (symmetric_moved, "DEV-SYMMETRIC-POSE", False),
    ]:
        write_step(shape, directory, name, inch)
    refs = reference_pair([[0, 0, 0], [130, 0, 0], [0, 80, 0]], rotation, (83, -37, 109))
    write_json(directory / "cases.json", [
        {"id": "pose", "new": "DEV-POSE-INCH", "old": "DEV-BASE", "state": "aligned",
         "references": refs, "max_error_mm": 0.15, "max_rms_mm": 0.05},
        {"id": "hole", "new": "DEV-HOLE", "old": "DEV-BASE", "state": "aligned",
         "hole": hole_spec(21, 19, 6, 6, 12), "hole_range_mm": [5.5, 6.3]},
        {"id": "symmetric", "new": "DEV-SYMMETRIC-POSE", "old": "DEV-SYMMETRIC", "state": "ambiguous"},
    ])


def entry(directory, id, shape, revision=None):
    path = write_step(shape, directory / "history", id)
    return {"id": id, "file": path.relative_to(SAMPLES).as_posix(), "source": DEMO_SOURCE,
            "part_number": id, "revision": revision}


def distractors():
    rows = []
    for i in range(28):
        w, h, t = 80 + (i % 7) * 15, 52 + (i // 7) * 15, 7 + i % 5 * 2
        holes = [(15, 16, 3 + i % 4), (w - 18, h - 18, 5), (w * .54, h * .32, 4)]
        shape = plate(w, h, t, holes, [(w - 15, h - 12, 20, 16)], [(w * .65, h * .66, 7)])
        rows.append(entry(SAMPLES / "common", f"PLATE-{i + 1:03d}", shape, "A" if i % 2 else None))
    return rows


def make_catalogs():
    shared = distractors()
    development = SAMPLES / "development"
    tuning = [{
        "id": id, "file": f"development/{id}.step", "source": DEMO_SOURCE, "revision": "A",
    } for id in ("DEV-BASE", "DEV-SYMMETRIC")]
    for i in range(1, 5):
        shape = plate(130, 80, 12, [(21, 19, 6), (103, 22, 4 + i / 5), (48, 62, 5)],
                      [(110, 59, 25, 30)], [(93, 53, 11)])
        tuning.append(entry(development, f"DEV-VARIANT-{i:02d}", shape))
    write_json(SAMPLES / "development-catalog.json", tuning + shared)

    rehearsal = SAMPLES / "rehearsal"
    queries = rehearsal / "queries"
    pose_holes = [(24, 21, 5.5), (118, 26, 4.5), (61, 70, 6)]
    pose_notch, pose_pocket = [(129, 65, 26, 36)], [(116, 62, 10)]
    pose_base = plate(152, 94, 13, pose_holes, pose_notch, pose_pocket)
    posed, rot = transformed(pose_base, (-2, 1, 3), 73, (-49, 94, 57))
    pose_file = write_step(posed, queries, "SHOW-POSE-INCH", True)
    refs = reference_pair([[0, 0, 0], [152, 0, 0], [0, 94, 0]], rot, (-49, 94, 57))
    hole_holes, hole_notch, hole_pocket = [(20, 19, 5), (74, 53, 6), (93, 18, 4)], [
        (94, 48, 22, 28),
    ], [(54, 37, 9)]
    hole_base = plate(112, 72, 10, hole_holes, hole_notch, hole_pocket)
    hole_changed = plate(112, 72, 10, [(25, 19, 5), *hole_holes[1:]], hole_notch, hole_pocket)
    hole_file = write_step(hole_changed, queries, "SHOW-HOLE")
    symmetric = plate(98, 64, 8, [(24, 32, 5), (49, 32, 4), (74, 32, 5)])
    symmetric_moved, _ = transformed(symmetric, (1, -3, 1), -57, (31, -22, 75))
    sym_file = write_step(symmetric_moved, queries, "SHOW-SYMMETRIC")
    catalog = [entry(rehearsal, id, shape, rev) for id, shape, rev in [
        ("SHOW-P01", pose_base, "B"), ("SHOW-H01", hole_base, "A"), ("SHOW-S01", symmetric, None),
    ]]
    for i in range(1, 5):
        holes = [*pose_holes[:2], (61, 70, 6 + i / 5)]
        catalog.append(entry(rehearsal, f"SHOW-P{i + 1:02d}",
                             plate(152, 94, 13, holes, pose_notch, pose_pocket), "A"))
    for i in range(1, 3):
        holes = [(20, 19, 5), (74, 53, 6 + i / 3), (93, 18, 4)]
        catalog.append(entry(rehearsal, f"SHOW-H{i + 1:02d}",
                             plate(112, 72, 10, holes, hole_notch, hole_pocket), "A"))
    write_json(SAMPLES / "catalog.json", catalog + shared)
    damaged = queries / "SHOW-DAMAGED.stp"
    damaged.write_bytes(b"Demonstration sample: intentionally damaged STEP.\n")
    (queries / "SHOW-POSE-RETRY.stp").write_bytes(pose_file.read_bytes())
    cases = [
        {"id": "pose", "file": pose_file.name, "sha256": digest(pose_file), "target": "SHOW-P01",
         "state": "aligned", "references": refs, "max_error_mm": 0.15, "max_rms_mm": 0.05,
         "known_change": "绕 (-2, 1, 3) 轴旋转 73°，平移 (-49, 94, 57) mm，英寸 STEP"},
        {"id": "hole", "file": hole_file.name, "sha256": digest(hole_file), "target": "SHOW-H01",
         "state": "aligned", "hole": hole_spec(20, 19, 5, 5, 10), "hole_range_mm": [4.5, 5.4],
         "known_change": "直径 10 mm 通孔沿 +X 平移 5 mm"},
        {"id": "symmetric", "file": sym_file.name, "sha256": digest(sym_file), "review_target": "SHOW-S01",
         "state": "ambiguous", "known_change": "中心对称孔组，整体另行旋转和平移"},
        {"id": "damaged", "file": damaged.name, "sha256": digest(damaged), "state": "failed",
         "known_change": "故意损坏的 .stp"},
    ]
    write_json(rehearsal / "cases.json", cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=["development", "catalogs"])
    if parser.parse_args().group == "development":
        make_development()
    else:
        make_catalogs()
