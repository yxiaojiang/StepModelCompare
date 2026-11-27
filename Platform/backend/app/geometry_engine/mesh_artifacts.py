"""用 NumPy 网格生成确定性采样与 GLB。仅在第一层 OCCT 子进程写入任务暂存目录。"""

import json
from pathlib import Path
import struct

import numpy as np

from app.geometry_engine.step_input import StepError

# 沿用 Demo 的右手系 Z 向上毫米到右手系 Y 向上米，保留模型原点。
CAD_TO_SCENE = (
    (0.001, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.001, 0.0),
    (0.0, -0.001, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def write_surface_samples(target: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    """将毫米网格和固定种子的面积加权采样写入 target，无返回值，I/O 异常向上传播。"""
    triangles = vertices[faces]
    edges = triangles[:, 1:] - triangles[:, :1]
    areas = np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1)
    if not np.isfinite(areas.sum()) or areas.sum() <= 0:
        raise StepError("ERR_STEP_MESH_FAILED")
    rng = np.random.default_rng(57)
    selected = rng.choice(len(faces), 6500, p=areas / areas.sum())
    uv = rng.random((len(selected), 2))
    flip = uv.sum(axis=1) > 1
    uv[flip] = 1 - uv[flip]
    sampled = triangles[selected, 0] + (uv[:, :, None] * edges[selected]).sum(axis=1)
    points = np.vstack((sampled, triangles.mean(axis=1), vertices[np.unique(faces)]))
    np.savez(target, vertices=vertices, faces=faces, sample_points=points)


def write_glb(target: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    """将毫米网格 vertices、faces 转为 glTF 2.0 GLB 并写入 target，无返回值。

    网格超过 GLB 的坐标精度范围时抛 StepError，I/O 异常向上传播。
    """
    used, indices = np.unique(faces, return_inverse=True)
    positions = np.asarray(vertices[used] @ np.asarray(CAD_TO_SCENE)[:3, :3].T, dtype="<f4")
    indices = np.asarray(indices, dtype="<u4").ravel()
    if not np.isfinite(positions).all():
        raise StepError("ERR_STEP_GLB_FAILED")
    binary_size = positions.nbytes + indices.nbytes
    gltf = {
        "asset": {"version": "2.0", "generator": "STEP Model Compare"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"doubleSided": True, "pbrMetallicRoughness": {"metallicFactor": 0}}],
        "buffers": [{"byteLength": binary_size}],
        "bufferViews": [
            {"buffer": 0, "byteLength": positions.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": 5125, "count": len(indices), "type": "SCALAR",
             "min": [int(indices.min())], "max": [int(indices.max())]},
        ],
    }
    metadata = json.dumps(gltf, separators=(",", ":"), allow_nan=False).encode("utf-8")
    metadata += b" " * (-len(metadata) % 4)
    with target.open("wb") as out:
        out.write(struct.pack("<4sII", b"glTF", 2, 28 + len(metadata) + binary_size))
        out.write(struct.pack("<I4s", len(metadata), b"JSON"))
        out.write(metadata)
        out.write(struct.pack("<I4s", binary_size, b"BIN\0"))
        positions.tofile(out)
        indices.tofile(out)
