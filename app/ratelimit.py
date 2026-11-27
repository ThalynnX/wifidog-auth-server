"""分布式滑动窗口限流器：优先 Redis，Redis 不可用时回退进程内内存。

多 worker/多实例下内存限流会把额度放大 N 倍；生产必须走 Redis。
Redis 使用 Lua 原子脚本，避免 ZCARD 与 ZADD 的竞态。
"""
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

import redis

from .config import REDIS_URL

logger = logging.getLogger("wifidog.ratelimit")

_lock = threading.Lock()
_buckets: dict = defaultdict(deque)
_MAX_KEYS = 50000
_redis = None
_redis_failed_until = 0.0

_LUA_CHECK = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_events = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local n = redis.call('ZCARD', key)
if n >= max_events then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 5)
return 1
"""


def _client():
    global _redis, _redis_failed_until
    if _redis is not None:
        return _redis
    if time.time() < _redis_failed_until:
        return None
    try:
        c = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=1, socket_timeout=1,
        )
        c.ping()
        _redis = c
        return c
    except Exception as e:  # noqa: BLE001
        _redis_failed_until = time.time() + 30
        logger.warning("Redis 限流不可用，回退进程内: %s", e)
        return None


def _cleanup_locked(now: float):
    if len(_buckets) <= _MAX_KEYS:
        return
    for k in list(_buckets.keys()):
        q = _buckets[k]
        if not q or q[-1] < now - 3600:
            del _buckets[k]
        if len(_buckets) <= _MAX_KEYS:
            break


def _mem_check(key: str, max_events: int, window_seconds: int) -> bool:
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


def check(key: str, max_events: int, window_seconds: int) -> bool:
    """未超限则记录本次并返回 True；超限返回 False（不记录）。"""
    r = _client()
    if r is not None:
        try:
            now = time.time()
            member = f"{now}:{uuid.uuid4().hex[:12]}"
            ok = r.eval(_LUA_CHECK, 1, "wd:rl:" + key, now, window_seconds, max_events, member)
            return bool(ok)
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 限流失败，回退进程内: %s", e)
    return _mem_check(key, max_events, window_seconds)


def blocked(key: str, max_events: int, window_seconds: int) -> bool:
    """只判断不记录（用于“失败 N 次后锁定”语义：成功后应 reset）。"""
    r = _client()
    if r is not None:
        try:
            now = time.time()
            r.zremrangebyscore("wd:rl:" + key, 0, now - window_seconds)
            return int(r.zcard("wd:rl:" + key) or 0) >= max_events
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis blocked 失败，回退进程内: %s", e)
    now = time.time()
    with _lock:
        q = _buckets.get(key)
        if not q:
            return False
        return sum(1 for t in q if t >= now - window_seconds) >= max_events


def reset(key: str):
    r = _client()
    if r is not None:
        try:
            r.delete("wd:rl:" + key)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis reset 失败，回退进程内: %s", e)
    with _lock:
        _buckets.pop(key, None)
