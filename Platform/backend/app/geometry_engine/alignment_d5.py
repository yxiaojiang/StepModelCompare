"""在任务级子进程执行 D5 刚体精排。依赖 NumPy、SciPy 和 libigl，按查询复用 AABB 和采样。"""

from dataclasses import dataclass
from itertools import permutations, product
from typing import TYPE_CHECKING

import igl
import numpy as np
from scipy.spatial.transform import Rotation

from app.compare.rule import AlignmentLimits

if TYPE_CHECKING:
    from app.compare.worker import Mesh

_FIT_POINTS = 320
_ICP_ITERATIONS = 14
_TRIM_FRACTION = 0.90
_STOP_MM = 0.0001
_REFLECTION = np.diag([-1., 1., 1.])


@dataclass(frozen=True)
class _Surface:
    """持有候选或查询的毫米网格、AABB、法向和特征。只在对比子进程中构造。"""

    mesh: "Mesh"
    tree: igl.AABB
    normals: np.ndarray
    points: np.ndarray
    fit_points: np.ndarray
    axes: np.ndarray
    center: np.ndarray
    scene: np.ndarray

    def closest(self, points: np.ndarray):
        """对 points 批量查询曲面最近点、平方距离和面索引；libigl 计算异常向上传播。"""
        return self.tree.squared_distance(self.mesh.vertices, self.mesh.faces, points)


@dataclass(frozen=True)
class _Trial:
    """保存一个候选刚体姿态及双向距离统计，供拟合与安全判定共用。"""

    pose: np.ndarray
    overall: tuple[dict, dict]

    @property
    def key(self) -> tuple[float, ...]:
        """无参数，返回双向 RMS、P95、最大值的各自较大值，用于稳定排序。"""
        return tuple(max(item[name] for item in self.overall) for name in ("rms_mm", "p95_mm", "max_mm"))


def prepare_surface(mesh: "Mesh", payload: dict) -> _Surface:
    """用 mesh 和同版本 payload 初始化曲面与 AABB，返回任务内曲面。

    退化网格、缺采样、无效主轴或场景矩阵时抛 ValueError。
    """
    points = mesh.sample_points
    if points is None or len(points) < 6500:
        raise ValueError("缺少必需的表面采样")
    triangles = mesh.vertices[mesh.faces]
    sides = triangles[:, 1:] - triangles[:, :1]
    normal = np.cross(sides[:, 0], sides[:, 1])
    areas = np.linalg.norm(normal, axis=1)
    if not np.isfinite(areas).all() or (areas <= 1e-12).any():
        raise ValueError("网格包含退化面")
    obb = payload.get("d6_obb") or {}
    center = np.asarray(obb.get("centroid_mm"), dtype=float)
    axes = np.asarray(obb.get("principal_axes"), dtype=float)
    scene = np.asarray((payload.get("cache_refs") or {}).get("glb_coord_matrix"), dtype=float)
    if (center.shape != (3,) or not np.isfinite(center).all() or not _orthogonal(axes)
            or scene.shape != (4, 4) or not np.isfinite(scene).all()
            or not np.array_equal(scene[3], (0, 0, 0, 1))):
        raise ValueError("主轴或场景变换无效")
    # glTF 坐标换算必须是同一量纲的正行列式相似变换，防止叠加引入镜像或剪切。
    scale = np.linalg.norm(scene[:3, 0])
    if not scale or not _orthogonal(scene[:3, :3] / scale):
        raise ValueError("场景旋转或尺度无效")
    tree = igl.AABB()
    tree.init(mesh.vertices, mesh.faces)
    return _Surface(mesh, tree, normal / areas[:, None], points, points[:_FIT_POINTS], axes, center, scene)


def align_surfaces(query: _Surface, candidate: _Surface, limits: AlignmentLimits) -> dict:
    """精排 query 与 candidate，返回双向距离、诊断、门禁和可发布的候选至查询变换。

    两个曲面须由任务进程准备。数值异常向上传播，由任务进程将候选分流为待复核。
    """
    trials = _trials(query, candidate, reflect=False)
    unique = []
    for trial in trials:
        if not any(
            angle <= limits.dedup_angle_deg and shift <= limits.dedup_center_mm
            for angle, shift in (_pose_distance(trial.pose, other.pose, candidate.center) for other in unique)
        ):
            unique.append(trial)
    accepted = [trial for trial in unique if _fits(trial, limits)]
    disagreements = []
    # 在同一候选的采样质心判位移，避免将远离模型的世界坐标原点当成姿态差。
    sample_center = candidate.points.mean(axis=0)
    for i, trial in enumerate(accepted):
        for j, other in enumerate(accepted[:i]):
            angle, shift = _pose_distance(trial.pose, other.pose, sample_center)
            if angle > limits.ambiguity_angle_deg or shift > limits.ambiguity_center_mm:
                disagreements.append({"poses": [j, i], "angle_deg": angle, "centroid_shift_mm": shift})

    best = accepted[0] if accepted else unique[0]
    reason = "SYMMETRY_AMBIGUITY" if disagreements else (None if accepted else "ALIGNMENT_FAILED")
    mirror_overall = None
    if not disagreements and best.key[0] >= limits.mirror_margin_mm:
        # 左手系试探只产生诊断距离，不进入候选姿态集、D5 得分或叠加矩阵。
        mirror = _trials(query, candidate, reflect=True)
        mirror_best = next((trial for trial in mirror if _fits(trial, limits)), None)
        if mirror_best is not None:
            mirror_overall = _directions(mirror_best)
            if (mirror_best.key[0] <= best.key[0] * limits.mirror_rms_ratio
                    and best.key[0] - mirror_best.key[0] >= limits.mirror_margin_mm
                    and mirror_best.key[1] <= best.key[1]):
                reason = "HANDEDNESS_MISMATCH"

    result = {
        "starts": len(trials), "unique_poses": len(unique), "accepted_poses": len(accepted),
        "overall": _directions(best), "mirror_diagnostic": mirror_overall,
        "disagreements": disagreements, "needs_review": reason is not None, "review_reason": reason,
        "transform_mm": None, "scene_matrix": None,
    }
    if reason is None:
        if not _orthogonal(best.pose[:3, :3]):
            raise ValueError("求得的姿态不满足右手系")
        result["transform_mm"] = best.pose.tolist()
        # 候选 GLB 先逆转到候选毫米，再刚体运动，最后映射到查询 GLB。
        result["scene_matrix"] = (query.scene @ best.pose @ np.linalg.inv(candidate.scene)).tolist()
    return result


def _orthogonal(rotation):
    return (rotation.shape == (3, 3) and np.isfinite(rotation).all()
            and np.linalg.det(rotation) > 0
            and np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0))


def _apply(points, pose):
    return points @ pose[:3, :3].T + pose[:3, 3]


def _inverse(points, pose):
    return (points - pose[:3, 3]) @ pose[:3, :3]


def _pose_distance(a, b, center):
    angle = Rotation.from_matrix(a[:3, :3] @ b[:3, :3].T).magnitude()
    return float(np.degrees(angle)), float(np.linalg.norm(_apply(center, a) - _apply(center, b)))


def _seeds(query, candidate, reflect):
    source_axes = candidate.axes @ _REFLECTION if reflect else candidate.axes
    for order in permutations(range(3)):
        for sign in product((-1, 1), repeat=3):
            rotation = (query.axes[:, order] * sign) @ source_axes.T
            if (np.linalg.det(rotation) < 0) == reflect:
                pose = np.eye(4)
                pose[:3, :3] = rotation
                pose[:3, 3] = query.center - rotation @ candidate.center
                yield pose


def _pairs(query, candidate, pose):
    moved = _apply(candidate.fit_points, pose)
    forward, q_idx, fixed = query.closest(moved)
    backward, c_idx, nearest = candidate.closest(_inverse(query.fit_points, pose))
    return (
        (forward, moved, fixed, query.normals[q_idx]),
        (backward, _apply(nearest, pose), query.fit_points, candidate.normals[c_idx] @ pose[:3, :3].T),
    )


def _refine(query, candidate, pose):
    for _ in range(_ICP_ITERATIONS):
        rows = []
        for squared, source, target, normal in _pairs(query, candidate, pose):
            count = max(1, int(len(squared) * _TRIM_FRACTION))
            keep = np.argpartition(squared, count - 1)[:count]
            rows.append((source[keep], target[keep], normal[keep]))
        moved, fixed, normals = (np.vstack(pair) for pair in zip(*rows))
        center = moved.mean(axis=0)
        relative = moved - center
        jacobian = np.column_stack((np.cross(relative, normals), normals))
        residual = np.einsum("ij,ij->i", normals, fixed - moved)
        step, _, rank, _ = np.linalg.lstsq(jacobian, residual, rcond=1e-8)
        if rank < 6:
            # 退化的点到面问题回退点到点 SVD，并纠正符号，保证更新没有反射。
            target_center = fixed.mean(axis=0)
            u, _, vh = np.linalg.svd(relative.T @ (fixed - target_center))
            correction = np.diag([1, 1, np.sign(np.linalg.det(vh.T @ u.T))])
            rotation = vh.T @ correction @ u.T
            shift = target_center - center
        else:
            rotation = Rotation.from_rotvec(step[:3]).as_matrix()
            shift = step[3:]
        delta = np.eye(4)
        delta[:3, :3] = rotation
        delta[:3, 3] = center + shift - rotation @ center
        pose = delta @ pose
        if np.max(np.linalg.norm(relative @ rotation.T - relative + shift, axis=1)) < _STOP_MM:
            break
    return pose


def _summary(squared):
    if not np.isfinite(squared).all():
        raise ValueError("表面距离计算超出数值范围")
    distances = np.sqrt(np.maximum(squared, 0.))
    return {"count": len(distances), "rms_mm": float(np.sqrt(np.mean(np.maximum(squared, 0.)))),
            "p95_mm": float(np.quantile(distances, 0.95)), "max_mm": float(distances.max())}


def _trials(query, candidate, *, reflect):
    trials = []
    for seed in _seeds(query, candidate, reflect):
        pose = _refine(query, candidate, seed)
        forward = query.closest(_apply(candidate.points, pose))[0]
        backward = candidate.closest(_inverse(query.points, pose))[0]
        trials.append(_Trial(pose, (_summary(forward), _summary(backward))))
    return sorted(trials, key=lambda trial: trial.key)


def _fits(trial, limits):
    rms, p95, _ = trial.key
    return rms <= limits.fit_rms_mm and p95 <= limits.fit_p95_mm


def _directions(trial):
    return dict(zip(("candidate_to_query", "query_to_candidate"), trial.overall))
