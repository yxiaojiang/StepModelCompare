"""Windows Studio launcher. The embedded WebView and API share one lifetime."""

import ctypes
import logging
import os
import socket
import sys
from multiprocessing import freeze_support
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

from paths import ROOT

TITLE = "STEP 相似检索 Studio"
MUTEX_NAME = "StepModelCompareStudio.Instance"


def start_studio(data_dir):
    os.environ["STEP_STUDIO_DATA"] = str(data_dir)
    cache = data_dir / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=cache / "studio.log", encoding="utf-8", level=logging.INFO)

    import uvicorn
    import webview

    from app import app

    with socket.create_server(("127.0.0.1", 0)) as listener:
        host, port = listener.getsockname()
        server = uvicorn.Server(uvicorn.Config(
            app, host=host, port=port, loop="asyncio", http="h11", ws="none",
            log_config=None, access_log=False, timeout_graceful_shutdown=5,
        ))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, name="Studio API")
        thread.start()
        try:
            deadline = monotonic() + 15
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(0.025)
            if not server.started:
                raise RuntimeError("本机服务启动失败，请查看 Data/.cache/studio.log。")
            webview.create_window(
                TITLE, url=f"http://{host}:{port}/", width=1440, height=920,
                min_size=(820, 620), background_color="#0a121d",
            )
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(cache / "webview"))
        finally:
            server.should_exit = True
            thread.join()


def main():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, MUTEX_NAME)
    try:
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            ctypes.windll.user32.MessageBoxW(None, "Studio 已打开。", TITLE, 0x40)
            return 0
        data_dir = (Path(sys.executable).resolve().parent / "Data" if getattr(sys, "frozen", False)
                    else ROOT / "data" / "studio")
        start_studio(data_dir)
        return 0
    except Exception as exc:
        logging.exception("Studio startup failed")
        ctypes.windll.user32.MessageBoxW(None, str(exc), TITLE, 0x10)
        return 1
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    # PyInstaller starts the same executable for each multiprocessing worker.
    freeze_support()
    sys.exit(main())
