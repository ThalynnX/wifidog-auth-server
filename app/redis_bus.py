"""跨进程 WebSocket 总线。

单 worker 时内存列表足够；多 worker 时每个进程只持有部分网关长连接。
本模块用 Redis 做三件事：
1. 命令广播：任意 worker 产生的 allow/deny/get_client_info 广播给所有 worker，
   只有持有该网关连接的 worker 会真正 send。
2. 回执广播：网关回复按 req_id 广播，发起请求的 worker 解析自己的 future。
3. 连接注册表：所有 worker 把 WS 连接写入 Redis 有序集合，后台统计到全局连接数。

Redis 不可用时自动退回单进程内存模式，不影响现有功能。
"""
import asyncio
import json
import logging
import os
import time
import uuid

import redis

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover
    aioredis = None

logger = logging.getLogger("wifidog.redis_bus")

REDIS_URL = os.getenv("WIFIDOG_REDIS_URL", "redis://127.0.0.1:6379/0")
CH_COMMAND = "wifidog:ws:cmd"
CH_ACK = "wifidog:ws:ack"
KEY_CONNS = "wifidog:ws:conns"
CONN_TTL = 90.0

WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

_sync_client = None
_async_client = None
_task = None
_local_router = None
_ack_router = None
_started = False


def _sync():
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=1, socket_timeout=1,
        )
    return _sync_client


async def start(local_router, ack_router) -> bool:
    """启动订阅任务；Redis 不可用时返回 False，调用方继续用内存模式。"""
    global _async_client, _task, _local_router, _ack_router, _started
    if _started:
        return True
    _local_router = local_router
    _ack_router = ack_router
    if aioredis is None:
        logger.warning("redis.asyncio 不可用，WebSocket 退回单进程模式")
        return False
    _async_client = aioredis.from_url(
        REDIS_URL, decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=2, socket_timeout=3,
    )
    try:
        await _async_client.ping()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 连接失败，WebSocket 退回单进程模式: %s", e)
        return False
    _started = True
    _task = asyncio.create_task(_subscriber(), name="wifidog-redis-ws-bus")
    logger.info("Redis WS 总线已启动 worker=%s url=%s", WORKER_ID, REDIS_URL)
    return True


async def stop() -> None:
    global _task, _async_client, _started
    _started = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _async_client:
        try:
            await _async_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _async_client = None


async def _subscriber() -> None:
    """独立订阅任务：断线自动重连，不阻塞 FastAPI 请求。"""
    while True:
        pubsub = None
        try:
            pubsub = _async_client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(CH_COMMAND, CH_ACK)
            logger.info("Redis WS 总线订阅成功 worker=%s", WORKER_ID)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message.get("data") or "{}")
                except Exception:  # noqa: BLE001
                    continue
                channel = message.get("channel")
                if channel == CH_COMMAND:
                    if data.get("origin") == WORKER_ID:
                        continue  # 本 worker 已直接发送，避免重复
                    payload = data.get("payload")
                    if isinstance(payload, dict) and _local_router:
                        await _local_router(payload)
                elif channel == CH_ACK:
                    if _ack_router:
                        await _ack_router(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis WS 总线订阅断开，1 秒后重连: %s", e)
            await asyncio.sleep(1)
        finally:
            if pubsub:
                try:
                    await pubsub.close()
                except Exception:  # noqa: BLE001
                    pass


async def publish_command(payload: dict) -> bool:
    if not _started or not _async_client:
        return False
    try:
        await _async_client.publish(
            CH_COMMAND,
            json.dumps({"origin": WORKER_ID, "payload": payload}, ensure_ascii=False),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 发布 WS 命令失败: %s", e)
        return False


async def publish_ack(msg: dict) -> bool:
    if not _started or not _async_client:
        return False
    try:
        await _async_client.publish(
            CH_ACK,
            json.dumps({"worker": WORKER_ID, "msg": msg}, ensure_ascii=False),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 发布 WS 回执失败: %s", e)
        return False


async def register_conn(conn_id: str, gw_id: str = "") -> None:
    if not _started or not _async_client:
        return
    try:
        now = time.time()
        await _async_client.zadd(KEY_CONNS, {conn_id: now})
        await _async_client.hset(f"wifidog:ws:meta:{conn_id}", mapping={
            "worker": WORKER_ID, "gw_id": gw_id or "", "connected_at": str(now),
        })
        await _async_client.expire(f"wifidog:ws:meta:{conn_id}", 120)
    except Exception as e:  # noqa: BLE001
        logger.debug("Redis 注册 WS 连接失败: %s", e)


async def heartbeat_conn(conn_id: str) -> None:
    if not _started or not _async_client:
        return
    try:
        await _async_client.zadd(KEY_CONNS, {conn_id: time.time()})
        await _async_client.expire(f"wifidog:ws:meta:{conn_id}", 120)
    except Exception:  # noqa: BLE001
        pass


async def unregister_conn(conn_id: str) -> None:
    if not _started or not _async_client:
        return
    try:
        await _async_client.zrem(KEY_CONNS, conn_id)
        await _async_client.delete(f"wifidog:ws:meta:{conn_id}")
    except Exception:  # noqa: BLE001
        pass


def global_conn_count() -> int:
    """跨 worker 的网关连接数；Redis 不可用时返回 0，调用方回退本地计数。"""
    try:
        c = _sync()
        c.zremrangebyscore(KEY_CONNS, "-inf", time.time() - CONN_TTL)
        return int(c.zcard(KEY_CONNS) or 0)
    except Exception:  # noqa: BLE001
        return 0


def redis_ok() -> bool:
    try:
        return bool(_sync().ping())
    except Exception:  # noqa: BLE001
        return False
