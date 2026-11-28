"""接收有限大小的 multipart 表单及保存 STEP 原件。依赖 Starlette 和受信存储卷。"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from fastapi import Request
from starlette.formparsers import MultiPartException, MultiPartParser

from app.db.model_features import vault_path
from app.geometry_engine.step_input import STEP_EXTENSIONS


class UploadError(ValueError):
    """携带稳定错误码及用于上传路由的 HTTP 状态。"""

    def __init__(self, code: str, status: int = 422):
        """保存 code 与 HTTP status，供路由或批量逐项响应使用。"""
        super().__init__(code)
        self.status = status


@dataclass(frozen=True, slots=True)
class StoredStep:
    """原件已封存后的文件名、摘要、大小以及服务端生成的存储引用。"""

    filename: str
    digest: str
    size: int
    key: str
    path: Path


@asynccontextmanager
async def upload_form(request: Request, max_files: int, max_bytes: int):
    """限制文件与字段数和全部请求字节，返回表单上下文；无效或超限抛 UploadError，退出关闭暂存文件。"""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise UploadError("INVALID_MULTIPART", 415)
    size = request.headers.get("content-length")
    if size is not None and size.isdecimal() and (len(size) > 19 or int(size) > max_bytes):
        raise UploadError("UPLOAD_TOO_LARGE", 413)

    async def limited():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > max_bytes:
                # 让解析器走异常关闭流程，清理已写入的部分文件。
                raise MultiPartException("UPLOAD_TOO_LARGE")
            yield chunk

    parser = MultiPartParser(request.headers, limited(), max_files=max_files, max_fields=8, max_part_size=8192)
    try:
        form = await parser.parse()
    except MultiPartException as error:
        if error.message == "UPLOAD_TOO_LARGE":
            raise UploadError("UPLOAD_TOO_LARGE", 413) from None
        raise UploadError("INVALID_MULTIPART") from None
    except BaseException:
        # 客户端断开或任务取消时解析器可能尚未移交表单，关闭它已经创建的各文件。
        for file in parser._files_to_close_on_error:
            file.close()
        raise
    try:
        yield form
    finally:
        await form.close()


def save_step(vault: Path, folder: str, name: str, stream: BinaryIO, limit: int) -> StoredStep:
    """把 stream 从首字节封存到 vault 的服务端 folder，返回元数据；格式和大小无效抛 UploadError。"""
    filename = Path(name.replace("\\", "/")).name
    if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise UploadError("INVALID_FILENAME")
    ext = Path(filename).suffix.lower()
    if ext not in STEP_EXTENSIONS:
        raise UploadError("INVALID_EXTENSION_OR_HEADER")
    key = f"{folder}/{uuid4().hex}{ext}"
    path = vault_path(vault, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    created = False
    try:
        stream.seek(0)
        with path.open("xb") as handle:
            created = True
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > limit:
                    raise UploadError("FILE_TOO_LARGE", 413)
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
    return StoredStep(filename, digest.hexdigest(), size, key, path)
