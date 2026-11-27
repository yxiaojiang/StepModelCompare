"""盘点目录中的 STEP 样本并输出 JSON。依赖 OCP、NumPy、SciPy 和 libigl，由命令行独立运行。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

# 允许从任意工作目录直接运行脚本，无须安装平台后端或修改 Demo 虚拟环境。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.geometry_engine.step_input import ParseLimits, STEP_EXTENSIONS, StepError
from app.geometry_engine.worker_runner import OcctPool


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("须输入正整数")
    return number


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 1 STEP 样本盘点")
    parser.add_argument("directory", type=Path, help="递归扫描的样本目录")
    parser.add_argument("--output", type=Path, help="JSON 报告路径，缺省输出到标准输出")
    parser.add_argument("--sample-label", default="未标注来源", help="样本来源说明")
    parser.add_argument("--max-file-bytes", type=_positive, default=ParseLimits.max_file_size_bytes)
    parser.add_argument("--max-brep-faces", type=_positive, default=ParseLimits.max_brep_faces)
    parser.add_argument("--max-mesh-triangles", type=_positive, default=ParseLimits.max_mesh_triangles)
    parser.add_argument("--memory-mib", type=_positive, default=ParseLimits.occt_memory_limit_mb)
    parser.add_argument("--parse-timeout", type=_positive, default=30)
    parser.add_argument("--recycle-after", type=_positive, default=50)
    parser.add_argument("--icp-samples", type=_positive, default=100)
    parser.add_argument("--icp-iterations", type=_positive, default=20)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("样本目录不存在")
    if args.output and args.output.suffix.lower() != ".json":
        parser.error("报告路径须以 .json 结尾")
    return args


def _measure_icp(source: Path, target: Path, samples: int, iterations: int) -> dict:
    import igl
    import numpy as np
    from scipy.linalg import det

    with np.load(source, allow_pickle=False) as a, np.load(target, allow_pickle=False) as b:
        source_vertices, source_faces = a["vertices"], a["faces"]
        target_vertices, target_faces = b["vertices"], b["faces"]
    started = perf_counter()
    rotation, translation = igl.iterative_closest_point(
        source_vertices, source_faces, target_vertices, target_faces,
        num_samples=samples, max_iters=iterations,
    )
    elapsed = perf_counter() - started
    valid = (
        np.isfinite(rotation).all() and np.isfinite(translation).all()
        and np.isclose(det(rotation), 1.0, atol=1e-5)
    )
    return {"seconds": elapsed, "status": "ok" if valid else "invalid_transform"}


def _distribution(values: list[int | float]) -> dict:
    import numpy as np

    stats = {"count": len(values), "min": None, "p50": None, "p95": None, "max": None}
    if values:
        p50, p95 = np.percentile(values, [50, 95])
        stats.update(min=min(values), p50=float(p50), p95=float(p95), max=max(values))
    return stats


def _rate(count: int, denominator: int) -> dict:
    return {
        "count": count, "denominator": denominator,
        "fraction": count / denominator if denominator else None,
    }


def _summary(files: list[dict], pairs: list[dict]) -> dict:
    parsed = [item for item in files if item["status"] == "ok"]
    count = len(files)
    protocols = Counter(protocol for item in files for protocol in item.get("protocols", ["UNKNOWN"]))
    ap242 = [item for item in parsed if "AP242" in item["protocols"]]
    with_pmi = [item for item in parsed if any(item["semantic_pmi"].values())]
    return {
        "scanned": count,
        "parsed": len(parsed),
        "parse_failures": dict(Counter(item["error_code"] for item in files if item["status"] == "failed")),
        "protocols": dict(protocols),
        "xde_material_readable": _rate(sum(item["xde_readable_materials"] > 0 for item in parsed), count),
        "xde_material_assigned": _rate(sum(item["xde_assigned_materials"] > 0 for item in parsed), count),
        "xde_color_or_layer": _rate(
            sum(bool(item["xde_color_definitions"] or item["xde_layer_definitions"]) for item in parsed), count,
        ),
        "semantic_pmi_readable": _rate(len(with_pmi), count),
        "ap242_semantic_pmi_readable": _rate(sum(any(item["semantic_pmi"].values()) for item in ap242), len(ap242)),
        "single_closed_solid": sum(item["closed_solid_count"] == 1 and item["solid_count"] == 1 for item in parsed),
        "multiple_closed_solids": sum(item["closed_solid_count"] > 1 for item in parsed),
        "has_open_shell": sum(item["open_shell_count"] > 0 for item in parsed),
        "contains_nonclosed_solid": sum(item["closed_solid_count"] < item["solid_count"] for item in parsed),
        "has_no_solids_or_shells": sum(
            not (item["solid_count"] or item["closed_shell_count"] or item["open_shell_count"])
            for item in parsed
        ),
        "assemblies": sum(item["is_assembly"] for item in parsed),
        "mesh_failures": dict(Counter(item["mesh_error"] for item in parsed if "mesh_error" in item)),
        "file_size_bytes": _distribution([item["file_size_bytes"] for item in files if item["file_size_bytes"] is not None]),
        "raw_face_count": _distribution([item["raw_face_count"] for item in parsed]),
        "mesh_triangles": _distribution([item["mesh_triangles"] for item in parsed if item["mesh_triangles"] is not None]),
        "occt_parse_seconds": _distribution([item["occt_parse_seconds"] for item in parsed]),
        "occt_mesh_seconds": _distribution([item["occt_mesh_seconds"] for item in parsed]),
        "igl_icp_seconds": _distribution([pair["seconds"] for pair in pairs if pair["status"] == "ok"]),
        "igl_icp_pair_outcomes": dict(Counter(pair["status"] for pair in pairs)),
    }


def _scan(args: argparse.Namespace) -> dict:
    directory = args.directory.resolve()
    paths = sorted(
        (path for path in directory.rglob("*") if path.suffix.lower() in STEP_EXTENSIONS and path.is_file()),
        key=lambda path: (path.as_posix().casefold(), path.as_posix()),
    )
    files, pairs = [], []
    limits = ParseLimits(
        max_file_size_bytes=args.max_file_bytes, max_brep_faces=args.max_brep_faces,
        max_mesh_triangles=args.max_mesh_triangles, occt_memory_limit_mb=args.memory_mib,
    )
    with TemporaryDirectory(prefix="step-inventory-") as temp, OcctPool(
        limits, workers=1, timeout=args.parse_timeout, recycle_after=args.recycle_after,
    ) as pool:
        previous_name = None
        previous_mesh, current_mesh = Path(temp) / "previous.npz", Path(temp) / "current.npz"
        for path in paths:
            name = path.relative_to(directory).as_posix()
            item = {"file": name, "file_size_bytes": None}
            started = perf_counter()
            try:
                item["file_size_bytes"] = path.stat().st_size
                result = pool.parse(path, mesh_target=current_mesh)
            except (StepError, OSError) as error:
                code = error.code if isinstance(error, StepError) else "ERR_STEP_FILE_IO"
                files.append({
                    **item, "status": "failed", "error_code": code,
                    "worker_wall_seconds": perf_counter() - started,
                })
                continue
            files.append({**item, **result, "status": "ok", "worker_wall_seconds": perf_counter() - started})
            if not current_mesh.is_file():
                continue
            if previous_name is not None:
                try:
                    timing = _measure_icp(previous_mesh, current_mesh, args.icp_samples, args.icp_iterations)
                except (RuntimeError, ValueError, OSError):
                    timing = {"status": "failed"}
                pairs.append({"source": previous_name, "target": name, **timing})
            current_mesh.replace(previous_mesh)
            previous_name = name
    return {
        "sample_label": args.sample_label,
        "directory": str(directory),
        "method": {
            "rate_denominator": "全部提交的 STEP 文件，AP242 专项使用已识别 AP242 文件",
            "topology_counts": "闭合实体和开壳可在同一文件中共存，统计项可重叠",
            "mesh": "毫米坐标，线性偏差 0.1 mm，角偏差 0.35 rad",
            "icp": "依文件相对路径排序，对相邻可网格化样本调用一次 igl.iterative_closest_point",
            "icp_samples": args.icp_samples,
            "icp_max_iters": args.icp_iterations,
        },
        "summary": _summary(files, pairs),
        "files": files,
        "icp_pairs": pairs,
    }


def main() -> int:
    """解析命令行参数并写出阶段一报告，成功返回 0，参数错误或写入失败返回非零。"""
    args = _arguments()
    report = json.dumps(_scan(args), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        # Windows 管道默认编码取决于外部环境，统一输出 UTF-8 JSON。
        sys.stdout.buffer.write(report.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
