"""apfree-wifidog 持久连接(WebSocket)接口：/ws/wifidogx。

apfree-wifidog 在 Persistent Connection Mode(WebSocket)下，会与认证服务器
保持一条长连接，周期性上报 auth/heartbeat(JSON)，服务器回 ack。
本协议实现最小可用闭环：接收上报 -> 维护设备在线(touch_device) -> 回 Pong/OK。
服务器亦可主动向设备下发 command(预留)。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import services_auth as svc
from ..settings_store import get_run_mode

logger = logging.getLogger("wifidog.ws")
router = APIRouter()

HEARTBEAT_INTERVAL = 30  # 秒：超过该时间未收到设备消息则主动探测


async def _safe_send(websocket: WebSocket, obj) -> bool:
    try:
        if isinstance(obj, str):
            await websocket.send_text(obj)
        else:
            await websocket.send_json(obj)
        return True
    except Exception:  # noqa: BLE001
        return False


@router.websocket("/ws/wifidogx")
async def wifidogx_ws(websocket: WebSocket):
    await websocket.accept()
    db: Session = SessionLocal()
    gw_id = ""
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                # 心跳探测：超时未收到上报，主动发 Pong
                if not await _safe_send(websocket, "Pong"):
                    break
                continue

            # 解析设备上报(兼容 JSON 与纯文本)
            data = None
            if raw.strip():
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    data = None

            if isinstance(data, dict):
                msg_type = str(data.get("type", "")).lower()
                payload = data.get("data", data)
                if not isinstance(payload, dict):
                    payload = data
                gid = (payload.get("gw_id") or payload.get("device_id")
                       or data.get("gw_id") or data.get("device_id") or "").strip()
                if gid:
                    gw_id = gid
                    ip = (payload.get("ip") or payload.get("gw_address")
                          or payload.get("gw_ip") or "").strip()
                    try:
                        svc.touch_device(
                            db, gid, ip,
                            int(payload.get("sys_uptime", 0) or 0),
                            int(payload.get("sys_memfree", 0) or 0),
                            float(payload.get("sys_load", 0) or 0),
                            int(payload.get("wifidog_uptime", 0) or 0),
                            auto_register=(get_run_mode(db) == "test"),
                        )
                        db.commit()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("WS touch_device failed gid=%s: %s", gid, e)
                # 回 ack
                if msg_type in ("auth", "heartbeat", "ping", "pong"):
                    await _safe_send(websocket, {"type": msg_type, "code": 0, "text": "Pong"})
                else:
                    await _safe_send(websocket, {"type": msg_type or "ack", "code": 0, "text": "OK"})
            else:
                # 非 JSON(如纯文本 Pong)原样回 Pong
                await _safe_send(websocket, "Pong")
    except WebSocketDisconnect:
        logger.info("WS 断开 gw_id=%s", gw_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("WS 异常 gw_id=%s: %s", gw_id, e)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
