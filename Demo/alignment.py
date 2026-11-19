"""Geometry-only multistart rigid registration and shared surface measurements."""

from itertools import permutations, product

import numpy as np
from scipy.spatial.transform import Rotation

from geometry import SETTINGS


def apply(points, pose):
    return points @ pose[:3, :3].T + pose[:3, 3]


def distance(a, b, center):
    angle = Rotation.from_matrix(a[:3, :3] @ b[:3, :3].T).magnitude()
    return float(np.degrees(angle)), float(np.linalg.norm(apply(center, a) - apply(center, b)))


def seeds(new, old):
    new_axes = np.asarray(new.mesh.info["principal_axes"])
    old_axes = np.asarray(old.mesh.info["principal_axes"])
    new_center = np.asarray(new.mesh.info["centroid_mm"])
    old_center = np.asarray(old.mesh.info["centroid_mm"])
    for order in permutations(range(3)):
        for sign in product((-1, 1), repeat=3):
            rotation = (new_axes[:, order] * sign) @ old_axes.T
            if np.linalg.det(rotation) > 0:
                pose = np.eye(4)
                pose[:3, :3] = rotation
                pose[:3, 3] = new_center - rotation @ old_center
                yield pose


def correspondences(new, old, pose, new_points, old_points):
    rot = pose[:3, :3]
    old_moved = apply(old_points, pose)
    old_in_new_sq, new_faces, new_nearest = new.closest(old_moved)
    new_in_old_sq, old_faces, old_nearest = old.closest((new_points - pose[:3, 3]) @ rot)
    return (old_in_new_sq, old_moved, new_nearest, new.normals[new_faces]), (
        new_in_old_sq, apply(old_nearest, pose), new_points, old.normals[old_faces] @ rot.T
    )


def refine(new, old, pose):
    for _ in range(SETTINGS["icp_iterations"]):
        pairs = correspondences(new, old, pose, new.fit_points, old.fit_points)
        moved, fixed, normals = [], [], []
        for squared, source, target, normal in pairs:
            count = int(len(squared) * SETTINGS["icp_trim_fraction"])
            keep = np.argpartition(squared, count - 1)[:count]
            moved.append(source[keep])
            fixed.append(target[keep])
            normals.append(normal[keep])
        moved, fixed, normals = map(np.vstack, (moved, fixed, normals))
        center = moved.mean(axis=0)
        relative = moved - center
        jacobian = np.column_stack((np.cross(relative, normals), normals))
        residual = np.einsum("ij,ij->i", normals, fixed - moved)
        step, _, rank, _ = np.linalg.lstsq(jacobian, residual, rcond=1e-8)
        if rank < 6:
            # Point-to-point update if planar correspondences leave a free direction.
            a, b = moved - center, fixed - fixed.mean(axis=0)
            u, _, vh = np.linalg.svd(a.T @ b)
            correction = np.diag([1, 1, np.linalg.det(vh.T @ u.T)])
            rotation = vh.T @ correction @ u.T
            shift = fixed.mean(axis=0) - center
        else:
            rotation = Rotation.from_rotvec(step[:3]).as_matrix()
            shift = step[3:]
        delta = np.eye(4)
        delta[:3, :3] = rotation
        delta[:3, 3] = center + shift - rotation @ center
        pose = delta @ pose
        if np.max(np.linalg.norm(relative @ rotation.T - relative + shift, axis=1)) < SETTINGS["icp_stop_mm"]:
            break
    return pose


def summary(distances):
    return {
        "count": len(distances), "rms_mm": float(np.sqrt(np.mean(distances**2))),
        "p95_mm": float(np.quantile(distances, 0.95)), "max_mm": float(distances.max()),
    }


def measurements(new, old, pose):
    forward = np.sqrt(new.closest(apply(old.points, pose))[0])
    backward = np.sqrt(old.closest((new.points - pose[:3, 3]) @ pose[:3, :3])[0])
    return forward, backward


def hole_measurement(new, old, pose, samples, region):
    bounds = np.asarray(region["bounds_mm"])
    sources = (apply(old.points, pose), new.points)
    selected = [(points >= bounds[0]).all(axis=1) & (points <= bounds[1]).all(axis=1)
                for points in sources]
    counts = [int(mask.sum()) for mask in selected]
    value = None
    if min(counts) >= SETTINGS["hole_min_per_direction"]:
        value = max(float(values[mask].max()) for values, mask in zip(samples, selected))
    return {"region": region, "counts": counts, "max_mm": value,
            "status": "ok" if value is not None else "insufficient"}


def compare(new, old, region=None, evidence_path=None):
    """Region annotations are consumed only after the alignment outcome is decided."""
    trials = []
    for start in seeds(new, old):
        pose = refine(new, old, start)
        samples = measurements(new, old, pose)
        directional = tuple(summary(values) for values in samples)
        key = (max(item["rms_mm"] for item in directional),
               max(item["p95_mm"] for item in directional))
        trials.append((key, pose, directional, samples))
    trials.sort(key=lambda trial: trial[0])
    center = old.points.mean(axis=0)
    unique = []
    for trial in trials:
        differences = [distance(trial[1], other[1], center) for other in unique]
        if not any(angle <= SETTINGS["dedup_angle_deg"] and shift <= SETTINGS["dedup_center_mm"]
                   for angle, shift in differences):
            unique.append(trial)

    accepted = [trial for trial in unique if trial[0][0] <= SETTINGS["fit_rms_mm"]
                and trial[0][1] <= SETTINGS["fit_p95_mm"]]
    disagreements = []
    for i, trial in enumerate(accepted):
        for j, other in enumerate(accepted[:i]):
            angle, shift = distance(trial[1], other[1], center)
            if angle > SETTINGS["ambiguity_angle_deg"] or shift > SETTINGS["ambiguity_center_mm"]:
                disagreements.append({"poses": [j, i], "angle_deg": angle, "centroid_shift_mm": shift})

    best = unique[0]
    if not accepted:
        state, reason = "failed", "没有姿态同时满足双向拟合条件"
    elif disagreements:
        best = accepted[0]
        state, reason = "ambiguous", "多个合格姿态的旋转角或质心位置差超过阈值"
    else:
        best = accepted[0]
        state, reason = "aligned", ""
    key, pose, overall, samples = best
    result = {
        "state": state, "reason": reason, "starts": len(trials), "unique": len(unique),
        "accepted": [{"pose_mm": trial[1].tolist(), "overall": trial[2]} for trial in accepted],
        "disagreements": disagreements,
        "overall": {"old_to_new": overall[0], "new_to_old": overall[1]},
        "rank_mm": key[0], "size_delta_mm": (new.mesh.fingerprint[:3] - old.mesh.fingerprint[:3]).tolist(),
        "transform_mm": None, "scene_matrix": None, "hole": None,
    }
    if state == "aligned":
        new_c, old_c = (np.asarray(surface.mesh.info["cad_to_scene"]) for surface in (new, old))
        result["transform_mm"] = pose.tolist()
        result["scene_matrix"] = (new_c @ pose @ np.linalg.inv(old_c)).tolist()
        if region is not None:
            result["hole"] = hole_measurement(new, old, pose, samples, region)
            if evidence_path is not None:
                np.savez_compressed(evidence_path, old_points_in_new_mm=apply(old.points, pose),
                                    new_points_mm=new.points, old_to_new_mm=samples[0],
                                    new_to_old_mm=samples[1])
    return result
