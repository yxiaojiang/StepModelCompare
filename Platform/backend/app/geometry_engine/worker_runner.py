"""管理第一层常驻 OCCT 进程。依赖标准库，固定槽位独占，退出上下文时回收所有子进程。"""

from contextlib import contextmanager
from collections import deque
import json
import multiprocessing as mp
from multiprocessing.connection import Connection, wait
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Condition
from time import monotonic
from typing import TYPE_CHECKING, Callable

from app.geometry_engine.feature_versions import require_feature_version
from app.geometry_engine.process_limits import apply_memory_limit
from app.geometry_engine.step_input import ParseLimits, StepError, validate_step_file

if TYPE_CHECKING:
    from app.config import Settings

_Processor = Callable[[Path, ParseLimits, Path | None], dict]
_TASK = b"T"
_RESULT = b"R"
_INIT_FAILED = b"E"


def _worker_main(connection: Connection, limits: ParseLimits, slot: Path, processor: _Processor | None):
    with connection:
        default_processor = processor is None
        try:
            apply_memory_limit(limits.occt_memory_limit_mb)
            os.environ.pop("STEP_PLATFORM_DATABASE_URL", None)
            # 避免 BLAS 为每个线程预留地址空间，挤占 OCCT 的内存预算。
            os.environ["OPENBLAS_NUM_THREADS"] = "1"
            if processor is None:
                from OCP.Message import Message
                from OCP.Message import Message_PrinterOStream
                from app.geometry_engine.step_reader import read_step

                Message.DefaultMessenger_s().RemovePrinters(Message_PrinterOStream.get_type_descriptor_s())
                processor = read_step
        except Exception:
            connection.send_bytes(_INIT_FAILED)
            return

        while True:
            try:
                if connection.recv_bytes(1) != _TASK:
                    return
            except (EOFError, OSError):
                return
            path, wants_mesh, wants_glb, features = json.loads((slot / "request.json").read_text(encoding="utf-8"))
            try:
                mesh = slot / "mesh.npz" if wants_mesh else None
                if features is None:
                    value = processor(Path(path), limits, mesh)
                elif default_processor:
                    value = processor(Path(path), limits, mesh, glb_path=slot / "preview.glb" if wants_glb else None,
                                      **features)
                else:
                    raise StepError("ERR_FEATURE_PROCESSOR_UNAVAILABLE")
                result = {"value": value}
            except StepError as error:
                result = {"error_code": error.code}
            except MemoryError:
                result = {"error_code": "ERR_PARSE_MEMORY_LIMIT", "restart": True}
            except Exception:
                # 未知原生或包装层异常后重建进程，避免继续复用可能损坏的内核状态。
                result = {"error_code": "ERR_PARSE_FAILED", "restart": True}

            (slot / "result.json").write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
            connection.send_bytes(_RESULT)


class _WorkerSlot:
    """持有一个进程、控制通道及暂存目录。槽位被借用期间仅由一个调用线程操作。"""

    def __init__(self, folder: Path):
        self.folder = folder
        self.process = None
        self.connection = None
        self.completed = 0

    # ---- 进程生命周期 ----

    def _start(self, limits: ParseLimits, processor: _Processor | None) -> None:
        # 统一使用 spawn，避免 fork 将父进程的线程、连接池或内核状态复制进解析进程。
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker_main, args=(child, limits, self.folder, processor), daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            process.close()
            raise
        finally:
            child.close()
        self.process, self.connection = process, parent
        self.completed = 0

    def _stop(self, *, force: bool = False) -> None:
        if self.process is None:
            return
        self.connection.close()
        if not force:
            self.process.join(0.1)
        if self.process.is_alive():
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        self.process.join()
        self.process.close()
        self.process = self.connection = None
        self.completed = 0

    # ---- 单次调度与产物发布 ----

    def _dispatch(self, deadline: float) -> dict:
        # IPC 只收发固定单字节信号。较大结果写到槽位目录，避免超时边界卡在读半帧。
        self.connection.send_bytes(_TASK)
        ready = wait([self.connection, self.process.sentinel], max(0, deadline - monotonic()))
        if not ready or monotonic() >= deadline:
            raise StepError("ERR_PARSE_TIMEOUT")
        if self.connection not in ready:
            raise StepError("ERR_PARSE_WORKER_EXIT")
        signal = self.connection.recv_bytes(1)
        if signal == _INIT_FAILED:
            raise StepError("ERR_PARSE_WORKER_INIT")
        if signal != _RESULT:
            raise StepError("ERR_PARSE_WORKER_EXIT")
        result = json.loads((self.folder / "result.json").read_text(encoding="utf-8"))
        if monotonic() >= deadline:
            raise StepError("ERR_PARSE_TIMEOUT")
        return result

    def _run(self, path, mesh_target, deadline, limits, recycle_after, processor, *,
             require_mesh, glb_target=None, features=None) -> dict:
        mesh = self.folder / "mesh.npz"
        glb = self.folder / "preview.glb"
        try:
            (self.folder / "request.json").write_text(
                json.dumps((str(path), mesh_target is not None, glb_target is not None, features)), encoding="utf-8",
            )
            if self.process is not None and not self.process.is_alive():
                self._stop()
            if self.process is None:
                self._start(limits, processor)
            result = self._dispatch(deadline)
        except StepError:
            self._stop(force=True)
            raise
        except (EOFError, OSError, ValueError) as error:
            self._stop(force=True)
            raise StepError("ERR_PARSE_WORKER_EXIT") from error

        self.completed += 1
        if result.get("restart") or self.completed >= recycle_after:
            self._stop()
        if "error_code" in result:
            raise StepError(result["error_code"])
        value = result["value"]
        if require_mesh:
            # 正式计算依赖成功生成的网格，不能把盘点用的部分结果或旧目标当成成功。
            if "mesh_error" in value:
                raise StepError(value["mesh_error"])
            if not mesh.is_file():
                raise StepError("ERR_STEP_MESH_MISSING")
        if features is not None:
            payload = value.get("feature_payload")
            if not payload or payload.get("feature_version") != features["feature_version"]:
                raise StepError("ERR_FEATURE_PAYLOAD_MISSING")
        if glb_target is not None and not glb.is_file():
            raise StepError("ERR_STEP_GLB_MISSING")
        for source, target in ((mesh, mesh_target), (glb, glb_target)):
            if target is not None and source.is_file():
                # 先复制到目标同一目录，再原子替换，避免跨磁盘移动与半成品可见。
                with TemporaryDirectory(prefix=".occt-", dir=target.parent) as staging:
                    complete = Path(staging) / source.name
                    shutil.copyfile(source, complete)
                    if monotonic() >= deadline:
                        raise StepError("ERR_PARSE_TIMEOUT")
                    complete.replace(target)
        return value


class OcctPool:
    """管理固定数量的解析槽位，按需启动常驻进程。线程可并发借用，关闭时等待已在跑的任务。"""

    def __init__(
        self, limits: ParseLimits, *, workers: int = 2, timeout: float = 30,
        recycle_after: int = 50, processor: _Processor | None = None,
    ):
        if min(workers, timeout, recycle_after) <= 0:
            raise ValueError("进程数、超时和轮换任务数须为正数")
        self._limits = limits
        self._timeout = timeout
        self._recycle_after = recycle_after
        self._processor = processor
        self._condition = Condition()
        self._storage = TemporaryDirectory(prefix="step-occt-")
        self._slots = [_WorkerSlot(Path(self._storage.name) / str(index)) for index in range(workers)]
        for slot in self._slots:
            slot.folder.mkdir()
        self._available = deque(self._slots)
        self._closed = False

    # ---- 外部调用与槽位归还 ----

    @classmethod
    def from_settings(cls, settings: "Settings") -> "OcctPool":
        """从 settings 读取生产解析和生命周期配置，返回惰性进程池，创建暂存失败时抛 OSError。"""
        return cls(
            settings.parse_limits(), workers=settings.occt_pool_size,
            timeout=settings.occt_parse_timeout, recycle_after=settings.occt_tasks_per_worker,
        )

    def __enter__(self):
        """进入池的资源上下文，无参数，返回本实例。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """接收上下文异常信息并释放进程池，无返回值，调用方异常继续传播。"""
        self.close()

    @contextmanager
    def _lease(self, deadline: float):
        with self._condition:
            while not self._available and not self._closed:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise StepError("ERR_PARSE_POOL_BUSY")
                self._condition.wait(remaining)
            if self._closed:
                raise RuntimeError("解析进程池已关闭")
            if monotonic() >= deadline:
                raise StepError("ERR_PARSE_POOL_BUSY")
            slot = self._available.popleft()
        try:
            yield slot
        finally:
            with self._condition:
                self._available.append(slot)
                self._condition.notify_all()

    def parse(
        self, path: Path, *, mesh_target: Path | None = None,
        require_mesh: bool = False, deadline: float | None = None,
    ) -> dict:
        """解析受信暂存路径 path 并返回摘要，指定 mesh_target 时发布完整网格。

        require_mesh 要求成功生成网格。deadline 为调用方的单调时钟截止值，与池自身时限取最早值。
        源文件、解析或必需网格失败抛 StepError，目标参数冲突抛 ValueError。
        池关闭抛 RuntimeError，输出写入失败抛 OSError。
        """
        return self._execute(path, mesh_target, require_mesh, deadline)

    def extract_features(
        self, path: Path, *, feature_version: str, mesh_target: Path, glb_target: Path | None = None,
        original_filename: str | None = None, registered_material: str | None = None,
        deadline: float | None = None,
    ) -> dict:
        """从 path 提取指定 feature_version，返回特征字典并写出必需网格和可选 GLB。

        original_filename 和 registered_material 对应原件名与台账材料；deadline 为单调时钟截止值。
        产物路径须为调用方拥有的暂存路径。版本或路径无效抛 ValueError，解析失败抛 StepError，
        暂存写入失败抛 OSError，池关闭抛 RuntimeError。
        """
        require_feature_version(feature_version)
        result = self._execute(
            path, mesh_target, True, deadline, glb_target=glb_target,
            features={"feature_version": feature_version, "original_filename": original_filename,
                      "registered_material": registered_material},
        )
        return result["feature_payload"]

    def _execute(self, path, mesh_target, require_mesh, deadline, *, glb_target=None, features=None) -> dict:
        if require_mesh and mesh_target is None:
            raise ValueError("要求生成网格时必须指定目标路径")
        pool_deadline = monotonic() + self._timeout
        if deadline is not None:
            pool_deadline = min(pool_deadline, deadline)
        validate_step_file(path, self._limits.max_file_size_bytes)
        path = path.absolute()
        targets = [target.resolve() for target in (mesh_target, glb_target) if target is not None]
        if path.resolve() in targets or len(set(targets)) != len(targets):
            raise ValueError("产物目标不能相同或覆盖 STEP 原件")
        with self._lease(pool_deadline) as slot:
            try:
                return slot._run(
                    path, mesh_target, pool_deadline, self._limits, self._recycle_after, self._processor,
                    require_mesh=require_mesh, glb_target=glb_target, features=features,
                )
            finally:
                # 强杀可能留下不完整网格，归还槽位之前清除，禁止下一件误用。
                (slot.folder / "mesh.npz").unlink(missing_ok=True)
                (slot.folder / "preview.glb").unlink(missing_ok=True)

    def close(self) -> None:
        """关闭池并释放进程与暂存目录，无参数或返回值，可重复调用。"""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
            self._condition.wait_for(lambda: len(self._available) == len(self._slots))
        for slot in self._slots:
            slot._stop()
        self._storage.cleanup()
