"""验证有效评分、缺项、上界和阶段 2 的真实 STEP 单任务入口。"""

from copy import deepcopy
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

import numpy as np
from sqlalchemy import create_engine

from app.compare.ranking import rank_candidates
from app.compare.rule import ComparisonRule, DIMENSIONS
from app.compare.scoring import score_features
from app.compare.worker import run_ranked_compare_task
from app.config import Settings
from app.db.models import Base, FeatureCatalog, Model, ModelFeature, User
from app.geometry_engine.worker_runner import OcctPool

_DEMO = Path(__file__).resolve().parents[3] / "Demo/samples/rehearsal"
_VERSION = "1.0.0"


def _rule(**weights):
    return ComparisonRule(weights=dict.fromkeys(DIMENSIONS, 0.) | weights)


def _volume(value=None, **others):
    return {"feature_version": _VERSION, **others,
            "d2_volume": {"volume_available": value is not None, "volume_mm3": value}}


def _candidate(model_id, payload):
    return {"model_id": model_id, "feature_payload": payload}


def _refined(rms=0., reason=None):
    return {"needs_review": reason is not None, "review_reason": reason,
            "overall": {"candidate_to_query": dict(rms_mm=rms, p95_mm=rms, max_mm=rms),
                        "query_to_candidate": dict(rms_mm=rms, p95_mm=rms, max_mm=rms)}}


class ScoreTests(TestCase):
    """验证版本 1 评分、权重和语义缺项，不调用 CAD 引擎。"""

    def test_missing_policy_and_pmi_transfer(self):
        """验证空有效集、单方三种策略、D3 转 D7 和双方零孔的缺证据回退。"""
        query = _volume(100.0)
        missing = _volume()
        card = score_features(missing, missing, _rule(D2=100))
        self.assertIsNone(card.total())
        for policy, expected in (("ZERO", 0.), ("NEUTRAL", 50.), ("REALLOCATE", None)):
            rule = ComparisonRule(weights=_rule(D2=100).weights, one_side_missing=policy)
            card = score_features(query, missing, rule)
            self.assertEqual(card.total(), expected)
            self.assertIn("D2:ONE_SIDE_MISSING_UNVERIFIED", card.warnings)

        holes = {"hole_count": 1, "diameters_sorted_mm": [5.0], "center_distances_sorted_mm": []}
        rule = ComparisonRule(weights=_rule(D3=100).weights, both_missing="D3_TO_D7")
        feature = _volume(d7_holes=holes)
        card = score_features(feature, feature, rule)
        self.assertEqual(card.rows["D3"]["status"], "BOTH_MISSING_TRANSFERRED_TO_D7")
        self.assertEqual((card.rows["D7"]["raw_weight"], card.rows["D7"]["effective_weight"]), (0, 100))
        self.assertEqual(card.total(), 100.)
        hole_free = _volume(d7_holes={"hole_count": 0, "diameters_sorted_mm": [], "center_distances_sorted_mm": []})
        self.assertIsNone(score_features(hole_free, hole_free, rule).total())
        self.assertEqual(score_features(hole_free, hole_free, _rule(D7=100)).total(), 100)

    def test_pmi_unknown_types_and_zeros(self):
        """按 P2-1 剔除未知类型和无法区分的零公差证据，已保存的提取记录保持原样。"""
        unknown = {"has_pmi": True, "pmi_count": 2, "type_histogram": {
            "dimensions.Location_None": 1, "geometric_tolerances.None": 1,
        }, "numeric_values_mm": [0.]}
        known = {"has_pmi": True, "type_histogram": {"dimensions.Size_Diameter": 1}, "numeric_values_mm": [5.]}
        unknown_model, known_model = _volume(d3_pmi=unknown), _volume(d3_pmi=known)
        snapshot = deepcopy(unknown_model)
        card = score_features(known_model, unknown_model, _rule(D3=100))
        self.assertEqual(card.rows["D3"]["status"], "ONE_SIDE_MISSING_UNVERIFIED")
        self.assertEqual(card.total(), 0.)
        self.assertEqual(unknown_model, snapshot)
        pair = {"has_pmi": True, "type_histogram": {
            "dimensions.Size_Diameter": 1, "geometric_tolerances.Position": 1,
        }, "numeric_values_mm": [0., 5.]}
        stable = {**pair, "numeric_values_mm": [5.]}
        with_zero, without_zero = _volume(d3_pmi=pair), _volume(d3_pmi=stable)
        card = score_features(with_zero, without_zero, _rule(D3=100))
        self.assertIn("PMI_ZERO_UNVERIFIED", card.rows["D3"]["data_notes"]["query"])
        self.assertEqual(card.total(), 100.)
        angles = {"has_pmi": True, "type_histogram": {"dimensions.Size_Angular": 1}, "numeric_values_deg": [45.]}
        other = {**angles, "numeric_values_deg": [60.]}
        card = score_features(_volume(d3_pmi=angles), _volume(d3_pmi=other), _rule(D3=100))
        self.assertEqual(card.rows["D3"]["components"]["degree_distribution"], 0.25)
        self.assertNotIn("mm_distribution", card.rows["D3"]["components"])

    def test_d1_material_d4_face_count_and_d6_size(self):
        """验证 D1 单边材料标记、有效面数抗拆面，并区分同体积异形尺寸。"""
        d1 = {"filename_stem": "Plate_19", "materials": ["SUS304"], "derived_params": {"obb_mm": [15, 10, 2]}}
        d4 = {"total_area_mm2": 300, "type_area_ratio": {"plane": 1.}, "merged_face_count": 6}
        a = _volume(300, d1_basic=d1, d4_surface=d4, d6_obb={"dims_sorted_mm": [15, 10, 2]},
                    topology={"raw_face_count": 6})
        b = _volume(300, d1_basic={**d1, "materials": []}, d4_surface=d4,
                    d6_obb={"dims_sorted_mm": [30, 5, 2]}, topology={"raw_face_count": 18})
        card = score_features(a, b, _rule(D1=25, D2=25, D4=25, D6=25))
        self.assertEqual(card.rows["D1"]["components"]["material"]["status"], "ONE_SIDE_MISSING_UNVERIFIED")
        self.assertEqual(card.rows["D2"]["subscore"], 100)
        self.assertEqual(card.rows["D4"]["subscore"], 100)
        self.assertEqual(card.rows["D6"]["subscore"], 0)
        self.assertAlmostEqual(card.total(), sum(row["weighted_score"] for row in card.rows.values()))

    def test_rule_validation(self):
        """拒绝非法总和、非有限阈值与和歧义一样宽松的去重门槛。"""
        with self.assertRaises(ValueError):
            _rule(D1=99)
        with self.assertRaises(ValueError):
            ComparisonRule(weights=_rule(D5=100).weights, safety={"dedup_angle_deg": 6.})
        with self.assertRaises(ValueError):
            ComparisonRule(weights=_rule(D5=100).weights, thresholds={"rms_mm": float("nan")})


class PruningTests(TestCase):
    """用精排桩验证上界、榜尾、预算和安全互相独立。"""

    def test_upper_bound_after_missing_reweight(self):
        """D2 双方缺失后不同候选的 D5 有效权重不同，高上界候选不会被误剪。"""
        d4 = {"total_area_mm2": 100, "type_area_ratio": {"plane": 1.}, "merged_face_count": 6}
        query = _volume(d4_surface=d4)
        less_fit = _volume(100, d4_surface=d4)
        hidden_winner = _volume(d4_surface=d4)
        far = _volume(100, d4_surface={**d4, "total_area_mm2": 1000})
        candidates = [_candidate(1, less_fit), _candidate(2, hidden_winner), _candidate(3, far)]
        rule = _rule(D2=80, D4=10, D5=10)
        refine = Mock(side_effect=lambda row: _refined())
        result = rank_candidates(query, candidates, rule, top_n=1, budget=3, refine=refine)
        self.assertEqual(result["results"][0]["model_id"], 2)
        self.assertEqual(result["results"][0]["breakdown"]["D5"]["effective_weight"], 50.0)
        self.assertEqual(result["termination"], "exact_pruned")
        self.assertEqual(result["pruned_model_ids"], [1, 3])
        self.assertEqual(refine.call_count, 1)

    def test_reliable_floor_ties_and_budget(self):
        """同分仍检查 ID 排序，待复核不抬高榜尾，预算截断记录剩余上界。"""
        candidates = [_candidate(3, _volume(100)), _candidate(1, _volume(100)),
                      _candidate(2, _volume(100)), _candidate(9, None)]
        refine = Mock(side_effect=lambda row: _refined(
            rms=0.2, reason="SYMMETRY_AMBIGUITY" if row["model_id"] == 1 else None,
        ))
        result = rank_candidates(_volume(100), candidates, _rule(D5=100), top_n=1, budget=2, refine=refine)
        self.assertEqual([row["model_id"] for row in result["results"]], [2])
        self.assertEqual([row["model_id"] for row in result["needs_review"]], [1, 9])
        self.assertEqual(result["termination"], "truncated_by_k")
        self.assertEqual(result["max_untested_upper"], 100.)
        self.assertEqual(result["truncated_model_ids"], [3])

    def test_equal_bound_uses_final_sort_key(self):
        """确认同分较大 ID 可精确剪，较小 ID 即使快速几何次序较后仍要精排。"""
        query = _volume(1.0)
        candidates = [_candidate(3, _volume(1.0)), _candidate(1, _volume())]
        result = rank_candidates(
            query, candidates, _rule(D5=100), top_n=1, budget=1, refine=lambda row: _refined(),
        )
        self.assertEqual(result["results"][0]["model_id"], 1)
        self.assertEqual(result["pruned_model_ids"], [3])
        self.assertEqual(result["termination"], "exact_pruned")

        query = _volume(1., d1_basic={"filename_stem": "alpha"})
        candidates = [
            _candidate(3, _volume(1., d1_basic={"filename_stem": "zzzz"})),
            _candidate(1, _volume(0.1, d1_basic={"filename_stem": "alpha"})),
        ]
        rule = _rule(D1=25, D2=25, D5=50)
        result = rank_candidates(query, candidates, rule, top_n=1, budget=1, refine=lambda row: _refined())
        self.assertEqual(result["results"][0]["model_id"], 3)
        self.assertEqual(result["truncated_model_ids"], [1])
        self.assertEqual(result["max_untested_upper"], 75.)
        result = rank_candidates(query, candidates, rule, top_n=1, budget=2, refine=lambda row: _refined())
        self.assertEqual(result["results"][0]["model_id"], 1)

    def test_pruned_matches_exhaustive_reference(self):
        """用固定随机折算、拟合分与不合格姿态验证精确榜单等于全量基准。"""
        rng = Random(311)
        for _ in range(40):
            cutoff = rng.randint(0, 100)
            rule = ComparisonRule(weights=_rule(D2=cutoff, D5=100 - cutoff).weights,
                                  one_side_missing=rng.choice(("ZERO", "NEUTRAL", "REALLOCATE")))
            query = _volume(rng.choice((100., 120., None)))
            candidates = [_candidate(i, _volume(rng.choice((100., 110., 120., None)))) for i in range(1, 21)]
            measurements = {row["model_id"]: _refined(
                rms=rng.choice((0., 0.03, 0.2, 0.6)), reason=rng.choice((None, None, "ALIGNMENT_FAILED")),
            ) for row in candidates}
            rng.shuffle(candidates)
            expected = []
            for row in candidates:
                card = score_features(query, row["feature_payload"], rule)
                model_id = row["model_id"]
                if card.total() is None or measurements[model_id]["needs_review"]:
                    continue
                card.apply_surface(measurements[model_id], rule)
                expected.append((card.total(), model_id))
            result = rank_candidates(query, candidates, rule, top_n=4, budget=20,
                                     refine=lambda row: measurements[row["model_id"]])
            actual = [(row["similarity_score"], row["model_id"]) for row in result["results"]]
            self.assertEqual(actual, sorted(expected, key=lambda item: (-item[0], item[1]))[:4])

    def test_zero_d5_weight_still_runs_gate(self):
        """禁用 D5 计分时，被拒绝的镜像不入榜，预算未覆盖时显式截断。"""
        candidates = [_candidate(1, _volume(100)), _candidate(2, _volume(100))]
        gate = Mock(side_effect=lambda row: _refined(reason="HANDEDNESS_MISMATCH" if row["model_id"] == 1 else None))
        result = rank_candidates(_volume(100), candidates, _rule(D2=100), top_n=1, budget=1, refine=gate)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["termination"], "truncated_by_k")
        result = rank_candidates(_volume(100), candidates, _rule(D2=100), top_n=1, budget=2, refine=gate)
        self.assertEqual(result["termination"], "exact_no_d5")
        self.assertEqual(result["results"][0]["model_id"], 2)
        self.assertEqual(result["results"][0]["breakdown"]["D5"]["weighted_score"], 0.)


def _seed_feature(connection, model_id, payload, status="APPROVED"):
    connection.execute(Model.__table__.insert().values(
        id=model_id, uploader_id=1, md5=str(model_id) * 32, model_name="测试件", original_filename="part.step",
        file_size_bytes=1, storage_path="part.step", status=status,
    ))
    connection.execute(ModelFeature.__table__.insert().values(
        model_id=model_id, feature_version=_VERSION, status="READY", feature_payload=payload,
        sample_points_path=f"{model_id}.npz", glb_path=f"{model_id}.glb",
    ))


class RankedWorkerTests(TestCase):
    """实际 OCCT/igl 与 SQLite 替身串通正式任务入口，数据库不用于并发结论。"""

    def test_demo_pose_and_cached_rule_change(self):
        """验证真实英寸件姿态、固定版本、候选诊断和重新加权时免读 STEP。"""
        settings = Settings(occt_pool_size=1, max_refine_k=4)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as temp, OcctPool.from_settings(settings) as pool:
            root = Path(temp)
            query = _DEMO / "queries/SHOW-POSE-INCH.step"
            history = _DEMO / "history/SHOW-P01.step"
            feature = pool.extract_features(history, feature_version=_VERSION, mesh_target=root / "1.npz")
            (root / "2.npz").write_bytes(b"bad")
            with engine.begin() as conn:
                conn.execute(FeatureCatalog.__table__.insert().values(id=1, active_feature_version=_VERSION))
                conn.execute(User.__table__.insert().values(id=1, username="test", password_hash="占位", role="SYS_ADMIN"))
                _seed_feature(conn, 1, feature)
                _seed_feature(conn, 2, feature)
                _seed_feature(conn, 3, feature, status="PENDING")
            result = run_ranked_compare_task(settings, pool, engine, root, query, [3, 2, 1], _rule(D5=100), top_n=2)
            self.assertEqual([row["model_id"] for row in result["results"]], [1])
            self.assertEqual({row["model_id"]: row["review_reason"] for row in result["needs_review"]},
                             {2: "ERR_COMPARE_CANDIDATE_MESH", 3: "CANDIDATE_FEATURE_UNAVAILABLE"})
            measure = result["results"][0]["alignment"]
            case = json.loads((_DEMO / "cases.json").read_text(encoding="utf-8"))[0]
            original, target = (np.array(case["references"][name]) for name in ("old_mm", "new_mm"))
            transform = np.array(measure["transform_mm"])
            self.assertAlmostEqual(np.linalg.det(transform[:3, :3]), 1.)
            transformed = original @ transform[:3, :3].T + transform[:3, 3]
            self.assertLess(np.linalg.norm(transformed - target, axis=1).max(), case["max_error_mm"])
            self.assertEqual(measure["starts"], 24)
            refs = feature["cache_refs"]["glb_coord_matrix"]
            np.testing.assert_allclose(measure["scene_matrix"], refs @ transform @ np.linalg.inv(refs))
            query_mesh = root / "cached.npz"
            query_feature = pool.extract_features(query, feature_version=_VERSION, mesh_target=query_mesh)
            unused = Mock()
            result = run_ranked_compare_task(settings, unused, engine, root, query_mesh, [1], _rule(D2=100),
                                             query_payload=query_feature)
            unused.extract_features.assert_not_called()
            self.assertEqual(result["termination"], "exact_no_d5")
            self.assertGreater(result["results"][0]["similarity_score"], 99.9)
            with self.assertRaisesRegex(ValueError, "版本"):
                run_ranked_compare_task(settings, unused, engine, root, query_mesh, [1], _rule(D2=100),
                                        query_payload={**query_feature, "feature_version": "1.1.0"})
