"""监督单次对比任务。借用常驻 OCCT 池，并在独立计算进程内用 NumPy 与 libigl 精排。"""

from collections.abc import Callable, Iterable, Mapping
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
    from sqlalchemy.engine import Engine

    from app.compare.rule import ComparisonRule

    from app.config import Settings
    from app.geometry_engine.worker_runner import OcctPool


@dataclass(frozen=True)
class Mesh:
    """持有毫米坐标和面索引的只读 NumPy 数组，由计算进程按需加载。"""

    vertices: "ndarray"
    faces: "ndarray"
    sample_points: "ndarray | None" = None


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
        result_path = Path(directory) / "result.json"
        return _supervise(_calculate, (query_mesh, work, refine), deadline, result_path)["items"]


def run_ranked_compare_task(
    settings: "Settings", occt_pool: "OcctPool", engine: "Engine", vault: Path,
    query: Path, candidate_ids: Iterable[int], rule: "ComparisonRule", *,
    top_n: int | None = None, feature_version: str | None = None, query_payload: dict | None = None,
    query_glb_target: Path | None = None,
) -> dict:
    """用单个版本精排已授权 candidate_ids，返回可靠榜、待复核、剪枝和截断诊断。

    query 是受信暂存 STEP；传入 query_payload 时 query 为该版本受信 NPZ，可按新规则直接重算。
    engine 提供版本与候选，vault 解析缓存路径，rule 是本次规则。top_n 和 feature_version 可覆写
    本次名额和已固定的版本快照。query_glb_target 可在解析 STEP 时一并导出预览；复用 NPZ 时不可指定。
    版本或参数不符抛 ValueError，时限或进程故障抛 CompareTaskError，
    STEP 读取失败抛 StepError，数据库与存储故障向上传播。
    """
    deadline = monotonic() + settings.compare_job_timeout
    from app.compare.rule import ComparisonRule
    from app.db.model_features import active_feature_version, load_ready_features, vault_path
    from app.geometry_engine.feature_versions import require_feature_version

    wanted = tuple(dict.fromkeys(candidate_ids))
    top_n = settings.default_top_n if top_n is None else top_n
    if type(top_n) is not int or top_n < 1:
        raise ValueError("可靠榜名额须为正整数")
    if query_payload is not None and query_glb_target is not None:
        raise ValueError("复用查询网格时不能生成查询预览")
    # 可变字典字段在派发前重新验证并复制，任务不受运行中的规则修改影响。
    snapshot = ComparisonRule.model_validate(rule.model_dump()).model_dump()
    with engine.connect() as connection:
        version = active_feature_version(connection) if feature_version is None else feature_version
        require_feature_version(version)
        rows = {}
        # 按批查询授权 ID，避免大候选库触发数据库绑定参数上限。所有批次继续使用同一版本。
        for start in range(0, len(wanted), 512):
            rows.update((row.model_id, row) for row in load_ready_features(
                connection, wanted[start:start + 512], version,
            ))
            _check_deadline(deadline)
    _check_deadline(deadline)

    candidates = []
    for model_id in wanted:
        row = rows.get(model_id)
        path = None
        if row is not None:
            try:
                path = str(vault_path(vault, row.sample_points_path))
            except (ValueError, OSError):
                pass
        candidates.append({
            "model_id": model_id, "mesh_path": path,
            "feature_payload": row.feature_payload if row is not None else None,
        })
    _check_deadline(deadline)

    with TemporaryDirectory(prefix="step-compare-") as directory:
        folder = Path(directory)
        query_mesh = folder / "query.npz"
        if query_payload is None:
            try:
                query_payload = occt_pool.extract_features(
                    query, feature_version=version, mesh_target=query_mesh, glb_target=query_glb_target,
                    deadline=deadline,
                )
            except StepError:
                _check_deadline(deadline)
                raise
        else:
            query_mesh = query
        if query_payload.get("feature_version") != version:
            raise ValueError("查询特征版本与任务快照不一致")

        # 大候选特征经本地暂存传递，避免在启动参数中复制全库负载。
        work = folder / "ranking.json"
        work.write_text(json.dumps({
            "query_payload": query_payload, "query_mesh": str(query_mesh.absolute()),
            "candidates": candidates, "rule": snapshot, "top_n": top_n, "budget": settings.max_refine_k,
        }, allow_nan=False), encoding="utf-8")
        _check_deadline(deadline)
        return _supervise(_calculate_ranked, (work,), deadline, folder / "result.json")


# ---- 父进程时限与资源回收 ----

def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise CompareTaskError("ERR_COMPARE_TIMEOUT")


def _supervise(target, args, deadline, result_path) -> dict:
    context = mp.get_context("spawn")
    process = context.Process(target=target, args=(*args, result_path), daemon=True)
    try:
        try:
            process.start()
        except Exception:
            raise CompareTaskError("ERR_COMPARE_WORKER_START") from None
        while process.exitcode is None:
            _check_deadline(deadline)
            # Windows 定时等待可能比单调时钟截止略早返回，仍存活的子进程须继续等待。
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
        return result
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
    _calculate_to_file(_refine_candidates, (query_mesh, candidates, refine), result_path)


def _calculate_ranked(work: Path, result_path: Path) -> None:
    _calculate_to_file(_ranked_result, (work,), result_path)


def _calculate_to_file(calculate, args, result_path):
    os.environ.pop("STEP_PLATFORM_DATABASE_URL", None)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    try:
        payload = json.dumps(calculate(*args), allow_nan=False)
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


def _ranked_result(work: Path) -> dict:
    import numpy as np

    from app.compare.ranking import rank_candidates
    from app.compare.rule import ComparisonRule
    from app.geometry_engine.alignment_d5 import align_surfaces, prepare_surface

    data = json.loads(work.read_text(encoding="utf-8"))
    query_feature = data["query_payload"]
    query_mesh = _load_mesh(Path(data["query_mesh"]), with_samples=True)
    if query_mesh is None:
        return {"error_code": "ERR_COMPARE_QUERY_MESH"}
    try:
        query = prepare_surface(query_mesh, query_feature)
    except (ValueError, TypeError, np.linalg.LinAlgError):
        return {"error_code": "ERR_COMPARE_QUERY_GEOMETRY"}
    rule = ComparisonRule.model_validate(data["rule"])

    def refine(candidate):
        path = candidate["mesh_path"]
        mesh = _load_mesh(Path(path), with_samples=True) if path else None
        if mesh is None:
            return {
                "needs_review": True, "review_reason": "ERR_COMPARE_CANDIDATE_MESH",
                "transform_mm": None, "scene_matrix": None,
            }
        try:
            surface = prepare_surface(mesh, candidate["feature_payload"])
            return align_surfaces(query, surface, rule.safety)
        except (ValueError, TypeError, np.linalg.LinAlgError):
            return {
                "needs_review": True, "review_reason": "ERR_COMPARE_CANDIDATE_GEOMETRY",
                "transform_mm": None, "scene_matrix": None,
            }

    return rank_candidates(
        query_feature, data["candidates"], rule, top_n=data["top_n"], budget=data["budget"], refine=refine,
    )


def _load_mesh(path: Path, *, with_samples: bool = False) -> Mesh | None:
    import numpy as np

    try:
        archive = np.load(path, allow_pickle=False)
        if isinstance(archive, np.ndarray):
            return None
        with archive:
            vertices, faces = archive["vertices"], archive["faces"]
            samples = archive["sample_points"] if with_samples else None
    except (OSError, ValueError, KeyError, EOFError, BadZipFile):
        return None
    if (
        vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3
        or vertices.dtype.kind != "f" or not np.isfinite(vertices).all()
        or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or faces.dtype.kind not in "iu"
        or int(faces.min()) < 0 or int(faces.max()) >= len(vertices)
    ):
        return None
    if with_samples and (
        samples.ndim != 2 or samples.shape[1] != 3 or not 6500 <= len(samples) <= 6500 + len(faces) + len(vertices)
        or samples.dtype.kind != "f" or not np.isfinite(samples).all()
    ):
        return None
    if samples is not None:
        samples = np.ascontiguousarray(samples, dtype=np.float64)
        samples.setflags(write=False)
    vertices = np.ascontiguousarray(vertices, dtype=np.float64)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    vertices.setflags(write=False)
    faces.setflags(write=False)
    return Mesh(vertices, faces, samples)
