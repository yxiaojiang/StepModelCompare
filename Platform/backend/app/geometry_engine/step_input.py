"""校验待解析的 STEP 文件并定义熔断上限。仅依赖标准库，父子进程共享校验逻辑。"""

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import BinaryIO

STEP_EXTENSIONS = frozenset({".stp", ".step"})
_STEP_SIGNATURE = b"ISO-10303-21;"
_HEADER_BYTES = 4096


@dataclass(frozen=True)
class ParseLimits:
    """保存单件文件、原始面、网格与子进程内存的上限，进程池运行期固定。"""

    max_file_size_bytes: int = 50_000_000
    max_brep_faces: int = 50_000
    max_mesh_triangles: int = 1_000_000
    occt_memory_limit_mb: int = 2048

    def __post_init__(self) -> None:
        """校验构造时配置的各项上限，无返回值，非正数时抛 ValueError。"""
        if min(
            self.max_file_size_bytes, self.max_brep_faces,
            self.max_mesh_triangles, self.occt_memory_limit_mb,
        ) <= 0:
            raise ValueError("解析上限须为正数")


class StepError(RuntimeError):
    """跨进程传递固定错误码，避免暴露文件路径或原生解析异常原文。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_step_file(path: Path, limit: int, *, read_payload: bool = False) -> bytes | None:
    """检查 path 的扩展名、limit 字节上限及文件头，read_payload 为真时返回完整文件字节。

    非普通文件、无效头、超限及 I/O 失败抛带固定错误码的 StepError。
    """
    if path.suffix.lower() not in STEP_EXTENSIONS:
        raise StepError("ERR_INVALID_STEP_HEADER")
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise StepError("ERR_STEP_FILE_IO")
        with path.open("rb") as source:
            _validate_open_file(source, limit)
            if read_payload:
                # 子进程在同一打开句柄再次验证并限长读取，避免仅依赖父进程的预检结果。
                source.seek(0)
                payload = source.read(limit + 1)
                if len(payload) > limit:
                    raise StepError("ERR_STEP_FILE_TOO_LARGE")
                if not _has_signature(payload[:_HEADER_BYTES]):
                    raise StepError("ERR_INVALID_STEP_HEADER")
                return payload
    except OSError as error:
        raise StepError("ERR_STEP_FILE_IO") from error
    return None


def _validate_open_file(source: BinaryIO, limit: int) -> None:
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise StepError("ERR_STEP_FILE_IO")
    if info.st_size > limit:
        raise StepError("ERR_STEP_FILE_TOO_LARGE")
    if not _has_signature(source.read(min(limit, _HEADER_BYTES))):
        raise StepError("ERR_INVALID_STEP_HEADER")


def _has_signature(data: bytes) -> bool:
    return data.removeprefix(b"\xef\xbb\xbf").lstrip(b" \r\n\t").startswith(_STEP_SIGNATURE)
