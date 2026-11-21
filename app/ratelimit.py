"""进程内滑动窗口限速器（用于登录/验证码/管理端登录防爆破）。

单实例部署足够；若未来多实例部署，应换成 Redis 实现（接口不变）。
"""
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_buckets: dict = defaultdict(deque)
_MAX_KEYS = 50000  # 防内存膨胀


def _cleanup_locked(now: float):
    if len(_buckets) <= _MAX_KEYS:
        return
    for k in list(_buckets.keys()):
        q = _buckets[k]
        if not q or q[-1] < now - 3600:
            del _buckets[k]
        if len(_buckets) <= _MAX_KEYS:
            break


def check(key: str, max_events: int, window_seconds: int) -> bool:
    """滑动窗口判断：key 在 window 内事件数是否超过 max_events。
    未超限则记录本次并返回 True；超限返回 False（不记录）。"""
    now = time.time()
    with _lock:
        _cleanup_locked(now)
        q = _buckets[key]
        while q and q[0] < now - window_seconds:
            q.popleft()
        if len(q) >= max_events:
            return False
        q.append(now)
        return True


def blocked(key: str, max_events: int, window_seconds: int) -> bool:
    """只判断不记录（用于"失败 N 次后锁定"语义：成功后应调用 reset）。"""
    now = time.time()
    with _lock:
        q = _buckets.get(key)
        if not q:
            return False
        return sum(1 for t in q if t >= now - window_seconds) >= max_events


def reset(key: str):
    with _lock:
        _buckets.pop(key, None)
