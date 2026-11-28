"""验证真实 libigl 几何门禁。使用 Demo 对称件及合成手性网格、拆分表面。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from app.compare.rule import AlignmentLimits
from app.compare.worker import _load_mesh
from app.geometry_engine.alignment_d5 import align_surfaces, prepare_surface
from app.geometry_engine.mesh_artifacts import CAD_TO_SCENE, write_surface_samples
from app.geometry_engine.step_input import ParseLimits
from app.geometry_engine.worker_runner import OcctPool

_DEMO = Path(__file__).resolve().parents[3] / "Demo/samples/rehearsal"


def _surface(path, vertices, faces):
    center = vertices[:4].mean(axis=0)
    _, axes = np.linalg.eigh(np.cov((vertices[:4] - center).T))
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    payload = {"d6_obb": {"centroid_mm": center, "principal_axes": axes},
               "cache_refs": {"glb_coord_matrix": CAD_TO_SCENE}}
    write_surface_samples(path, vertices, faces)
    return prepare_surface(_load_mesh(path, with_samples=True), payload)


class AlignmentTests(unittest.TestCase):
    """用实际距离树验证可发布姿态和两类安全分流。"""

    def test_mirror_and_split_surface(self):
        """同一曲面的拆分网格仍贴合，镜像只能形成诊断，不能发布姿态。"""
        vertices = np.array([[0., 0., 0.], [70., 0., 0.], [8., 38., 0.], [5., 9., 20.]])
        faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
        a, b, c = faces[0]
        subdivided = np.vstack((vertices, vertices[[a, b, c]].mean(axis=0)))
        split_faces = np.vstack((faces[1:], [[a, b, 4], [b, c, 4], [c, a, 4]]))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            base = _surface(root / "base.npz", vertices, faces)
            split = _surface(root / "split.npz", subdivided, split_faces)
            mirror = _surface(root / "mirror.npz", vertices * [-1, 1, 1], faces[:, [0, 2, 1]])
            same = align_surfaces(base, split, AlignmentLimits())
            self.assertFalse(same["needs_review"])
            self.assertLess(same["overall"]["candidate_to_query"]["rms_mm"], 1e-5)
            reflected = align_surfaces(base, mirror, AlignmentLimits())
            self.assertEqual(reflected["review_reason"], "HANDEDNESS_MISMATCH")
            self.assertLess(reflected["mirror_diagnostic"]["candidate_to_query"]["rms_mm"], 1e-5)
            self.assertIsNone(reflected["transform_mm"])
            self.assertIsNone(reflected["scene_matrix"])

    def test_demo_symmetry_withholds_overlay(self):
        """已知中心对称件有多个可拟合的右手姿态，禁止双色叠加。"""
        with TemporaryDirectory() as temp, OcctPool(ParseLimits(), workers=1) as pool:
            root = Path(temp)
            surfaces = []
            for i, source in enumerate(("queries/SHOW-SYMMETRIC.step", "history/SHOW-S01.step")):
                mesh = root / f"{i}.npz"
                payload = pool.extract_features(_DEMO / source, feature_version="1.0.0", mesh_target=mesh)
                surfaces.append(prepare_surface(_load_mesh(mesh, with_samples=True), payload))
        result = align_surfaces(*surfaces, AlignmentLimits())
        self.assertEqual(result["review_reason"], "SYMMETRY_AMBIGUITY")
        self.assertGreaterEqual(result["accepted_poses"], 2)
        self.assertIsNone(result["scene_matrix"])
