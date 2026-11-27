"""监督单次对比任务。借用常驻 OCCT 池，并在独立计算进程内用 NumPy 与 libigl 精排。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import multiprocessing as mp
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING
from zipfile import BadZipFile

from app.geometry_engine.step_input import StepError

if TYPE_CHECKING:
    from igl import AABB
    from numpy import ndarray

    from app.config import Settings
    from app.geometry_engine.worker_runner import OcctPool


@dataclass(frozen=True)
class Mesh:
    """持有毫米坐标和面索引的只读 NumPy 数组，由计算进程按需加载。"""

    vertices: "ndarray"
    faces: "ndarray"


class CompareTaskError(RuntimeError):
    """报告任务截止、预算或进程故障的固定错误码，供调用层处理。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_Refiner = Callable[[Mesh, "AABB", Mesh], dict]


def run_compare_task(
    settings: "Settings",
    occt_pool: "OcctPool",
    query_step: Path,
    candidates: Mapping[int, Path],
    refine: _Refiner,
) -> list[dict]:
    """解析 query_step 并按 candidates 的模型 ID 到网格路径的顺序执行精排，返回逐候选结果。

    settings 提供总时限和候选上限，occt_pool 由调用者复用。refine 须为可跨进程传递的可信计算函数，
    接收查询网格、查询树和候选网格，返回可序列化为 JSON 的对象。候选由上游完成选取。
    超限、超时或进程故障抛 CompareTaskError，解析或必需网格生成失败抛 StepError，
    本地暂存 I/O 失败抛 OSError。
    """
    # 同一截止值覆盖查询件解析、计算进程启动及全部候选精排，两个阶段不重置预算。
    deadline = monotonic() + settings.compare_job_timeout
    work = tuple(candidates.items())
    if len(work) > settings.max_refine_k:
        raise CompareTaskError("ERR_COMPARE_BUDGET_EXCEEDED")

    with TemporaryDirectory(prefix="step-compare-") as directory:
        query_mesh = Path(directory) / "query.npz"
        try:
            occt_pool.parse(query_step, mesh_target=query_mesh, require_mesh=True, deadline=deadline)
        except StepError:
            _check_deadline(deadline)
            raise
        _check_deadline(deadline)
        if not work:
            return []
        return _supervise(query_mesh, work, refine, deadline, Path(directory) / "result.json")


# ---- 父进程时限与资源回收 ----

def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise CompareTaskError("ERR_COMPARE_TIMEOUT")


def _supervise(query_mesh, candidates, refine, deadline, result_path) -> list[dict]:
    context = mp.get_context("spawn")
    process = context.Process(
        target=_calculate, args=(query_mesh, candidates, refine, result_path), daemon=True,
    )
    try:
        try:
            process.start()
        except Exception:
            raise CompareTaskError("ERR_COMPARE_WORKER_START") from None
        process.join(max(0, deadline - monotonic()))
        _check_deadline(deadline)
        if process.exitcode != 0:
            raise CompareTaskError("ERR_COMPARE_WORKER_EXIT")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CompareTaskError("ERR_COMPARE_WORKER_FAILED") from None
        _check_deadline(deadline)
        if "error_code" in result:
            raise CompareTaskError(result["error_code"])
        return result["items"]
    finally:
        if process.pid is not None:
            if process.is_alive():
                # 原生计算卡死不能靠取消线程回收，必须结束该任务的独立进程。
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            process.join()
        process.close()


# ---- 子进程中的几何计算 ----

def _calculate(query_mesh, candidates, refine, result_path) -> None:
    os.environ.pop("STEP_PLATFORM_DATABASE_URL", None)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    try:
        result = _refine_candidates(query_mesh, candidates, refine)
        payload = json.dumps(result, allow_nan=False)
    except Exception:
        payload = '{"error_code":"ERR_COMPARE_WORKER_FAILED"}'
    # 结果只在所有候选处理完毕后写盘，父进程确认正常退出才读取。
    try:
        result_path.write_text(payload, encoding="utf-8")
    except OSError:
        return


def _refine_candidates(query_path, candidates, refine) -> dict:
    import igl

    query = _load_mesh(query_path)
    if query is None:
        return {"error_code": "ERR_COMPARE_QUERY_MESH"}

    # 整个任务只建立一次查询树，候选逐个加载，避免每次精排复制查询几何或重建索引。
    query_tree = igl.AABB()
    query_tree.init(query.vertices, query.faces)
    items = []
    for model_id, path in candidates:
        candidate = _load_mesh(path)
        if candidate is None:
            items.append({"model_id": model_id, "error_code": "ERR_COMPARE_CANDIDATE_MESH"})
            continue
        measurements = refine(query, query_tree, candidate)
        if not isinstance(measurements, dict):
            raise TypeError("精排函数须返回字典")
        items.append({"model_id": model_id, "measurements": measurements})
    return {"items": items}


def _load_mesh(path: Path) -> Mesh | None:
    import numpy as np

    try:
        archive = np.load(path, allow_pickle=False)
        if isinstance(archive, np.ndarray):
            return None
        with archive:
            vertices, faces = archive["vertices"], archive["faces"]
    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
        return None
    if (
        vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3
        or vertices.dtype.kind != "f" or not np.isfinite(vertices).all()
        or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or faces.dtype.kind not in "iu"
        or int(faces.min()) < 0 or int(faces.max()) >= len(vertices)
    ):
        return None
    vertices = np.ascontiguousarray(vertices, dtype=np.float64)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    vertices.setflags(write=False)
    faces.setflags(write=False)
    return Mesh(vertices, faces)
