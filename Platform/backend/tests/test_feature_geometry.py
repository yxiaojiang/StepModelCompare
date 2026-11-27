"""验证真实 OCCT 特征、合成几何/PMI 及缓存坐标。临时文件仅在各测试上下文内存在。"""

import io
import json
from math import pi
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_PrinterOStream
from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shell
from OCP.XCAFDimTolObjects import (
    XCAFDimTolObjects_DatumObject, XCAFDimTolObjects_DimensionObject,
    XCAFDimTolObjects_DimensionType_Size_Diameter, XCAFDimTolObjects_GeomToleranceObject,
    XCAFDimTolObjects_GeomToleranceType_Position,
)
from OCP.XCAFDoc import (
    XCAFDoc_ColorGen, XCAFDoc_Datum, XCAFDoc_Dimension, XCAFDoc_DocumentTool, XCAFDoc_GeomTolerance,
)
from OCP.collections import Sequence_TDF_Label
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from app.geometry_engine.step_input import ParseLimits, StepError
from app.geometry_engine.worker_runner import OcctPool

_DEMO = Path(__file__).resolve().parents[3] / "Demo/samples/rehearsal/queries/SHOW-POSE-INCH.step"


def _box(x, y, z, origin=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*origin), x, y, z).Shape()


def _synthetic_step(destination: Path, shape, *, pmi_face=None) -> None:
    Message.DefaultMessenger_s().RemovePrinters(Message_PrinterOStream.get_type_descriptor_s())
    STEPControl_Controller.Init_s()
    Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    main = document.Main()
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(main)
    root = shapes.AddShape(shape, False)
    TDataStd_Name.Set_s(root, TCollection_ExtendedString("BRACKET REV A"))
    if pmi_face is not None:
        _add_xde_attributes(main, shapes.AddSubShape(root, pmi_face), root)
    writer = STEPCAFControl_Writer()
    writer.SetDimTolMode(True)
    writer.SetMaterialMode(True)
    if not writer.Transfer(document):
        raise AssertionError("合成 STEP 转换失败")
    stream = io.BytesIO()
    if writer.WriteStream(stream) != IFSelect_RetDone:
        raise AssertionError("合成 STEP 写出失败")
    destination.write_bytes(stream.getvalue())


def _add_xde_attributes(main, face, root) -> None:
    XCAFDoc_DocumentTool.ColorTool_s(main).SetColor(root, Quantity_Color(1., 0., 0., Quantity_TOC_sRGB), XCAFDoc_ColorGen)
    XCAFDoc_DocumentTool.LayerTool_s(main).SetLayer(root, TCollection_ExtendedString("machined"))
    materials = XCAFDoc_DocumentTool.MaterialTool_s(main)
    materials.SetMaterial(root, *(TCollection_HAsciiString(x) for x in ("AL-6061", "Aluminum")), 2.7,
                          TCollection_HAsciiString("mass density"), TCollection_HAsciiString("g/cm3"))
    gdt = XCAFDoc_DocumentTool.DimTolTool_s(main)
    dimension = XCAFDimTolObjects_DimensionObject()
    dimension.SetType(XCAFDimTolObjects_DimensionType_Size_Diameter)
    dimension.SetValue(6.0)
    label = gdt.AddDimension()
    XCAFDoc_Dimension.Set_s(label).SetObject(dimension)
    gdt.SetDimension(face, label)
    tolerance = XCAFDimTolObjects_GeomToleranceObject()
    tolerance.SetType(XCAFDimTolObjects_GeomToleranceType_Position)
    tolerance.SetValue(0.15)
    label = gdt.AddGeomTolerance()
    XCAFDoc_GeomTolerance.Set_s(label).SetObject(tolerance)
    gdt.SetGeomTolerance(face, label)

    datum = XCAFDimTolObjects_DatumObject()
    datum.SetName(TCollection_HAsciiString("A"))
    datum.SetPosition(1)
    datum_label = gdt.AddDatum()
    XCAFDoc_Datum.Set_s(datum_label).SetObject(datum)
    faces = Sequence_TDF_Label()
    faces.Append(face)
    gdt.SetDatum(faces, datum_label)
    gdt.SetDatumToGeomTol(datum_label, label)


class FeatureGeometryTests(unittest.TestCase):
    """用临时的合成 STEP、已有 Demo STEP 和本机 OCP 进程验证几何提取。"""

    def test_inch_samples_and_glb(self):
        """验证 Demo 英寸件、可重复采样、毫米到 GLB 右手米制矩阵和版本参数。"""
        with TemporaryDirectory() as directory, OcctPool(ParseLimits(), workers=1) as pool:
            folder = Path(directory)
            with self.assertRaises(ValueError):
                pool.extract_features(_DEMO, feature_version="9.0.0", mesh_target=folder / "bad.npz")
            mesh, glb = folder / "mesh.npz", folder / "preview.glb"
            payload = pool.extract_features(_DEMO, feature_version="1.0.0", mesh_target=mesh, glb_target=glb)
            self.assertEqual(payload["unit_source"], "INCH")
            self.assertTrue(payload["d2_volume"]["volume_available"])
            self.assertGreater(payload["d2_volume"]["volume_mm3"], 100_000)
            self.assertEqual(payload["d7_holes"]["hole_count"], 4)
            self.assertFalse(payload["d3_pmi"]["has_pmi"])
            second = folder / "again.npz"
            pool.extract_features(_DEMO, feature_version="1.0.0", mesh_target=second)
            with np.load(mesh, allow_pickle=False) as arrays, np.load(second, allow_pickle=False) as another:
                vertices, faces, points = (arrays[key] for key in ("vertices", "faces", "sample_points"))
                np.testing.assert_array_equal(points, another["sample_points"])
                self.assertEqual(len(points), 6500 + len(faces) + len(np.unique(faces)))
                binary = glb.read_bytes()
                self.assertEqual(struct.unpack_from("<4sII", binary), (b"glTF", 2, len(binary)))
                json_size, chunk = struct.unpack_from("<I4s", binary, 12)
                self.assertEqual(chunk, b"JSON")
                metadata = json.loads(binary[20:20 + json_size])
                bin_size, bin_chunk = struct.unpack_from("<I4s", binary, 20 + json_size)
                self.assertEqual((bin_size, bin_chunk), (len(binary) - 28 - json_size, b"BIN\0"))
                count = metadata["accessors"][0]["count"]
                actual = np.frombuffer(binary, "<f4", count * 3, 28 + json_size).reshape(-1, 3)
                expected = vertices[np.unique(faces)] @ np.array(payload["cache_refs"]["glb_coord_matrix"])[:3, :3].T
                np.testing.assert_allclose(actual, expected, atol=1e-8)

    def test_ap242_pmi_and_hole(self):
        """回读合成 AP242 的 XDE 属性、尺寸和公差，验证内孔方向与毫米几何量。"""
        body = _box(30, 20, 8)
        hole_axis = gp_Ax2(gp_Pnt(10, 10, 0), gp_Dir(0, 0, 1))
        cylinder = BRepPrimAPI_MakeCylinder(hole_axis, 3., 8.).Shape()
        part = BRepAlgoAPI_Cut(body, cylinder).Shape()
        explorer = TopExp_Explorer(part, TopAbs_FACE)
        while explorer.More():
            face = TopoDS.Face(explorer.Current())
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder:
                break
            explorer.Next()
        else:
            self.fail("合成内孔缺少圆柱面")
        with TemporaryDirectory() as directory, OcctPool(ParseLimits(), workers=1) as pool:
            folder = Path(directory)
            source = folder / "pmi.step"
            _synthetic_step(source, part, pmi_face=face)
            self.assertIn(b"AP242", source.read_bytes())
            feature = pool.extract_features(
                source, feature_version="1.0.0", mesh_target=folder / "pmi.npz",
                original_filename="ＢＲＡＣＫＥＴ.step",
            )
            basic = feature["d1_basic"]
            self.assertEqual(basic["filename_stem"], "bracket")
            self.assertIn("bracket rev a", basic["product_names"])
            self.assertEqual(basic["materials"], ["AL-6061"])
            self.assertIn("#ff0000", basic["colors_layers"]["rgb_palette"])
            self.assertIn("machined", basic["colors_layers"]["layers"])
            pmi = feature["d3_pmi"]
            self.assertTrue(pmi["has_pmi"])
            self.assertEqual(pmi["semantic_counts"], {"dimensions": 1, "geometric_tolerances": 1, "datums": 1})
            np.testing.assert_allclose(pmi["numeric_values_mm"], [0.15, 6.0])
            self.assertAlmostEqual(feature["d2_volume"]["volume_mm3"], (600 - pi * 9) * 8)
            self.assertAlmostEqual(sum(feature["d4_surface"]["type_area_ratio"].values()), 1)
            self.assertEqual(feature["d7_holes"]["hole_count"], 1)
            np.testing.assert_allclose(feature["d7_holes"]["diameters_sorted_mm"], [6.])
            np.testing.assert_allclose(feature["d7_holes"]["centers_mm"], [[10., 10., 4.]])
            override = pool.extract_features(source, feature_version="1.0.0", mesh_target=folder / "override.npz",
                                             registered_material=" SUS304 ")
            self.assertEqual(override["d1_basic"]["materials"], ["SUS304"])

    def test_multi_solid_open_shell_and_strict_mesh(self):
        """验证闭合体积累加、开壳显式缺项和超面片后旧目标文件保留。"""
        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        builder.Add(compound, _box(2, 3, 4))
        builder.Add(compound, _box(3, 4, 5, (20, 0, 0)))
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        face = TopExp_Explorer(compound, TopAbs_FACE).Current()
        builder.Add(shell, face)
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            good, open_step = folder / "multi.step", folder / "open.step"
            _synthetic_step(good, compound)
            _synthetic_step(open_step, shell)
            mesh = folder / "mesh.npz"
            with OcctPool(ParseLimits(), workers=1) as pool:
                feature = pool.extract_features(good, feature_version="1.0.0", mesh_target=mesh)
                self.assertEqual(feature["topology"]["closed_solid_count"], 2)
                self.assertAlmostEqual(feature["d2_volume"]["volume_mm3"], 84.)
                feature = pool.extract_features(open_step, feature_version="1.0.0", mesh_target=mesh)
                self.assertTrue(feature["topology"]["is_open_shell"])
                self.assertEqual(feature["d2_volume"], {"volume_available": False, "volume_mm3": None})
            old = mesh.read_bytes()
            glb = folder / "preview.glb"
            with OcctPool(ParseLimits(max_mesh_triangles=1), workers=1) as pool:
                with self.assertRaisesRegex(StepError, "ERR_MODEL_TOO_COMPLEX"):
                    pool.extract_features(good, feature_version="1.0.0", mesh_target=mesh, glb_target=glb)
            self.assertEqual(mesh.read_bytes(), old)
            self.assertFalse(glb.exists())


    def test_coplanar_and_coaxial_face_merging(self):
        """验证共边拆面归并，分离同轴孔各保留一项，外圆柱不会计入孔数。"""
        def drilled(z):
            axis = gp_Ax2(gp_Pnt(6, 5, z), gp_Dir(0, 0, 1))
            hole = BRepPrimAPI_MakeCylinder(axis, 2., 4.).Shape()
            return BRepAlgoAPI_Cut(_box(12, 10, 4, (0, 0, z)), hole).Shape()

        fused = BRepAlgoAPI_Fuse(drilled(0), drilled(4)).Shape()
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        builder.Add(compound, drilled(0))
        builder.Add(compound, drilled(10))
        builder.Add(compound, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20, 0, 0), gp_Dir(0, 0, 1)), 2., 3.).Shape())
        with TemporaryDirectory() as directory, OcctPool(ParseLimits(), workers=1) as pool:
            folder = Path(directory)
            source, mesh = folder / "fused.step", folder / "mesh.npz"
            _synthetic_step(source, fused)
            feature = pool.extract_features(source, feature_version="1.0.0", mesh_target=mesh)
            self.assertGreater(feature["topology"]["raw_face_count"], 7)
            self.assertEqual(feature["d4_surface"]["merged_face_count"], 7)
            self.assertEqual(feature["d7_holes"]["hole_count"], 1)
            np.testing.assert_allclose(feature["d7_holes"]["centers_mm"], [[6., 5., 4.]])
            _synthetic_step(source, compound)
            feature = pool.extract_features(source, feature_version="1.0.0", mesh_target=mesh)
            self.assertEqual(feature["d7_holes"]["hole_count"], 2)
            np.testing.assert_allclose(feature["d7_holes"]["center_distances_sorted_mm"], [10.])
