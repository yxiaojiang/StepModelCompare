"""Background library refresh with pollable progress for the Studio dialog."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from threading import Lock

from library import catalog


def _percent(done, total):
    if total is None:
        return None
    if total <= 0:
        return 100
    return min(100, int((done * 100) / total))


class LibraryLoader:
    def __init__(self, studio, work_lock, database=None, catalog_fn=None):
        self.studio = studio
        self.work_lock = work_lock
        self.database = database
        self._catalog_fn = catalog_fn or catalog
        self._lock = Lock()
        self._work = ThreadPoolExecutor(max_workers=1, thread_name_prefix="library")
        self._future = None
        self._status = {
            "state": "idle",
            "done": 0,
            "total": None,
            "percent": None,
            "current": "",
            "reused": False,
            "text": "尚未读取基础模型库",
        }

    def start(self):
        with self._lock:
            if self._future is not None and not self._future.done():
                return self._snapshot_locked()
        if self._reuse_if_current():
            return self.snapshot()
        with self._lock:
            if self._future is not None and not self._future.done():
                return self._snapshot_locked()
            self._status = {
                "state": "waiting",
                "done": 0,
                "total": None,
                "percent": None,
                "current": "",
                "reused": False,
                "text": "正在准备读取基础模型库",
            }
            self._future = self._work.submit(self._run)
            return self._snapshot_locked()

    def current(self, start_if_idle=False):
        if start_if_idle:
            with self._lock:
                idle = self._future is None and self._status["state"] == "idle"
            if idle:
                return self.start()
        return self.snapshot()

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def _catalog(self):
        path = self.database
        if path is None and self.studio is not None:
            path = self.studio.library
        if path is not None:
            return self._catalog_fn(path)
        return self._catalog_fn()

    def _reuse_if_current(self):
        acquired = self.work_lock.acquire(blocking=False)
        if not acquired:
            return False
        try:
            if self.studio is not None and not self.studio.can_reuse_index():
                return False
            entries = self._catalog()
        except Exception:
            return False
        finally:
            self.work_lock.release()
        with self._lock:
            total = len(entries)
            self._status = {
                "state": "complete",
                "done": total,
                "total": total,
                "percent": 100,
                "current": "",
                "reused": True,
                "entries": entries,
                "text": "基础模型库已就绪",
            }
        return True

    def _run(self):
        try:
            if self.studio is None:
                entries = self._catalog()
                self._update("complete", len(entries), len(entries), entries=entries, current="")
                return
            with self.work_lock:
                self.studio.refresh_base(progress=self._on_progress)
                entries = self._catalog()
            self._update("complete", len(entries), len(entries), entries=entries, current="")
        except Exception as error:
            self._update("failed", reason=str(error), text=f"基础模型库读取失败 · {error}")

    def _on_progress(self, done, total=None, current=""):
        if total is None:
            found = f"（已找到 {done} 个）" if done else ""
            text = f"正在扫描基础模型库{found}"
            state = "scanning"
        else:
            text = f"正在更新基础模型库（{done}/{total}）"
            state = "indexing"
        self._update(state, done, total, text=text, current=current or "")

    def _update(self, state, done=0, total=None, text="", reason=None, entries=None, current=None):
        with self._lock:
            status = {
                "state": state,
                "done": done,
                "total": total,
                "percent": _percent(done, total),
                "current": self._status.get("current", "") if current is None else current,
                "reused": False,
                "text": text or self._status.get("text", ""),
            }
            if reason is not None:
                status["reason"] = reason
            if entries is not None:
                status["entries"] = entries
            self._status = status

    def _snapshot_locked(self):
        snapshot = dict(self._status)
        if snapshot["state"] == "complete":
            snapshot.setdefault("entries", [])
        return snapshot

    def close(self, timeout=4):
        self._work.shutdown(wait=False, cancel_futures=True)
        future = self._future
        if future is None or timeout is None or timeout <= 0:
            return
        try:
            future.result(timeout=timeout)
        except (FuturesTimeout, Exception):
            return
