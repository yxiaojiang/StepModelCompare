"""Single-solid STEP ingestion and GLB export. Calculation coordinates are millimetres."""

import io
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import igl
import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.collections import Sequence_TCollection_AsciiString
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

ROOT = Path(__file__).resolve().parent
SETTINGS = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
if not (SETTINGS["dedup_angle_deg"] < SETTINGS["ambiguity_angle_deg"]
        and SETTINGS["dedup_center_mm"] < SETTINGS["ambiguity_center_mm"]):
    raise ValueError("姿态去重容差必须严于歧义阈值")

# Right-handed Z-up millimetres to right-handed Y-up metres, preserving origin.
CAD_TO_SCENE = np.array([
    [0.001, 0, 0, 0],
    [0, 0, 0.001, 0],
    [0, -0.001, 0, 0],
    [0, 0, 0, 1],
])


def entities(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


@dataclass
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray
    info: dict

    @property
    def fingerprint(self):
        return np.array([*self.info["size_mm"], self.info["area_mm2"], self.info["volume_mm3"]])

    def packed(self):
        output = io.BytesIO()
        np.savez_compressed(output, v=self.vertices, f=self.faces)
        return output.getvalue()

    @classmethod
    def unpack(cls, packed, info):
        with np.load(io.BytesIO(packed), allow_pickle=False) as arrays:
            return cls(arrays["v"], arrays["f"], info)


def read_step(source, name="input.step"):
    reader = STEPControl_Reader()
    # The stream label has no effect on the original filename or file contents.
    if reader.ReadStream("input.step", io.BytesIO(source)) != IFSelect_RetDone:
        raise ValueError(f"{name}：OCCT 无法读取 STEP，文件可能损坏")
    length, angle, solid_angle = (Sequence_TCollection_AsciiString() for _ in range(3))
    reader.FileUnits(length, angle, solid_angle)
    units = [length.Value(i).ToCString() for i in range(1, length.Length() + 1)]
    reader.SetSystemLengthUnit(1.0)
    if reader.NbRootsForTransfer() != 1 or reader.TransferRoots() != 1:
        raise ValueError(f"{name}：需要包含一个可转换的 STEP 根")
    shape = reader.OneShape()
    solids = list(entities(shape, TopAbs_SOLID))
    if shape.ShapeType() != TopAbs_SOLID or len(solids) != 1:
        raise ValueError(f"{name}：当前仅支持单实体零件")
    if not all(BRep_Tool.IsClosed_s(shell) for shell in entities(shape, TopAbs_SHELL)):
        raise ValueError(f"{name}：实体存在未闭合壳")
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError(f"{name}：实体几何完整性检查失败")

    volume, surface = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume, True)
    BRepGProp.SurfaceProperties_s(shape, surface)
    if min(volume.Mass(), surface.Mass()) <= 0:
        raise ValueError(f"{name}：实体面积或体积无效")
    center = np.array(volume.CentreOfMass().Coord())
    inertia = volume.MatrixOfInertia()
    moments = np.array([[inertia.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    _, axes = np.linalg.eigh(moments)
    mesh_builder = BRepMesh_IncrementalMesh(
        shape, SETTINGS["mesh_chord_mm"], False, SETTINGS["mesh_angle_rad"], False
    )
    if not mesh_builder.IsDone():
        raise ValueError(f"{name}：网格离散失败")
    vertices, faces = [], []
    for face_shape in entities(shape, TopAbs_FACE):
        face = TopoDS.Face(face_shape)
        location = TopLoc_Location()
        triangles = BRep_Tool.Triangulation_s(face, location)
        if triangles is None or triangles.NbTriangles() == 0:
            raise ValueError(f"{name}：存在无法离散的面")
        offset = len(vertices)
        transform = location.Transformation()
        vertices.extend(triangles.Node(i).Transformed(transform).Coord()
                        for i in range(1, triangles.NbNodes() + 1))
        for i in range(1, triangles.NbTriangles() + 1):
            a, b, c = (n + offset - 1 for n in triangles.Triangle(i).Get())
            faces.append((a, c, b) if face.Orientation() == TopAbs_REVERSED else (a, b, c))
    vertices, faces = np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int64)
    edges = vertices[faces[:, 1:]] - vertices[faces[:, :1]]
    faces = faces[np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) > 1e-12]
    if not len(faces) or not np.isfinite(vertices).all():
        raise ValueError(f"{name}：网格坐标无效")
    return Mesh(vertices, faces, {
        "step_units": units, "calculation_unit": "mm", "geometry": "有效闭合单实体",
        "size_mm": np.sort(np.ptp((vertices - center) @ axes, axis=0))[::-1].tolist(),
        "area_mm2": surface.Mass(), "volume_mm3": volume.Mass(),
        "centroid_mm": center.tolist(), "principal_axes": axes.tolist(),
        "cad_to_scene": CAD_TO_SCENE.tolist(), "triangles": len(faces),
    })


class Surface:
    def __init__(self, mesh):
        self.mesh = mesh
        self.tree = igl.AABB()
        self.tree.init(mesh.vertices, mesh.faces)
        self.triangles = mesh.vertices[mesh.faces]
        edges = self.triangles[:, 1:] - self.triangles[:, :1]
        crosses = np.cross(edges[:, 0], edges[:, 1])
        twice_areas = np.linalg.norm(crosses, axis=1)
        self.normals = crosses / twice_areas[:, None]
        self.weights = twice_areas / twice_areas.sum()
        self.fit_points = self.sample(SETTINGS["fit_samples"], 33)
        uniform = self.sample(SETTINGS["surface_samples"], 57)
        # Include triangle centres and used vertices to retain mesh coverage at hole walls.
        self.points = np.vstack((uniform, self.triangles.mean(axis=1),
                                 mesh.vertices[np.unique(mesh.faces)]))

    def sample(self, count, seed):
        rng = np.random.default_rng(seed)
        triangles = self.triangles[rng.choice(len(self.triangles), count, p=self.weights)]
        uv = rng.random((count, 2))
        uv[uv.sum(axis=1) > 1] = 1 - uv[uv.sum(axis=1) > 1]
        return triangles[:, 0] + np.sum(uv[:, :, None] * (triangles[:, 1:] - triangles[:, :1]), axis=1)

    def closest(self, points):
        return self.tree.squared_distance(self.mesh.vertices, self.mesh.faces, points)


def export_glb(mesh):
    positions = np.asarray(mesh.vertices @ CAD_TO_SCENE[:3, :3].T, dtype="<f4")
    indices = np.asarray(mesh.faces, dtype="<u4").reshape(-1)
    binary = positions.tobytes() + indices.tobytes()
    gltf = {
        "asset": {"version": "2.0", "generator": "STEP-SIM-003"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": positions.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
            {"bufferView": 1, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
    }
    metadata = json.dumps(gltf, separators=(",", ":")).encode()
    metadata += b" " * (-len(metadata) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(metadata) + len(binary))
            + struct.pack("<I4s", len(metadata), b"JSON") + metadata
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)
