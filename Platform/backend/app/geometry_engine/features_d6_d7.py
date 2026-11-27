"""提取 D6 与 D7。依赖 OCCT 毫米制形状、共边圆柱分组与 NumPy，单次提取后释放原生对象。"""

from itertools import combinations
from math import atan2, pi, tau

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.gp import gp_Trsf
from OCP.TopLoc import TopLoc_Location

from app.geometry_engine.features_d1_d4 import SurfacePatch
from app.geometry_engine.step_input import StepError

# 两两孔心距是平方增长的载荷，通过固定版本的上限拦截超大特征。
_MAX_HOLES = 1024
_ANGLE_EPS = 1e-7


def extract_obb(shape, props) -> dict:
    """使用 shape 和同坐标系的体积或表面质量属性 props 返回主轴 OBB。

    沿用惯性主轴，轴上边界按精确 B-Rep 计算。无效边界抛 StepError，OCCT 异常向上传播。
    """
    center = np.array(props.CentreOfMass().Coord())
    inertia = props.MatrixOfInertia()
    moments = np.array([[inertia.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)])
    _, axes = np.linalg.eigh(moments)
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    rotation = axes.T
    offset = -rotation @ center
    transform = gp_Trsf()
    transform.SetValues(*(np.column_stack((rotation, offset)).ravel().tolist()))
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.Moved(TopLoc_Location(transform)), box, False, False)
    if box.IsVoid() or box.IsOpen():
        raise StepError("ERR_STEP_NO_GEOMETRY")
    dims = np.sort(np.array(box.CornerMax().Coord()) - np.array(box.CornerMin().Coord()))[::-1]
    return {
        "dims_sorted_mm": dims.tolist(),
        "ratios": [float(a / b) if b > 1e-9 else None for a, b in zip(dims, dims[1:])],
        "centroid_mm": center.tolist(), "principal_axes": axes.tolist(),
    }


def extract_holes(patches: list[SurfacePatch], groups: list[list[int]]) -> dict:
    """用相邻柱面 patches 及共轴合并 groups 返回内孔孔径、孔心和两两间距。

    满足至少半周角覆盖的内柱面才计入。孔系规模超限抛 StepError，OCCT 异常向上传播。
    """
    holes = []
    for group in groups:
        first = patches[group[0]]
        if not first.points_inward:
            continue
        cylinder = first.surface.Cylinder()
        x = np.array(cylinder.XAxis().Direction().Coord())
        z = first.direction
        y = np.cross(z, x)
        intervals, axial_bounds = [], []
        for index in group:
            patch = patches[index]
            u = patch.surface.FirstUParameter()
            span = min(tau, patch.surface.LastUParameter() - u)
            v = patch.surface.FirstVParameter()
            point = np.array(patch.surface.Value(u, v).Coord())
            radial = point - patch.origin
            angle = atan2(float(radial @ y), float(radial @ x))
            own_cylinder = patch.surface.Cylinder()
            direct = own_cylinder.Direct()
            if direct != bool(patch.direction @ z > 0):
                angle -= span
            start = angle % tau
            end = start + span
            intervals.append((start, min(end, tau)))
            if end > tau:
                intervals.append((0.0, end - tau))
            # 用合并后轴向覆盖的中点定义孔心，同一孔拆面时不随面数改变。
            other = np.array(patch.surface.Value(u, patch.surface.LastVParameter()).Coord())
            axial_bounds.extend((float((point - first.origin) @ z), float((other - first.origin) @ z)))

        if _covered_angle(intervals) + _ANGLE_EPS >= pi:
            areas = [patches[index].area for index in group]
            radius = float(np.average([patches[index].radius for index in group], weights=areas))
            center = first.origin + z * ((min(axial_bounds) + max(axial_bounds)) / 2)
            holes.append((radius * 2, center))
            if len(holes) > _MAX_HOLES:
                raise StepError("ERR_MODEL_TOO_COMPLEX")
    holes.sort(key=lambda hole: (hole[0], *hole[1]))
    return {
        "hole_count": len(holes), "diameters_sorted_mm": [diameter for diameter, _ in holes],
        "centers_mm": [center.tolist() for _, center in holes],
        "center_distances_sorted_mm": sorted(
            float(np.linalg.norm(a - b)) for (_, a), (_, b) in combinations(holes, 2)
        ),
    }


def _covered_angle(intervals: list[tuple[float, float]]) -> float:
    total = end = 0.0
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return total
