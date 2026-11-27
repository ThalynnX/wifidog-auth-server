"""apfree-wifidog 持久连接(WebSocket)接口：/ws/wifidogx。

apfree-wifidog 在 Persistent Connection Mode(WebSocket)下，会与认证服务器
保持一条长连接，周期性上报 connect/heartbeat/counters，服务器回执。

这个端点同时承担三件事：
1. 接收网关心跳/上报，维护设备在线状态；
2. 把连接注册到统一 WS 总线，使门户登录后的 allow/deny 指令能真正下发到网关；
3. 处理网关 counters 上报，写入真实流量/速率，并在响应里下发
   auth_code 与 qos（限速），让到期/流量耗尽/拉黑真正断网。

仅维护在线状态而不注册，是此前“登录成功但无法上网”的根因；
只改数据库不处理 counters，是“到期还能一直用、限速不生效”的根因。
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Session as PortalSession
from .. import services_auth as svc
from .. import redis_bus as ws_bus
from ..settings_store import get_run_mode
from .wifidog import _ws_conn_ids, _ws_pending, _ws_register, _ws_unregister

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


def _payload(data: dict) -> dict:
    inner = data.get("data", data)
    return inner if isinstance(inner, dict) else data


def _gateways(data: dict) -> list[dict]:
    payload = _payload(data)
    gws = payload.get("gateway")
    if not isinstance(gws, list):
        gws = data.get("gateway")
    if not isinstance(gws, list):
        return []
    return [g for g in gws if isinstance(g, dict)]


def _msg_op(data: dict) -> str:
    payload = _payload(data)
    return str(payload.get("op") or payload.get("type")
               or data.get("op") or data.get("type") or "").lower()


def _client_gw_id(data: dict) -> str:
    gws = _gateways(data)
    for gw in gws:
        gid = str(gw.get("gw_id") or "").strip()
        if gid:
            return gid
    payload = _payload(data)
    return str(
        payload.get("gw_id") or payload.get("device_id")
        or data.get("gw_id") or data.get("device_id") or ""
    ).strip()


def _report_device_ids(db: Session, data: dict, gid: str) -> None:
    """记住网关自报的 device_id；kickoff 指令需要它，且与 gw_id 不是一回事。"""
    device_id = str(data.get("device_id") or _payload(data).get("device_id") or "").strip()
    if not (gid and device_id):
        return
    try:
        ws_bus._sync().set(f"wifidog:gw:device_id:{gid}", device_id, ex=86400)
    except Exception:  # noqa: BLE001
        pass


def _touch(db: Session, data: dict) -> str:
    payload = _payload(data)
    gid = _client_gw_id(data)
    if not gid:
        return ""
    gws = _gateways(data)
    if not gws:
        gws = [{"gw_id": gid}]
    for gw in gws:
        gw_id = str(gw.get("gw_id") or gid).strip()
        ip = str(gw.get("gw_address_v4") or gw.get("ip") or payload.get("ip")
                 or payload.get("gw_address") or payload.get("gw_ip") or "").strip()
        try:
            svc.touch_device(
                db, gw_id, ip,
                int(payload.get("sys_uptime", 0) or 0),
                int(payload.get("sys_memfree", 0) or 0),
                float(payload.get("sys_load", 0) or 0),
                int(payload.get("wifidog_uptime", 0) or 0),
                auto_register=(get_run_mode(db) == "test"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("WS touch_device failed gid=%s: %s", gw_id, e)
            db.rollback()
    db.commit()
    _report_device_ids(db, data, gid)
    return gid


def _counter_action(cid, auth_code: int, sub=None) -> dict:
    op = {"id": int(cid), "auth_code": int(auth_code)}
    if sub is not None and auth_code == 1:
        down_bps = max(0, int(getattr(sub, "speed_limit_down", 0) or 0)) * 1000
        up_bps = max(0, int(getattr(sub, "speed_limit_up", 0) or 0)) * 1000
        if down_bps or up_bps:
            op.update({
                "qos_present": True,
                "downrate_bps": down_bps,
                "uprate_bps": up_bps,
            })
    return op


def _process_counters(db: Session, data: dict) -> tuple[dict, dict]:
    """处理网关 counters，返回 counters_response 与统计。"""
    now = datetime.now()
    gws = _gateways(data)
    device_id = str(data.get("device_id") or _payload(data).get("device_id") or "")
    stats = {"clients": 0, "allowed": 0, "denied": 0, "qos": 0, "gateways": len(gws)}
    result = []
    for gw in gws:
        gid = str(gw.get("gw_id") or device_id or "").strip()
        clients = gw.get("clients")
        if not isinstance(clients, list):
            clients = []
        ops = []
        for c in clients:
            if not isinstance(c, dict):
                continue
            stats["clients"] += 1
            cid = c.get("id")
            mac = str(c.get("mac") or "").lower()
            ip = str(c.get("ip") or "").strip()
            token = str(c.get("token") or "").strip()
            sess = None
            if token:
                sess = db.query(PortalSession).filter(
                    PortalSession.token == token).order_by(PortalSession.id.desc()).first()
            if sess is None and mac:
                sess = db.query(PortalSession).filter(
                    PortalSession.mac == mac).order_by(PortalSession.id.desc()).first()

            allowed = False
            sub = None
            reason = "no_session"
            if sess is not None:
                reason = ""
                if getattr(sess, "revoked", False):
                    reason = "revoked"
                elif svc.token_expired(sess):
                    reason = "token_expired"
                elif svc.is_mac_blacklisted(db, mac or sess.mac or ""):
                    reason = "mac_blacklisted"
                else:
                    user = db.query(svc.User).filter(svc.User.id == sess.user_id).first() if sess.user_id else None
                    if user:
                        ok, why, sub = svc.check_access(
                            db, user, exclude_session_id=sess.id,
                            exclude_mac=sess.mac or mac)
                        if not ok:
                            reason = why or "no_active_subscription"
                    else:
                        # 白名单固定 token：没有用户也可放行，但仍受 token 有效期约束
                        ok, _why, sub = True, "", None
                    if not reason:
                        allowed = True

            if allowed:
                stats["allowed"] += 1
                try:
                    incoming = int(c.get("incoming_bytes") or 0)
                    outgoing = int(c.get("outgoing_bytes") or 0)
                except (TypeError, ValueError):
                    incoming = outgoing = 0
                svc.update_traffic(db, sess, incoming, outgoing, sub)
                # update_traffic 可能把流量套餐置为耗尽
                if sub is not None and sub.status == 3:
                    allowed = False
                    reason = "traffic_exhausted"
                    stats["allowed"] -= 1
                    stats["denied"] += 1
                else:
                    sess.status = 1
                    sess.stop_at = None
                    sess.last_update_at = now
                    sess.gw_id = gid or sess.gw_id
                    sess.ip = ip or sess.ip
                    user = db.query(svc.User).filter(svc.User.id == sess.user_id).first() if sess.user_id else None
                    svc.mark_authenticated(
                        db, sess, "login",
                        user_id=sess.user_id,
                        phone=(user.phone if user else ""),
                        gw_id=gid, mac=mac or sess.mac or "", ip=ip or sess.ip or "",
                        reason="counters",
                    )
                    if sub is not None and (int(sub.speed_limit_down or 0) or int(sub.speed_limit_up or 0)):
                        stats["qos"] += 1
                    db.commit()
            if not allowed:
                stats["denied"] += 1
                if sess is not None:
                    was_online = sess.status == 1
                    sess.status = 0
                    sess.stop_at = sess.stop_at or now
                    db.commit()
                    if was_online:
                        svc.write_log(
                            db, "auth", "fail", user_id=sess.user_id,
                            phone="", gw_id=gid, mac=mac or sess.mac or "",
                            ip=ip or sess.ip or "", reason=reason or "denied",
                        )
            if cid is not None:
                ops.append(_counter_action(cid, 1 if allowed else 0, sub if allowed else None))
        result.append({"gw_id": gid, "auth_op": ops})
        db.commit()

    response = {
        "req_id": str(data.get("req_id") or ("srv-" + str(int(now.timestamp() * 1000)))),
        "response": "counters_response",
        "data": {"type": "counters_response", "result": result},
    }
    return response, stats


@router.websocket("/ws/wifidogx")
async def wifidogx_ws(websocket: WebSocket):
    await websocket.accept()
    db: Session = SessionLocal()
    gw_id = (websocket.query_params.get("gw_id")
             or websocket.query_params.get("device_id") or "").strip()
    await _ws_register(websocket, gw_id)
    cid = _ws_conn_ids.get(websocket, "")
    logger.info("WS 连接建立 gw_id=%s conn=%s", gw_id or "-", cid)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                if not await _safe_send(websocket, "Pong"):
                    break
                await ws_bus.heartbeat_conn(cid)
                continue

            await ws_bus.heartbeat_conn(cid)

            data = None
            if raw.strip():
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    data = None

            if not isinstance(data, dict):
                await _safe_send(websocket, "Pong")
                continue

            op = _msg_op(data)

            # 网关 resources/counters 上报：顶层 op=counters，带 gateway[].clients[]。
            # 必须回 counters_response（data.result[].auth_op[]），不能只回普通 ack；
            # 网关收到 auth_code=0 才会删除客户端，收到 qos 才会应用 eBPF 限速。
            if op in ("counters", "counters_request"):
                try:
                    resp, stats = _process_counters(db, data)
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    logger.warning("WS counters 处理失败: %s", e)
                    resp, stats = {"req_id": str(data.get("req_id") or "srv-err"),
                                   "response": "counters_response",
                                   "data": {"type": "counters_response", "result": []}}, {}
                await _safe_send(websocket, resp)
                logger.info("WS counters device=%s gateways=%s clients=%s allowed=%s denied=%s qos=%s",
                            data.get("device_id") or "-", stats.get("gateways", 0),
                            stats.get("clients", 0), stats.get("allowed", 0),
                            stats.get("denied", 0), stats.get("qos", 0))
                continue

            gid = _touch(db, data)
            if gid:
                if gid != gw_id:
                    gw_id = gid
                    await ws_bus.register_conn(cid, gw_id)

            rid = data.get("req_id")
            if rid is not None:
                fut = _ws_pending.get(rid)
                if fut is not None and not fut.done():
                    fut.set_result(data)
                if "response" not in data:
                    # 网关请求/心跳：带 req_id 回执，命令总线按 req_id 关联。
                    await _safe_send(websocket, {
                        "req_id": rid, "response": "200", "msg": "ok",
                    })
                else:
                    # 网关对服务端指令的回执：广播给其他 worker。
                    await ws_bus.publish_ack(data)
                continue

            # 无 req_id 的旧协议心跳：保持原有 Pong/OK 兼容。
            if op in ("auth", "heartbeat", "ping", "pong"):
                await _safe_send(websocket, {"type": op, "code": 0, "text": "Pong"})
            else:
                await _safe_send(websocket, {"type": op or "ack", "code": 0, "text": "OK"})
    except WebSocketDisconnect:
        logger.info("WS 断开 gw_id=%s", gw_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("WS 异常 gw_id=%s: %s", gw_id, e)
    finally:
        await _ws_unregister(websocket)
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
