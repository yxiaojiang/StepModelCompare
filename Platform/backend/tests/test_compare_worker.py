"""回归任务级计算隔离。使用 Demo 几何验证实际 AABB，使用合成故障验证硬截止与回收。"""

from functools import partial
import multiprocessing as mp
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

from app.compare.worker import CompareTaskError, _supervise, run_compare_task
from app.config import Settings
from app.geometry_engine.step_input import ParseLimits, StepError
from app.geometry_engine.worker_runner import OcctPool

_DEMO_SAMPLE = (
    Path(__file__).resolve().parents[3] / "Demo" / "samples" / "rehearsal" / "queries" / "SHOW-POSE-INCH.step"
)


def _write_triangle(path: Path) -> None:
    import numpy as np

    np.savez(
        path,
        vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        faces=np.array([[0, 1, 2]], dtype=np.int64),
    )


def _query_processor(path, _limits, mesh):
    if path.stem == "hang":
        path.with_suffix(".pid").write_text(str(os.getpid()), encoding="ascii")
        sleep(60)
    if path.stem == "slow":
        sleep(1.5)
    if mesh is not None:
        _write_triangle(mesh)
    return {"pid": os.getpid()}


def _distance(query, tree, candidate):
    squared, _, _ = tree.squared_distance(query.vertices, query.faces, candidate.vertices)
    return {"max_squared_distance": float(squared.max()), "pid": os.getpid(), "tree_id": id(tree)}


def _hang_on_second_candidate(query, tree, candidate, *, marker):
    if marker.exists():
        marker.with_suffix(".partial").write_text(str(os.getpid()), encoding="ascii")
        sleep(60)
    marker.write_text(str(os.getpid()), encoding="ascii")
    return _distance(query, tree, candidate)


def _exit_worker(_query, _tree, _candidate):
    os._exit(8)


class CompareWorkerTests(unittest.TestCase):
    """验证常驻 OCCT 与单任务计算进程的衔接及异常隔离，临时产物按测试回收。"""

    def setUp(self):
        """准备本测试专用的输入及缓存目录。"""
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self._folder = Path(storage.name)
        self._query = self._folder / "query.step"
        self._query.write_bytes(b"ISO-10303-21;")
        self._mesh = self._folder / "cache.npz"
        _write_triangle(self._mesh)

    def test_real_geometry_reuses_query_tree_and_preserves_order(self):
        """用真实 OCCT 产物和 AABB 查询验证整批运行，坏缓存有独立诊断。"""
        settings = Settings(max_refine_k=3, occt_pool_size=1)
        cached = self._folder / "demo.npz"
        broken = self._folder / "broken.npz"
        broken.write_bytes(b"broken")
        with OcctPool.from_settings(settings) as pool:
            pool.parse(_DEMO_SAMPLE, mesh_target=cached, require_mesh=True)
            result = run_compare_task(settings, pool, _DEMO_SAMPLE, {3: cached, 2: broken, 1: cached}, _distance)
        self.assertEqual([item["model_id"] for item in result], [3, 2, 1])
        self.assertEqual(result[1]["error_code"], "ERR_COMPARE_CANDIDATE_MESH")
        first, last = result[0]["measurements"], result[2]["measurements"]
        self.assertLess(first["max_squared_distance"], 1e-15)
        self.assertEqual(first, last)
        self.assertNotEqual(first["pid"], os.getpid())

    def test_budget_and_strict_mesh_gate(self):
        """超过 K 直接拒绝，查询三角片超限时整条对比入口传播解析错误。"""
        settings = Settings(max_refine_k=1, max_mesh_triangles=1, occt_pool_size=1)
        with OcctPool.from_settings(settings) as pool:
            with self.assertRaisesRegex(CompareTaskError, "^ERR_COMPARE_BUDGET_EXCEEDED$"):
                run_compare_task(settings, pool, self._folder / "missing.step", {1: self._mesh, 2: self._mesh}, _distance)
            with self.assertRaisesRegex(StepError, "^ERR_MODEL_TOO_COMPLEX$"):
                run_compare_task(settings, pool, _DEMO_SAMPLE, {1: self._mesh}, _distance)

    def test_query_parser_uses_task_deadline(self):
        """任务时限先到时强杀第一层进程，池随后能重新解析。"""
        settings = Settings(compare_job_timeout=1)
        hang = self._folder / "hang.step"
        hang.write_bytes(self._query.read_bytes())
        with OcctPool(ParseLimits(), workers=1, timeout=15, processor=_query_processor) as pool:
            first = pool.parse(self._query)["pid"]
            started = monotonic()
            with self.assertRaisesRegex(CompareTaskError, "^ERR_COMPARE_TIMEOUT$"):
                run_compare_task(settings, pool, hang, {1: self._mesh}, _distance)
            self.assertLess(monotonic() - started, 3)
            self.assertEqual(int(hang.with_suffix(".pid").read_text(encoding="ascii")), first)
            self.assertNotIn(first, {child.pid for child in mp.active_children()})
            self.assertNotEqual(pool.parse(self._query)["pid"], first)

    def test_refinement_counts_query_time_and_discards_partial_results(self):
        """查询与精排共用时限，第二个候选卡死时回收计算进程，整个任务返回超时。"""
        settings = Settings(compare_job_timeout=4)
        slow = self._folder / "slow.step"
        slow.write_bytes(self._query.read_bytes())
        marker = self._folder / "refine.pid"
        refine = partial(_hang_on_second_candidate, marker=marker)
        with OcctPool(ParseLimits(), workers=1, timeout=15, processor=_query_processor) as pool:
            pool.parse(self._query, mesh_target=self._folder / "warmup.npz")
            started = monotonic()
            with self.assertRaisesRegex(CompareTaskError, "^ERR_COMPARE_TIMEOUT$"):
                run_compare_task(settings, pool, slow, {1: self._mesh, 2: self._mesh}, refine)
            self.assertLess(monotonic() - started, 5)
            pid = int(marker.read_text(encoding="ascii"))
            self.assertEqual(int(marker.with_suffix(".partial").read_text(encoding="ascii")), pid)
            self.assertNotIn(pid, {child.pid for child in mp.active_children()})
            subsequent = run_compare_task(settings, pool, self._query, {1: self._mesh}, _distance)
            self.assertNotEqual(subsequent[0]["measurements"]["pid"], pid)

    def test_supervisor_continues_waiting_after_early_join(self):
        """定时等待提前返回且子进程仍存活时，继续等待并读取正常结果。"""
        process = Mock(pid=1, exitcode=None)
        process.is_alive.return_value = False
        result = self._folder / "result.json"
        result.write_text('{"items":[]}', encoding="utf-8")

        def early_join(timeout=None):
            if process.join.call_count == 2:
                process.exitcode = 0

        process.join.side_effect = early_join
        with patch("app.compare.worker.mp.get_context") as context:
            context.return_value.Process.return_value = process
            self.assertEqual(_supervise(_distance, (), monotonic() + 1, result), {"items": []})

    def test_compute_crash_is_reported_and_next_task_runs(self):
        """计算进程异常退出时返回固定错误，之后可继续执行独立对比任务。"""
        settings = Settings()
        with OcctPool(ParseLimits(), workers=1, processor=_query_processor) as pool:
            with self.assertRaisesRegex(CompareTaskError, "^ERR_COMPARE_WORKER_EXIT$"):
                run_compare_task(settings, pool, self._query, {1: self._mesh}, _exit_worker)
            result = run_compare_task(settings, pool, self._query, {1: self._mesh}, _distance)
            self.assertEqual(result[0]["measurements"]["max_squared_distance"], 0.0)
