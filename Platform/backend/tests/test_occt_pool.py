"""验证解析进程熔断与真实 STEP 读取。使用 unittest、自制文件及仓库现有 Demo 样例。"""

from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from app.config import Settings
from app.geometry_engine.step_input import ParseLimits, StepError
from app.geometry_engine.worker_runner import OcctPool

_DEMO_QUERIES = Path(__file__).resolve().parents[3] / "Demo" / "samples" / "rehearsal" / "queries"


def _processor(path: Path, _limits: ParseLimits, mesh: Path | None) -> dict:
    if path.stem == "hang":
        path.with_suffix(".pid").write_text(str(os.getpid()), encoding="ascii")
        if mesh is not None:
            mesh.write_bytes(b"half")
        sleep(60)
    elif path.stem == "crash":
        path.with_suffix(".pid").write_text(str(os.getpid()), encoding="ascii")
        os._exit(8)
    elif path.stem == "exception":
        raise RuntimeError("测试异常文本")
    elif path.stem == "memory":
        return {"allocation_denied": _allocation_denied()}
    return {"pid": os.getpid()}


def _allocation_denied() -> bool:
    length = 256 * 1024 * 1024
    if os.name == "nt":
        import ctypes as ct

        kernel = ct.WinDLL("kernel32")
        kernel.VirtualAlloc.argtypes = (ct.c_void_p, ct.c_size_t, ct.c_ulong, ct.c_ulong)
        kernel.VirtualAlloc.restype = ct.c_void_p
        kernel.VirtualFree.argtypes = (ct.c_void_p, ct.c_size_t, ct.c_ulong)
        kernel.VirtualFree.restype = ct.c_int
        # 同时保留并提交匿名页，直接验证 Windows Job Object 的进程提交上限。
        allocation = kernel.VirtualAlloc(None, length, 0x3000, 0x04)
        if allocation:
            kernel.VirtualFree(allocation, 0, 0x8000)
        return not allocation

    import mmap

    try:
        with mmap.mmap(-1, length):
            return False
    except OSError:
        return True


class PoolRecoveryTests(unittest.TestCase):
    """用可控测试子进程验证物理强杀、异常隔离和固定任务数轮换。"""

    def setUp(self):
        """创建每项测试独享的带签名文件和暂存目录。"""
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self._directory = Path(directory.name)
        for name in ("ok", "hang", "crash", "exception", "memory"):
            (self._directory / f"{name}.step").write_bytes(b"ISO-10303-21;")
        self._ok = self._directory / "ok.step"

    def test_hard_timeout_kills_and_recovers(self):
        """在已常驻进程内模拟卡死，确认进程被回收、半成品清除、下一件成功。"""
        with OcctPool(ParseLimits(), workers=1, timeout=2, processor=_processor) as pool:
            first_pid = pool.parse(self._ok)["pid"]
            stalled = self._directory / "hang.step"
            target = self._directory / "mesh.npz"
            started = monotonic()
            with self.assertRaisesRegex(StepError, "^ERR_PARSE_TIMEOUT$"):
                pool.parse(stalled, mesh_target=target)
            self.assertLess(monotonic() - started, 5)
            self.assertEqual(int(stalled.with_suffix(".pid").read_text(encoding="ascii")), first_pid)
            self.assertNotIn(first_pid, {process.pid for process in multiprocessing.active_children()})
            self.assertFalse(target.exists())
            self.assertNotEqual(pool.parse(self._ok, mesh_target=target)["pid"], first_pid)
            self.assertFalse(target.exists())

    def test_native_exit_and_exception_recover(self):
        """模拟进程无响应退出和未预期 Python 异常，两次故障后均能换新继续。"""
        with OcctPool(ParseLimits(), workers=1, timeout=5, processor=_processor) as pool:
            first_pid = pool.parse(self._ok)["pid"]
            with self.assertRaisesRegex(StepError, "^ERR_PARSE_WORKER_EXIT$"):
                pool.parse(self._directory / "crash.step")
            self.assertNotIn(first_pid, {process.pid for process in multiprocessing.active_children()})
            second_pid = pool.parse(self._ok)["pid"]
            self.assertNotEqual(second_pid, first_pid)
            with self.assertRaisesRegex(StepError, "^ERR_PARSE_FAILED$"):
                pool.parse(self._directory / "exception.step")
            self.assertNotEqual(pool.parse(self._ok)["pid"], second_pid)

    def test_recycles_after_n_tasks(self):
        """同一进程恰好服务两次，到限释放，下一次由新进程执行。"""
        with OcctPool(ParseLimits(), workers=1, timeout=5, recycle_after=2, processor=_processor) as pool:
            first_pid = pool.parse(self._ok)["pid"]
            self.assertEqual(pool.parse(self._ok)["pid"], first_pid)
            self.assertNotIn(first_pid, {process.pid for process in multiprocessing.active_children()})
            self.assertNotEqual(pool.parse(self._ok)["pid"], first_pid)

    def test_memory_limit_denies_os_allocation(self):
        """用 128 MiB 上限拒绝子进程的 256 MiB 系统分配。"""
        limits = replace(ParseLimits(), occt_memory_limit_mb=128)
        with OcctPool(limits, workers=1, timeout=5, processor=_processor) as pool:
            self.assertTrue(pool.parse(self._directory / "memory.step")["allocation_denied"])

    def test_preflight_rejects_header_extension_and_size(self):
        """校验错误在解析前被报告，合法大写 STEP 扩展名可进入解析。"""
        bad_header = self._directory / "invalid.STP"
        bad_header.write_bytes(b"payload ISO-10303-21;")
        wrong_suffix = self._directory / "wrong.txt"
        wrong_suffix.write_bytes(b"ISO-10303-21;")
        big = self._directory / "big.STEP"
        big.write_bytes(b"ISO-10303-21;" + b" " * 100)
        upper = self._directory / "upper.STEP"
        upper.write_bytes(b"ISO-10303-21;")
        with OcctPool(replace(ParseLimits(), max_file_size_bytes=25), workers=1, processor=_processor) as pool:
            for path, code in (
                (bad_header, "ERR_INVALID_STEP_HEADER"), (wrong_suffix, "ERR_INVALID_STEP_HEADER"),
                (big, "ERR_STEP_FILE_TOO_LARGE"),
            ):
                with self.subTest(path=path), self.assertRaisesRegex(StepError, code):
                    pool.parse(path)
            self.assertGreater(pool.parse(upper)["pid"], 0)

    def test_strict_mesh_requires_new_artifact(self):
        """任务声明必需网格时，已有目标文件不能代替本次缺失产物。"""
        target = self._directory / "previous.npz"
        target.write_bytes(b"previous")
        with OcctPool(ParseLimits(), workers=1, processor=_processor) as pool:
            with self.assertRaisesRegex(StepError, "^ERR_STEP_MESH_MISSING$"):
                pool.parse(self._ok, mesh_target=target, require_mesh=True)
            self.assertEqual(target.read_bytes(), b"previous")
            with self.assertRaises(ValueError):
                pool.parse(self._ok, require_mesh=True)


class RealOcctTests(unittest.TestCase):
    """在真实 OCCT 子进程中读取自制 STEP 和 Demo 样例，验证协议、几何及原始面/三角片熔断。"""

    def test_demo_and_face_budgets(self):
        """对 Demo 英寸件验证毫米与面数，分别触发原始面和网格三角片上限。"""
        sample = _DEMO_QUERIES / "SHOW-POSE-INCH.step"
        with TemporaryDirectory() as directory:
            target = Path(directory) / "mesh.npz"
            with OcctPool.from_settings(Settings(occt_pool_size=1)) as pool:
                result = pool.parse(sample, mesh_target=target)
                self.assertEqual(result["protocols"], ["AP214"])
                self.assertEqual(result["source_units"], ["INCH"])
                self.assertEqual(result["calculation_unit"], "mm")
                self.assertEqual(result["closed_solid_count"], 1)
                self.assertGreater(result["raw_face_count"], 1)
                self.assertGreater(result["mesh_triangles"], 1)
                self.assertTrue(target.is_file())
            target.unlink()
            with OcctPool.from_settings(Settings(occt_pool_size=1, max_brep_faces=1)) as pool:
                with self.assertRaisesRegex(StepError, "^ERR_MODEL_TOO_COMPLEX$"):
                    pool.parse(sample)
            with OcctPool.from_settings(Settings(occt_pool_size=1, max_mesh_triangles=1)) as pool:
                result = pool.parse(sample, mesh_target=target)
                self.assertEqual(result["mesh_error"], "ERR_MODEL_TOO_COMPLEX")
                self.assertFalse(target.exists())
                with self.assertRaisesRegex(StepError, "^ERR_MODEL_TOO_COMPLEX$"):
                    pool.parse(sample, mesh_target=target, require_mesh=True)
                self.assertFalse(target.exists())

    def test_ap242_material_and_semantic_pmi(self):
        """用自制 AP242 圆柱验证 XDE 材料名、形状分配和直径语义标注的正例。"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "annotated.step"
            _write_annotated_cylinder(path)
            with OcctPool(ParseLimits(), workers=1) as pool:
                result = pool.parse(path)
        self.assertEqual(result["protocols"], ["AP242"])
        self.assertEqual(result["xde_readable_materials"], 1)
        self.assertEqual(result["xde_assigned_materials"], 1)
        self.assertEqual(result["semantic_pmi"], {"dimensions": 1, "geometric_tolerances": 0, "datums": 0})

    def test_multiple_solids_open_shell_and_external_ref(self):
        """用自制装配及开壳验证拓扑，分文件装配在加载外部零件前被拒绝。"""
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            assembly, shell, external = _write_topology_samples(folder)
            with OcctPool(ParseLimits(), workers=1) as pool:
                multi = pool.parse(assembly)
                self.assertTrue(multi["is_assembly"])
                self.assertEqual(multi["closed_solid_count"], 2)
                self.assertEqual(multi["open_shell_count"], 0)
                self.assertEqual(pool.parse(shell)["open_shell_count"], 1)
                with self.assertRaisesRegex(StepError, "^ERR_STEP_EXTERNAL_REFERENCE$"):
                    pool.parse(external)


def _write_annotated_cylinder(path: Path) -> None:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.XCAFDimTolObjects import XCAFDimTolObjects_DimensionObject, XCAFDimTolObjects_DimensionType
    from OCP.XCAFDoc import XCAFDoc_Dimension, XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    main = document.Main()
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(main)
    cylinder = BRepPrimAPI_MakeCylinder(5.0, 12.0).Shape()
    part = shapes.AddShape(cylinder, False)
    strings = [TCollection_HAsciiString(item) for item in ("Test material", "Test", "density", "mass density")]
    XCAFDoc_DocumentTool.MaterialTool_s(main).SetMaterial(
        part, strings[0], strings[1], 7.8, strings[2], strings[3],
    )
    faces = TopExp_Explorer(cylinder, TopAbs_FACE)
    while faces.More():
        face = TopoDS.Face(faces.Current())
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder:
            surface = shapes.AddSubShape(part, face)
            break
        faces.Next()
    else:
        raise AssertionError("未生成圆柱曲面")
    tools = XCAFDoc_DocumentTool.DimTolTool_s(main)
    dimension = tools.AddDimension()
    data = XCAFDimTolObjects_DimensionObject()
    data.SetType(XCAFDimTolObjects_DimensionType.XCAFDimTolObjects_DimensionType_Size_Diameter)
    data.SetValue(10.0)
    XCAFDoc_Dimension.Set_s(dimension).SetObject(data)
    tools.SetDimension(surface, dimension)

    writer = STEPCAFControl_Writer()
    previous_schema = Interface_Static.CVal_s("write.step.schema")
    try:
        assert Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")
        writer.SetMaterialMode(True)
        writer.SetDimTolMode(True)
        assert writer.Transfer(document)
        assert writer.Write(str(path)) == IFSelect_RetDone
    finally:
        Interface_Static.SetCVal_s("write.step.schema", previous_schema)


def _write_topology_samples(folder: Path) -> tuple[Path, Path, Path]:
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_ShellBasedSurfaceModel, STEPControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape()
    builder.Add(compound, box)
    builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(20, 0, 0), 8.0, 8.0, 8.0).Shape())
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFDoc_DocumentTool.ShapeTool_s(document.Main()).AddShape(compound, True)
    assembly, shell_path, external = (folder / name for name in ("assembly.step", "shell.step", "external.step"))
    writer = STEPCAFControl_Writer()
    assert writer.Transfer(document)
    assert writer.Write(str(assembly)) == IFSelect_RetDone
    external_writer = STEPCAFControl_Writer()
    assert external_writer.Transfer(document, STEPControl_AsIs, "part-")
    assert external_writer.Write(str(external)) == IFSelect_RetDone

    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    faces = TopExp_Explorer(box, TopAbs_FACE)
    for _ in range(5):
        builder.Add(shell, faces.Current())
        faces.Next()
    shell_writer = STEPControl_Writer()
    assert shell_writer.Transfer(shell, STEPControl_ShellBasedSurfaceModel) == IFSelect_RetDone
    assert shell_writer.Write(str(shell_path)) == IFSelect_RetDone
    return assembly, shell_path, external
