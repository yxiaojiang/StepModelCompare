"""为当前解析进程设置操作系统内存上限。依赖平台标准库，在加载原生几何库之前调用。"""

import os
import sys


def apply_memory_limit(limit_mb: int) -> None:
    """将当前进程限制为 limit_mb MiB，无返回值，平台不支持或设置失败时抛 OSError。"""
    limit_bytes = limit_mb * 1024 * 1024
    if os.name == "nt":
        _apply_windows_limit(limit_bytes)
    elif sys.platform.startswith("linux"):
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # 继承到更严格的容器限制时只能收紧，避免无意扩大已有上限。
        effective = min(limit for limit in (limit_bytes, soft, hard) if limit != resource.RLIM_INFINITY)
        resource.setrlimit(resource.RLIMIT_AS, (effective, effective))
    else:
        raise OSError("此平台暂无 OCCT 进程内存限制实现")


def _apply_windows_limit(limit_bytes: int) -> None:
    import ctypes as ct
    from ctypes import wintypes as win

    # 结构按 Win32 ABI 排列，SIZE_T 在 32/64 位系统上随指针宽度变化。
    class _BasicLimit(ct.Structure):
        """对应 Job Object 基本限制，由扩展限制结构按值使用。"""

        _fields_ = [
            ("process_time", ct.c_longlong), ("job_time", ct.c_longlong),
            ("flags", win.DWORD), ("min_working_set", ct.c_size_t),
            ("max_working_set", ct.c_size_t), ("active_processes", win.DWORD),
            ("affinity", ct.c_size_t), ("priority", win.DWORD),
            ("scheduling_class", win.DWORD),
        ]

    class _ExtendedLimit(ct.Structure):
        """对应 Win32 扩展限制，为 SetInformationJobObject 提供进程内存上限。"""

        _fields_ = [
            ("basic", _BasicLimit), ("io_counters", ct.c_ulonglong * 6),
            ("process_memory", ct.c_size_t), ("job_memory", ct.c_size_t),
            ("peak_process_memory", ct.c_size_t), ("peak_job_memory", ct.c_size_t),
        ]

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ct.c_void_p, win.LPCWSTR)
    kernel.CreateJobObjectW.restype = win.HANDLE
    kernel.GetCurrentProcess.argtypes = ()
    kernel.GetCurrentProcess.restype = win.HANDLE
    kernel.SetInformationJobObject.argtypes = (win.HANDLE, ct.c_int, ct.c_void_p, win.DWORD)
    kernel.SetInformationJobObject.restype = win.BOOL
    kernel.AssignProcessToJobObject.argtypes = (win.HANDLE, win.HANDLE)
    kernel.AssignProcessToJobObject.restype = win.BOOL
    kernel.CloseHandle.argtypes = (win.HANDLE,)
    kernel.CloseHandle.restype = win.BOOL

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ct.WinError(ct.get_last_error())
    try:
        info = _ExtendedLimit()
        job_object_limit_process_memory = 0x00000100
        job_object_extended_limit_information = 9
        info.basic.flags = job_object_limit_process_memory
        info.process_memory = limit_bytes
        if not kernel.SetInformationJobObject(
            job, job_object_extended_limit_information, ct.byref(info), ct.sizeof(info),
        ):
            raise ct.WinError(ct.get_last_error())
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ct.WinError(ct.get_last_error())
    finally:
        # 未启用关闭即终止标志，Windows 会保留关联直到解析进程退出。
        kernel.CloseHandle(job)
