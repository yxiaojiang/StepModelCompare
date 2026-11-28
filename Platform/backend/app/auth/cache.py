"""实现成功鉴权的有界 LRU/TTL 内存缓存。

职责是避免重复执行 Argon2id 慢哈希；依赖标准库线程锁，不承担用户状态持久化。
缓存只保存已经通过密码校验的用户摘要，`auth_version` 进入键空间以保证状态变更即时失效。
"""

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
import time


@dataclass(frozen=True, slots=True)
class AuthCacheValue:
    """保存鉴权成功后可安全复用的最小用户摘要。"""

    user_id: int
    username: str
    auth_version: int
    role: str
    enterprise_id: int | None
    is_trial: bool


class AuthSuccessCache:
    """提供线程安全的有界 LRU/TTL 成功缓存，过期项只在访问或写入时清理。"""

    def __init__(self, capacity: int = 1024, ttl_seconds: int = 300, clock=time.monotonic):
        """创建缓存；容量或 TTL 非正数抛出 ValueError，clock 仅供确定性测试替换。"""
        if capacity <= 0 or ttl_seconds <= 0:
            raise ValueError("鉴权缓存容量和 TTL 必须为正数")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, AuthCacheValue]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> AuthCacheValue | None:
        """按 HMAC 键读取未过期摘要；未命中或过期返回 None。"""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= self._clock():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def put(self, key: str, value: AuthCacheValue) -> None:
        """写入成功摘要并刷新 LRU 顺序，超过容量时淘汰最久未使用项。"""
        with self._lock:
            self._items[key] = (self._clock() + self.ttl_seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)

    def clear(self) -> None:
        """清空当前进程缓存，供测试、密钥轮换或进程生命周期结束时使用。"""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """返回当前缓存条目数；不保证返回前后跨线程稳定。"""
        with self._lock:
            return len(self._items)
