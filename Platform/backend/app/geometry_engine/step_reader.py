"""在隔离进程中读取 STEP/XDE 和三角网格。依赖 OCP 与 NumPy，模型坐标统一为毫米。"""

from collections import Counter
import io
from pathlib import Path
import re
from time import perf_counter

import numpy as np
from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
from OCP.BRep import BRep_Tool
from OCP.BRepCheck import BRepCheck_NoError, BRepCheck_Shell
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.collections import Sequence_TCollection_AsciiString, Sequence_TDF_Label
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.STEPConstruct import STEPConstruct_ExternRefs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

from app.geometry_engine.feature_versions import INITIAL_FEATURE_VERSION, require_feature_version
from app.geometry_engine.features_d1_d4 import (
    extract_basic, extract_pmi, extract_surfaces, extract_volume, material_entries, pmi_objects,
)
from app.geometry_engine.features_d6_d7 import extract_holes, extract_obb
from app.geometry_engine.mesh_artifacts import CAD_TO_SCENE, write_glb, write_surface_samples
from app.geometry_engine.step_input import ParseLimits, StepError, validate_step_file

_MESH_DEFLECTION_MM = 0.1
_MESH_ANGLE_RAD = 0.35


def read_step(
    path: Path, limits: ParseLimits, mesh_path: Path | None = None, *,
    feature_version: str | None = None, glb_path: Path | None = None, original_filename: str | None = None,
    registered_material: str | None = None,
) -> dict:
    """用 XDE 读取 path，按 limits 熔断，返回协议、单位、属性、拓扑和耗时。

    mesh_path 由进程池提供，指定时尝试生成网格并记录网格诊断。无效文件或面数超限抛 StepError，
    指定 feature_version 时从同一份 B-Rep 生成特征和必需采样网格，可同时写入 glb_path。
    original_filename 和 registered_material 分别表示原件名与台账材料。缺少网格路径或版本未知时抛 ValueError，
    未知原生异常交给进程池隔离并重建。
    """
    if glb_path is not None and feature_version is None:
        raise ValueError("导出 GLB 时必须指定特征版本")
    if feature_version is not None:
        require_feature_version(feature_version)
        if mesh_path is None:
            raise ValueError("提取特征时必须指定网格路径")
    started = perf_counter()
    payload = validate_step_file(path, limits.max_file_size_bytes, read_payload=True)
    reader = STEPCAFControl_Reader()
    reader.SetMatMode(True)
    reader.SetGDTMode(True)
    # 使用字节流支持 Unicode 路径，同时避免记录原始文件名。
    if reader.ReadStream("input.step", io.BytesIO(payload)) != IFSelect_RetDone:
        raise StepError("ERR_INVALID_STEP_FILE")

    basic = reader.ChangeReader()
    # XDE 转换会主动打开外部零件路径。首期仅接收自包含文件，转换前用同一解析器识别引用。
    session = basic.WS()
    if not session.ComputeGraph():
        raise StepError("ERR_STEP_NO_GEOMETRY")
    references = STEPConstruct_ExternRefs(session)
    references.LoadExternRefs()
    if references.NbExternRefs():
        raise StepError("ERR_STEP_EXTERNAL_REFERENCE")
    schemas, protocols, units = _header(basic)
    # 与 Demo 几何基线一致，按 STEP 自带单位换算为毫米后传入 XDE。
    basic.SetSystemLengthUnit(1.0)
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    if not reader.Transfer(document):
        raise StepError("ERR_INVALID_STEP_FILE")

    label = document.Main()
    tool = XCAFDoc_DocumentTool.ShapeTool_s(label)
    roots = Sequence_TDF_Label()
    tool.GetFreeShapes(roots)
    shape = tool.GetOneShape()
    if shape.IsNull() or not roots.Length():
        raise StepError("ERR_STEP_NO_GEOMETRY")
    raw_faces = _count_faces(shape, limits.max_brep_faces)
    topology = _topology(shape)
    pmi = extract_pmi(label) if feature_version is not None else None
    counts = pmi["semantic_counts"] if pmi is not None else Counter(kind for kind, _ in pmi_objects(label))
    result = {
        "schemas": schemas,
        "protocols": protocols,
        "source_units": units,
        "calculation_unit": "mm",
        "root_count": roots.Length(),
        "is_assembly": any(XCAFDoc_ShapeTool.IsAssembly_s(root) for root in roots),
        **topology,
        **_xde_summary(label),
        "semantic_pmi": {kind: counts.get(kind, 0) for kind in ("dimensions", "geometric_tolerances", "datums")},
        "raw_face_count": raw_faces,
        "occt_parse_seconds": perf_counter() - started,
        "mesh_triangles": None,
        "occt_mesh_seconds": None,
    }
    if mesh_path is not None:
        started = perf_counter()
        try:
            vertices, faces = _mesh_arrays(shape, limits.max_mesh_triangles)
            if feature_version is None:
                np.savez(mesh_path, vertices=vertices, faces=faces)
            else:
                write_surface_samples(mesh_path, vertices, faces)
            result["mesh_triangles"] = len(faces)
        except StepError as error:
            if feature_version is not None:
                raise
            # 盘点仍需保留已读到的协议和拓扑，网格失败单列并跳过 ICP。
            result["mesh_error"] = error.code
        result["occt_mesh_seconds"] = perf_counter() - started
    if glb_path is not None:
        write_glb(glb_path, vertices, faces)
    if feature_version is not None:
        extract = _FEATURE_EXTRACTORS[feature_version]
        result["feature_payload"] = extract(
            shape, label, result, pmi, original_filename or path.name, registered_material,
        )
    return result


def _features_v1(shape, label, parsed, pmi, filename, registered_material) -> dict:
    volume, solid_props = extract_volume(shape, parsed)
    surface, patches, groups, surface_props = extract_surfaces(shape)
    obb = extract_obb(shape, solid_props if solid_props is not None else surface_props)
    holes = extract_holes(patches, groups)
    unit_codes = {"millimetre": "MM", "millimeter": "MM", "inch": "INCH", "metre": "M", "meter": "M"}
    units = parsed["source_units"]
    distinct = {unit_codes.get(unit.casefold(), unit.upper()) for unit in units}
    unit = next(iter(distinct)) if len(distinct) == 1 else ("MIXED" if distinct else "UNKNOWN")
    return {
        "feature_version": INITIAL_FEATURE_VERSION, "unit_source": unit, "source_units": units,
        "topology": {
            **{key: parsed[key] for key in ("solid_count", "closed_solid_count", "open_shell_count", "raw_face_count")},
            "is_open_shell": parsed["open_shell_count"] > 0, "merged_face_count": surface["merged_face_count"],
        },
        "d1_basic": extract_basic(label, filename, registered_material, obb, holes), "d2_volume": volume,
        "d3_pmi": pmi, "d4_surface": surface, "d6_obb": obb, "d7_holes": holes,
        "cache_refs": {"glb_coord_matrix": CAD_TO_SCENE},
    }


_FEATURE_EXTRACTORS = {INITIAL_FEATURE_VERSION: _features_v1}


def _header(reader) -> tuple[list[str], list[str], list[str]]:
    header = APIHeaderSection_MakeHeader(reader.StepModel())
    schemas = (
        [header.SchemaIdentifiersValue(i).ToCString() for i in range(1, header.NbSchemaIdentifiers() + 1)]
        if header.HasFs() else []
    )
    protocols = sorted({_classify_schema(schema) for schema in schemas}) or ["UNKNOWN"]
    lengths, angles, solid_angles = (Sequence_TCollection_AsciiString() for _ in range(3))
    reader.FileUnits(lengths, angles, solid_angles)
    return schemas, protocols, [lengths.Value(i).ToCString() for i in range(1, lengths.Length() + 1)]


def _classify_schema(schema: str) -> str:
    name = schema.upper()
    for protocol, prefix in (("242", "AP242"), ("214", "AUTOMOTIVE_DESIGN"), ("203", "CONFIG_CONTROL_DESIGN")):
        if name.startswith(prefix) or re.search(rf"\b(?:AP\s*|10303[ -]+){protocol}\b", name):
            return f"AP{protocol}"
    return "OTHER"


def _entities(shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def _count_faces(shape, limit: int) -> int:
    count = 0
    for count, _ in enumerate(_entities(shape, TopAbs_FACE), 1):
        if count > limit:
            raise StepError("ERR_MODEL_TOO_COMPLEX")
    return count


def _is_closed_shell(shape) -> bool:
    return BRepCheck_Shell(TopoDS.Shell(shape)).Closed() == BRepCheck_NoError


def _topology(shape) -> dict:
    closed_solids = solid_count = 0
    for solid in _entities(shape, TopAbs_SOLID):
        solid_count += 1
        shells = list(_entities(solid, TopAbs_SHELL))
        closed_solids += bool(shells) and all(_is_closed_shell(shell) for shell in shells)
    closed_shells = open_shells = 0
    for shell in _entities(shape, TopAbs_SHELL):
        if _is_closed_shell(shell):
            closed_shells += 1
        else:
            open_shells += 1
    return {
        "solid_count": solid_count,
        "closed_solid_count": closed_solids,
        "closed_shell_count": closed_shells,
        "open_shell_count": open_shells,
    }


def _xde_summary(label) -> dict:
    materials = list(material_entries(label))
    colors, layers = Sequence_TDF_Label(), Sequence_TDF_Label()
    XCAFDoc_DocumentTool.ColorTool_s(label).GetColors(colors)
    XCAFDoc_DocumentTool.LayerTool_s(label).GetLayerLabels(layers)
    return {
        "xde_material_definitions": len(materials),
        "xde_readable_materials": sum(bool(name) for name, _ in materials),
        "xde_assigned_materials": sum(bool(name) and assigned for name, assigned in materials),
        "xde_color_definitions": colors.Length(),
        "xde_layer_definitions": layers.Length(),
    }


def _mesh_arrays(shape, triangle_limit: int) -> tuple[np.ndarray, np.ndarray]:
    mesh = BRepMesh_IncrementalMesh(shape, _MESH_DEFLECTION_MM, False, _MESH_ANGLE_RAD, False)
    if not mesh.IsDone():
        raise StepError("ERR_STEP_MESH_FAILED")

    vertices, triangles = [], []
    for subshape in _entities(shape, TopAbs_FACE):
        face = TopoDS.Face(subshape)
        placement = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face, placement)
        if poly is None or not poly.NbTriangles():
            raise StepError("ERR_STEP_MESH_INCOMPLETE")
        if len(triangles) + poly.NbTriangles() > triangle_limit:
            raise StepError("ERR_MODEL_TOO_COMPLEX")
        offset = len(vertices)
        transform = placement.Transformation()
        vertices.extend(poly.Node(i).Transformed(transform).Coord() for i in range(1, poly.NbNodes() + 1))
        reversed_face = face.Orientation() == TopAbs_REVERSED
        for i in range(1, poly.NbTriangles() + 1):
            a, b, c = (index + offset - 1 for index in poly.Triangle(i).Get())
            triangles.append((a, c, b) if reversed_face else (a, b, c))

    if not triangles:
        raise StepError("ERR_STEP_MESH_EMPTY")
    points = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(triangles, dtype=np.int64)
    if not np.isfinite(points).all():
        raise StepError("ERR_STEP_MESH_FAILED")
    edges = points[faces[:, 1:]] - points[faces[:, :1]]
    faces = faces[np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) > 1e-12]
    if not len(faces):
        raise StepError("ERR_STEP_MESH_EMPTY")
    return points, faces
