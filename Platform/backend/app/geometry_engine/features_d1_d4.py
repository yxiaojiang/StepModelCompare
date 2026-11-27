"""提取 D1 至 D4。依赖已转为毫米的 OCCT/XDE 形状与文档，输出可序列化特征。"""

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from math import cos, isfinite, radians
from pathlib import PureWindowsPath
import unicodedata

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.Quantity import Quantity_Color
from OCP.TDataStd import TDataStd_Name, TDataStd_TreeNode
from OCP.TDF import TDF_AttributeIterator
from OCP.TCollection import TCollection_ExtendedString
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import (
    XCAFDoc, XCAFDoc_Datum, XCAFDoc_Dimension, XCAFDoc_DocumentTool, XCAFDoc_GeomTolerance, XCAFDoc_Material,
)
from OCP.collections import (
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher,
    IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher, Sequence_TDF_Label,
)

_SURFACE_TYPES = {
    GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere", GeomAbs_Torus: "torus", GeomAbs_BSplineSurface: "bspline",
}
_ALIGNMENT_COS = cos(radians(1))
_MERGE_MM = 0.05


@dataclass(frozen=True)
class SurfacePatch:
    """保存单个毫米制 B-Rep 面的类型、测量和轴。仅在解析进程单次提取期间使用。"""

    surface: BRepAdaptor_Surface
    kind: str
    area: float
    center: np.ndarray
    origin: np.ndarray | None
    direction: np.ndarray | None
    radius: float | None
    points_inward: bool


def attribute_values(label):
    """遍历 XDE label 上的实际属性，返回迭代器，OCCT 读取异常向上传播。"""
    iterator = TDF_AttributeIterator(label)
    while iterator.More():
        yield iterator.Value()
        iterator.Next()


def material_entries(label):
    """从 XDE 主 label 迭代材料名称及实际指派标志，OCCT 读取异常向上传播。"""
    material_labels = Sequence_TDF_Label()
    XCAFDoc_DocumentTool.MaterialTool_s(label).GetMaterialLabels(material_labels)
    ref_guid = XCAFDoc.MaterialRefGUID_s()
    for item in material_labels:
        attributes = tuple(attribute_values(item))
        material = next((attr for attr in attributes if isinstance(attr, XCAFDoc_Material)), None)
        name = material.GetName() if material is not None else None
        assigned = any(
            isinstance(attr, TDataStd_TreeNode) and attr.ID().IsSame(ref_guid) and attr.HasFirst()
            for attr in attributes
        )
        yield (name.ToCString().strip() if name is not None else "", assigned)


def extract_basic(label, filename: str, registered_material: str | None, obb: dict, holes: dict) -> dict:
    """从 XDE 主 label、原文件名、台账材料、主轴尺寸及孔系生成 D1，OCCT 错误向上传播。"""
    shapes, colors, layers = (Sequence_TDF_Label() for _ in range(3))
    XCAFDoc_DocumentTool.ShapeTool_s(label).GetShapes(shapes)
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(label)
    color_tool.GetColors(colors)
    layer_tool = XCAFDoc_DocumentTool.LayerTool_s(label)
    layer_tool.GetLayerLabels(layers)

    names = {
        _normalized(attr.Get().ToExtString()) for shape in shapes for attr in attribute_values(shape)
        if isinstance(attr, TDataStd_Name)
    }
    names = sorted(name for name in names if name and not name.startswith("=>"))
    palette = set()
    for item in colors:
        color = Quantity_Color()
        if color_tool.GetColor_s(item, color):
            palette.add(Quantity_Color.ColorToHex_s(color).ToCString().lower())
    layer_names = set()
    for item in layers:
        name = TCollection_ExtendedString()
        if layer_tool.GetLayer(item, name) and name.ToExtString().strip():
            layer_names.add(name.ToExtString().strip())
    material = (registered_material or "").strip()
    materials = [material] if material else sorted({name for name, assigned in material_entries(label) if name and assigned})
    return {
        "product_names": names,
        "filename_stem": _normalized(PureWindowsPath(filename).stem),
        "materials": materials,
        "material_source": "REGISTERED" if material else ("XDE" if materials else None),
        "colors_layers": {"layers": sorted(layer_names), "rgb_palette": sorted(palette)},
        "derived_params": {"obb_mm": obb["dims_sorted_mm"], "hole_diameters_mm": holes["diameters_sorted_mm"]},
        "has_material": bool(materials),
        "has_attributes": bool(names or layer_names or palette),
    }


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def extract_volume(shape, topology: dict) -> tuple[dict, GProp_GProps | None]:
    """按 topology 校验闭合实体并累加毫米立方体积，返回 D2 和供主轴分析使用的体积属性。

    存在开壳、坏实体或非法体积时显式返回缺项，原生异常交给进程监督。
    """
    unavailable = {"volume_available": False, "volume_mm3": None}
    count = topology["solid_count"]
    if not count or topology["open_shell_count"] or count != topology["closed_solid_count"]:
        return unavailable, None

    total = GProp_GProps()
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solid = explorer.Current()
        props = GProp_GProps()
        if not BRepCheck_Analyzer(solid).IsValid():
            return unavailable, None
        BRepGProp.VolumeProperties_s(solid, props, True)
        if not isfinite(props.Mass()) or props.Mass() <= 0:
            return unavailable, None
        total.Add(props)
        explorer.Next()
    return {"volume_available": True, "volume_mm3": total.Mass()}, total


def pmi_objects(label):
    """从 XDE 主 label 迭代可读的尺寸、公差和基准对象，不计入纯图形标注。OCCT 读取异常向上传播。"""
    gdt = XCAFDoc_DocumentTool.DimTolTool_s(label)
    for kind, get_labels, attribute_type in (
        ("dimensions", gdt.GetDimensionLabels, XCAFDoc_Dimension),
        ("geometric_tolerances", gdt.GetGeomToleranceLabels, XCAFDoc_GeomTolerance),
        ("datums", gdt.GetDatumLabels, XCAFDoc_Datum),
    ):
        labels = Sequence_TDF_Label()
        get_labels(labels)
        for item in labels:
            attr = next((attr for attr in attribute_values(item) if isinstance(attr, attribute_type)), None)
            obj = attr.GetObject() if attr is not None else None
            if obj is not None:
                if kind == "dimensions" and obj.GetType().name.endswith(("_DimensionPresentation", "_CommonLabel")):
                    continue
                yield kind, obj


def extract_pmi(label) -> dict:
    """从 XDE 主 label 生成 D3，分别收集长度和角度值。OCCT 读取异常向上传播。"""
    lengths, angles = [], []
    counts, histogram = Counter(), Counter()
    for kind, obj in pmi_objects(label):
        counts[kind] += 1
        if kind == "datums":
            histogram["datum"] += 1
            continue
        name = obj.GetType().name.rsplit("Type_", 1)[-1]
        histogram[f"{kind}.{name}"] += 1
        if kind == "geometric_tolerances":
            values = [obj.GetValue()]
        else:
            array = obj.GetValues()
            values = list(array) if array is not None else []
            if name == "Location_None":
                continue
        # OCCT 在 STEP 转入时将角度尺寸换成度。长度和角度的多重集不能混用。
        target = angles if name.endswith("_Angular") else lengths
        target.extend(value for value in values if isfinite(value))
    return {
        "has_pmi": bool(counts), "pmi_count": counts.total(), "type_histogram": dict(sorted(histogram.items())),
        "semantic_counts": {kind: counts[kind] for kind in ("dimensions", "geometric_tolerances", "datums")},
        "numeric_values_mm": sorted(lengths), "numeric_values_deg": sorted(angles),
    }


def extract_surfaces(shape) -> tuple[dict, list[SurfacePatch], list[list[int]], GProp_GProps]:
    """从毫米制 shape 返回 D4、面信息、共边合并分组和面质量属性，OCCT 异常向上传播。"""
    faces = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    areas = dict.fromkeys((*_SURFACE_TYPES.values(), "other"), 0.0)
    total = GProp_GProps()
    patches = []
    for index in range(1, faces.Extent() + 1):
        patch, props = _measure_face(TopoDS.Face(faces.FindKey(index)))
        patches.append(patch)
        areas[patch.kind] += patch.area
        total.Add(props)
    groups = _merge_groups(shape, faces, patches)
    return {
        "total_area_mm2": total.Mass(),
        "type_area_ratio": {kind: area / total.Mass() if total.Mass() > 0 else 0.0 for kind, area in areas.items()},
        "merged_face_count": len(groups),
    }, patches, groups, total


def _measure_face(face) -> tuple[SurfacePatch, GProp_GProps]:
    surface = BRepAdaptor_Surface(face)
    kind = _SURFACE_TYPES.get(surface.GetType(), "other")
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    origin = direction = radius = None
    inward = False
    if kind in ("plane", "cylinder"):
        geometry = surface.Plane() if kind == "plane" else surface.Cylinder()
        origin = np.array(geometry.Location().Coord())
        direction = np.array(geometry.Axis().Direction().Coord())
        if kind == "cylinder":
            radius = geometry.Radius()
            # 间接坐标系的参数法向为径向反向，再叠加面朝向得到实体外法向。
            inward = geometry.Direct() == (face.Orientation() == TopAbs_REVERSED)
    patch = SurfacePatch(surface, kind, props.Mass(), np.array(props.CentreOfMass().Coord()),
                         origin, direction, radius, inward)
    return patch, props


def _merge_groups(shape, faces, patches) -> list[list[int]]:
    adjacency = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndUniqueAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, adjacency)
    parents = list(range(len(patches)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    # 只考察共边面。彼此分离的共面件或同轴孔不因尺寸相同而被归成一个面。
    for edge in range(1, adjacency.Extent() + 1):
        neighbors = [faces.FindIndex(face) - 1 for face in adjacency.FindFromIndex(edge)]
        for a, b in combinations(neighbors, 2):
            first, second = root(a), root(b)
            if first != second and _mergeable(patches[a], patches[b]):
                parents[max(first, second)] = min(first, second)
    groups = {}
    for index in range(len(patches)):
        groups.setdefault(root(index), []).append(index)
    return list(groups.values())


def _mergeable(a: SurfacePatch, b: SurfacePatch) -> bool:
    if a.kind != b.kind or a.direction is None or abs(a.direction @ b.direction) < _ALIGNMENT_COS:
        return False
    if a.kind == "plane":
        return max(abs((a.center - b.origin) @ b.direction), abs((b.center - a.origin) @ a.direction)) <= _MERGE_MM
    if a.points_inward != b.points_inward or abs(a.radius - b.radius) > _MERGE_MM:
        return False
    # 用各面的局部范围比较近似平行的轴，避免轴原点在轴向的位置左右合并判定。
    a_axis = a.origin + ((a.center - a.origin) @ a.direction) * a.direction
    b_axis = b.origin + ((b.center - b.origin) @ b.direction) * b.direction
    return max(np.linalg.norm(np.cross(a_axis - b.origin, b.direction)),
               np.linalg.norm(np.cross(b_axis - a.origin, a.direction))) <= _MERGE_MM
