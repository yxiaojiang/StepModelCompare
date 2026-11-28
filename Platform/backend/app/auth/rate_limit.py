"""提供鉴权失败前的 IP 与账号双维度滑动窗口限流。

该模块只保存当前进程的短期计数，不替代生产多实例部署的共享限流器；共享限流器属于部署阶段待冻结内容。
"""

from collections import OrderedDict, deque
from threading import RLock
import time


class AuthRateLimiter:
    """以固定容量滑动窗口限制密码慢校验的进入次数。"""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60, clock=time.monotonic, *, capacity=4096):
        """创建限流器；参数非正数抛 ValueError，clock 可用于确定性测试。"""
        if max_attempts <= 0 or window_seconds <= 0 or capacity < 2:
            raise ValueError("鉴权限流参数必须为正数")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._capacity = capacity
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = RLock()

    def allow(self, *dimensions: str) -> bool:
        """原子检查并记录所有维度；任一维度超限则返回 False 且不写入本次命中。"""
        if not dimensions or any(not dimension for dimension in dimensions):
            raise ValueError("限流维度不能为空")
        with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            # 按最近写入时间清理；容量耗尽时拒绝新增维度，防止驱逐活跃桶而重置限额。
            while self._hits and next(iter(self._hits.values()))[-1] <= cutoff:
                self._hits.popitem(last=False)
            dimensions = tuple(dict.fromkeys(dimensions))
            if len(self._hits) + sum(key not in self._hits for key in dimensions) > self._capacity:
                return False
            for dimension in dimensions:
                hits = self._hits.get(dimension)
                if hits is not None and sum(hit > cutoff for hit in hits) >= self.max_attempts:
                    return False
            for dimension in dimensions:
                hits = self._hits.setdefault(dimension, deque())
                while hits and hits[0] <= cutoff:
                    hits.popleft()
                hits.append(now)
                self._hits.move_to_end(dimension)
            return True

    def clear(self) -> None:
        """清空窗口计数，供测试或进程生命周期重置使用。"""
        with self._lock:
            self._hits.clear()
