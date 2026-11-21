"""wifidog 网关协议接口：ping / login / auth / portal / message。

安全与正确性要点：
- ping 无鉴权但受运行模式约束：正式模式不自动登记未知网关（防垃圾数据）；
  gw_id 做格式校验。
- MAC 白名单仅在 login 阶段放行并纳入会话管理；counters 走正常计费；
  白名单心跳不再逐条写日志（防 AuthLog 膨胀）。
- 上网 token 有效期 PORTAL_TOKEN_EXPIRE，过期会话拒绝重新激活。
"""
import asyncio
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import realtime, services_auth as svc
from ..config import GATEWAY_PROTO
from ..database import get_db
from ..models import Device, Plan, Session as Sess, User
from ..security import new_token
from ..settings_store import get_run_mode, get_site_name, get_site_notice
from ..templates_env import templates

router = APIRouter(prefix="/wifidog", tags=["wifidog"])

logger = logging.getLogger("wifidog")

_GW_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")


def _client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()
    return request.client.host if request.client else ""


def _valid_gw_id(gw_id: str) -> bool:
    return bool(gw_id) and bool(_GW_ID_RE.match(gw_id))


# ------------------------------------------------------------------ 心跳
@router.get("/ping", response_class=PlainTextResponse)
def ping(
    request: Request,
    gw_id: str = "",
    device_id: str = "",  # apfree-wifidog 用 device_id
    sys_uptime: int = 0,
    sys_memfree: int = 0,
    sys_load: float = 0.0,
    wifidog_uptime: int = 0,
    db: Session = Depends(get_db),
):
    """网关心跳，返回 Pong（wifidog 以此判断认证服务器可用性）。"""
    gid = gw_id or device_id  # 兼容 apfree-wifidog (device_id) 与原版 wifidog (gw_id)
    if _valid_gw_id(gid):
        auto_register = get_run_mode(db) == "test"
        try:
            svc.touch_device(db, gid, _client_ip(request), sys_uptime, sys_memfree,
                             sys_load, wifidog_uptime, auto_register=auto_register)
        except Exception as e:  # noqa: BLE001  心跳不应因异常失败
            logger.warning("touch_device failed: %s (gid=%s)", e, gid)
    return "Pong"


# ------------------------------------------------------------------ 登录页
@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    gw_id: str = "",
    gw_address: str = "",
    gw_port: str = "",
    mac: str = "",
    ip: str = "",
    url: str = "",
    db: Session = Depends(get_db),
):
    """用户被网关重定向到这里，展示认证门户。"""
    # IP 白名单（如有线网段）：命中则无感放行，不出登录页，直接回网关完成认证
    user_ip = ip or _client_ip(request)
    if user_ip and svc.is_ip_whitelisted(db, user_ip):
        wtok = svc.ip_whitelist_token(user_ip)
        return RedirectResponse(
            build_gateway_redirect(gw_address, gw_port, wtok), status_code=302
        )
    # MAC 白名单设备同样无感放行
    mac_n = (mac or "").lower()
    if mac_n and svc.is_mac_whitelisted(db, mac_n):
        wtok = f"wl-{mac_n.replace(':', '').replace('-', '')}"
        return RedirectResponse(
            build_gateway_redirect(gw_address, gw_port, wtok), status_code=302
        )

    plans = db.query(Plan).filter(Plan.status == 1).order_by(Plan.sort.desc(), Plan.id.asc()).all()
    dev = db.query(Device).filter(Device.gw_id == gw_id).first() if _valid_gw_id(gw_id) else None
    return templates.TemplateResponse(
        request=request,
        name="portal/login.html",
        context={
            "request": request,
            "gw_id": gw_id,
            "gw_address": gw_address,
            "gw_port": gw_port,
            "mac": mac,
            "ip": ip,
            "url": url,
            "plans": plans,
            "device": dev,
            "site_name": get_site_name(db),
            "site_notice": get_site_notice(db),
            "change_token": "",
            "bound_mac": "",
            "cur_mac": mac,
        },
    )


# ------------------------------------------------------------------ 认证
@router.api_route("/auth", methods=["GET", "POST"], response_class=PlainTextResponse)
async def auth(
    request: Request,
    stage: str = Query("", description="login|counters|logout"),
    ip: str = "",
    mac: str = "",
    token: str = "",
    incoming: int = 0,
    outgoing: int = 0,
    gw_id: str = "",
    device_id: str = "",
    db: Session = Depends(get_db),
):
    """网关调用本接口完成认证/计费/下线。login/logout/counters 响应必须是 `Auth: 1` / `Auth: 0`
    （apfree-wifidog 7.10 用 strstr(buf, "Auth: ") 解析）；counters_v2 走 JSON 批量协议。"""
    mac = (mac or "").lower()
    gw_id = gw_id or device_id

    # apfree-wifidog >= 7.10：counters_v2 用 POST 提交 JSON 客户端数组，
    # 响应必须是 {"result":[{"gw_id":..,"auth_op":[{"id":..,"auth_code":..}]}]}
    if stage == "counters_v2":
        return await _auth_counters_v2(request, db, gw_id)

    # -1) MAC 黑名单：最高优先级，命中即拒绝（白名单/有效订阅/免认证网段都拦不住）
    if mac and svc.is_mac_blacklisted(db, mac):
        svc.write_log(db, "denied", "fail", gw_id=gw_id, mac=mac, ip=ip, reason="mac blacklisted")
        # 在线的直接踢掉
        for s in db.query(Sess).filter(Sess.mac == mac, Sess.status == 1).all():
            s.status = 0
            s.stop_at = datetime.now()
        db.commit()
        return "Auth: 0\n"

    # 0) IP 白名单（如有线网段 192.168.6.0/24）：login/counters/logout 全部直通
    # 安全校验：token 携带的 IP 与请求 IP 都必须真实命中白名单，防止伪造 wlip-* token
    wl_ip = ip if (ip and svc.is_ip_whitelisted(db, ip)) else None
    if not wl_ip and (token or "").startswith("wlip-"):
        cand = token[5:].replace("-", ".")
        if svc.is_ip_whitelisted(db, cand):
            wl_ip = cand
    if wl_ip:
        wtok = svc.ip_whitelist_token(wl_ip)
        if stage == "login":
            svc.create_session_by_token(db, wtok, user_id=0, gw_id=gw_id, mac=mac, ip=wl_ip)
        elif stage == "counters":
            s = svc.create_session_by_token(db, wtok, user_id=0, gw_id=gw_id, mac=mac, ip=wl_ip)
            svc.update_traffic(db, s, incoming, outgoing, None)
        elif stage == "logout":
            svc.close_session(db, wtok)
        return "Auth: 1\n"

    # 1) MAC 白名单：放行并纳入会话管理（不再绕过计费/管理）
    if mac and svc.is_mac_whitelisted(db, mac) and stage == "login":
        svc.create_whitelist_session(db, gw_id, mac, ip)
        return "Auth: 1\n"

    if stage == "login":
        return _auth_login(db, token, mac, ip, gw_id)
    if stage == "counters":
        return _auth_counters(db, token, mac, ip, incoming, outgoing, gw_id)
    if stage == "logout":
        svc.close_session(db, token)
        return "Auth: 1\n"

    return "Auth: 0\n"


def _v2_gateways(data) -> list:
    """从 counters_v2 请求体里取出 gateway 列表。

    实际报文形如：
    {"device_id":"ap001","gateway":[{"gw_id":"<mac>","gw_channel":"wan","clients":[...]}]}
    """
    if isinstance(data, list):
        return [g for g in data if isinstance(g, dict)]
    if not isinstance(data, dict):
        return []
    gw = data.get("gateway")
    if isinstance(gw, list):
        return [g for g in gw if isinstance(g, dict)]
    if isinstance(gw, dict):
        return [gw]
    if "clients" in data or "id" in data:
        return [data]
    return []


def _v2_client_allowed(db, c: dict, gw_id: str) -> bool:
    """判断 counters_v2 上报的单个客户端是否仍允许上网，并顺带更新会话/流量。"""
    token = str(c.get("token") or "")
    mac = str(c.get("mac") or "").lower()
    ip = str(c.get("ip") or "")
    incoming = int(c.get("incoming_bytes") or c.get("incoming") or 0)
    outgoing = int(c.get("outgoing_bytes") or c.get("outgoing") or 0)

    # 黑名单最优先：命中即拒绝，并关掉其在线会话
    if mac and svc.is_mac_blacklisted(db, mac):
        for s in db.query(Sess).filter(Sess.mac == mac, Sess.status == 1).all():
            s.status = 0
            s.stop_at = datetime.now()
        db.commit()
        return False

    if ip and svc.is_ip_whitelisted(db, ip):
        wtok = svc.ip_whitelist_token(ip)
        s = svc.create_session_by_token(db, wtok, user_id=0, gw_id=gw_id, mac=mac, ip=ip)
        svc.update_traffic(db, s, incoming, outgoing, None)
        return True

    if mac and svc.is_mac_whitelisted(db, mac):
        svc.create_whitelist_session(db, gw_id, mac, ip)
        return True

    res = _auth_counters(db, token, mac, ip, incoming, outgoing, gw_id)
    return "Auth: 1" in res


async def _auth_counters_v2(request: Request, db, gw_id: str) -> str:
    """apfree-wifidog 7.10 的 counters_v2 批量计费/状态同步接口。

    请求体为 JSON（客户端数组或包含客户端数组的对象），响应必须是：
    {"result":[{"gw_id":"<gw>","auth_op":[{"id":<client_id>,"auth_code":<0|1>}]}]}
    """
    try:
        raw = (await request.body()).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        raw = ""

    try:
        data = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        data = None

    logger.debug("counters_v2 body: %s", raw[:2000])

    # 会话里记录的网关标识用 device_id（如 ap001），而不是 body 里网关的 MAC，
    # 保证后台「在线会话」的网关列与设备列表一致。
    device_id = ""
    if isinstance(data, dict):
        device_id = str(data.get("device_id") or "")

    result = []
    seen = 0
    clients_total = 0
    for g in _v2_gateways(data):
        gid = str(g.get("gw_id") or gw_id or "default")
        sess_gw = device_id or gw_id or gid
        clients = [c for c in (g.get("clients") or []) if isinstance(c, dict)]
        clients_total += len(clients)
        ops = []
        for c in clients:
            cid = c.get("id")
            if cid is None:
                continue
            seen += 1
            try:
                allowed = _v2_client_allowed(db, c, sess_gw)
            except Exception as e:  # noqa: BLE001  单个客户端异常不影响整批
                logger.warning("counters_v2 client error: %s", e)
                allowed = False
            ops.append({"id": cid, "auth_code": 1 if allowed else 0})
        result.append({"gw_id": gid, "auth_op": ops})

    if not result:
        result = [{"gw_id": gw_id or "default", "auth_op": []}]
    if clients_total and not seen:
        logger.warning("counters_v2: clients present but no id parsed, raw=%s", raw[:500])

    return json.dumps({"result": result})


def _auth_login(db, token: str, mac: str, ip: str, gw_id: str) -> str:
    if not token:
        svc.write_log(db, "login", "fail", gw_id=gw_id, mac=mac, ip=ip, reason="no token")
        return "Auth: 0\n"
    sess = svc.get_session(db, token)
    if not sess:
        svc.write_log(db, "login", "fail", gw_id=gw_id, mac=mac, ip=ip, reason="invalid token")
        return "Auth: 0\n"

    # 2) token 有效期：过期会话不可再激活（P1-6）
    if sess.status != 1 and svc.token_expired(sess):
        svc.write_log(db, "login", "fail", user_id=sess.user_id, gw_id=gw_id, mac=mac,
                      ip=ip, reason="token expired")
        return "Auth: 0\n"
    if sess.status == 1 and svc.token_expired(sess):
        sess.status = 0
        sess.stop_at = datetime.now()
        db.commit()
        return "Auth: 0\n"

    user = db.query(User).filter(User.id == sess.user_id).first()
    ok, reason, _sub = svc.check_access(db, user, exclude_mac=mac)
    if not ok:
        sess.status = 0
        db.commit()
        svc.write_log(db, "login", "fail", user_id=user.id if user else None,
                      gw_id=gw_id, mac=mac, ip=ip, reason=reason)
        return "Auth: 0\n"

    svc.create_session(db, user, gw_id, mac, ip, token)
    svc.write_log(db, "login", "success", user_id=user.id, gw_id=gw_id, mac=mac, ip=ip)
    return "Auth: 1\n"


def _auth_counters(db, token: str, mac: str, ip: str, incoming: int, outgoing: int, gw_id: str) -> str:
    """周期性流量上报：更新流量并在套餐耗尽/过期/超时/禁用时踢下线。"""
    sess = svc.get_session(db, token)
    if not sess or sess.status != 1:
        return "Auth: 0\n"
    if svc.token_expired(sess):
        sess.status = 0
        sess.stop_at = datetime.now()
        db.commit()
        return "Auth: 0\n"

    user = None
    if sess.user_id:
        user = db.query(User).filter(User.id == sess.user_id).first()

    if user:
        ok, reason, sub = svc.check_access(db, user, exclude_session_id=sess.id)
        svc.update_traffic(db, sess, incoming, outgoing, sub)
        if not ok:
            sess.status = 0
            sess.stop_at = datetime.now()
            db.commit()
            svc.write_log(db, "denied", "fail", user_id=sess.user_id, gw_id=gw_id, mac=mac,
                          ip=ip, reason=reason)
            return "Auth: 0\n"
    else:
        # 白名单设备：只更新会话活跃时间与累计流量
        svc.update_traffic(db, sess, incoming, outgoing, None)
    return "Auth: 1\n"


# ------------------------------------------------------------------ 成功页 / 消息
@router.get("/portal", response_class=HTMLResponse)
def portal_page(request: Request, gw_id: str = "", token: str = "", db: Session = Depends(get_db)):
    """认证成功后可选跳转的落地页。"""
    sess = svc.get_session(db, token) if token else None
    user = db.query(User).filter(User.id == sess.user_id).first() if sess else None
    sub = svc.get_active_subscription(db, user.id) if user else None
    online = db.query(func.count(Sess.id)).filter(Sess.status == 1).scalar() or 0
    return templates.TemplateResponse(
        request=request,
        name="portal/success.html",
        context={"request": request, "gw_id": gw_id, "token": token,
                 "session": sess, "user": user, "sub": sub, "online": int(online)},
    )


@router.get("/message", response_class=PlainTextResponse)
def message(gw_id: str = "", db: Session = Depends(get_db)):
    """网关拉取的提示消息（后台公告）。"""
    return get_site_notice(db) or ""


# ------------------------------------------------------------------ 供 portal 使用的工具
_GWSTATS_KEY = "<your-gwstats-key>"


@router.post("/gwstats")
async def gwstats(request: Request):
    """网关实时统计上报：网关原样 POST `wdctlx status client` 的 JSON。"""
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": "bad json"}
    key = request.headers.get("x-gw-key") or (data.get("key") if isinstance(data, dict) else "") or ""
    if key != _GWSTATS_KEY:
        return {"ok": False, "msg": "bad key"}
    clients = data.get("clients") if isinstance(data, dict) else None
    gw_id = (data.get("gw_id") if isinstance(data, dict) else "") or ""
    if isinstance(clients, list) and any(isinstance(c, dict) and "traffic" in c for c in clients):
        n = realtime.ingest_gateway(clients, gw_id)
    else:
        n = realtime.ingest(gw_id, clients)
    return {"ok": True, "n": n}


def build_gateway_redirect(gw_address: str, gw_port: str, token: str, gw_id: str = "") -> str:
    port = gw_port or "2060"
    qs = f"token={token}"
    if gw_id:
        qs += f"&gw_id={gw_id}"
    return f"{GATEWAY_PROTO}://{gw_address}:{port}/wifidog/auth?{qs}"


def issue_token(db, user_id: int, gw_id: str = "", mac: str = "") -> str:
    """签发上网令牌并预建会话（网关确认后置为在线）。"""
    token = new_token(24)
    sess = Sess(token=token, user_id=user_id, gw_id=gw_id, mac=(mac or "").lower(),
                status=0, start_at=datetime.now(), last_update_at=datetime.now())
    db.add(sess)
    db.commit()
    return token


# ------------------------------------------------------------------ WebSocket 下行指令
_ws_links: list = []


def _ws_register(ws) -> None:
    _ws_links.append(ws)


def _ws_unregister(ws) -> None:
    try:
        _ws_links.remove(ws)
    except ValueError:
        pass


_ws_pending: dict = {}


async def ws_call(payload: dict, timeout: float = 3.0):
    """向网关于请求并等待回执（按 req_id 关联）。"""
    rid = payload.get("req_id") or ("srv-" + new_token(8))
    payload["req_id"] = rid
    fut = asyncio.get_running_loop().create_future()
    _ws_pending[rid] = fut
    try:
        if not await push_op(payload):
            return None
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _ws_pending.pop(rid, None)


def _dig_data(resp):
    """兼容 pkg 包裹层级，取出 handler 返回的 data。"""
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else {}


async def fetch_client_ip(mac: str) -> str:
    mac = (mac or "").lower()
    if not mac:
        return ""
    resp = await ws_call({"op": "get_client_info", "mac": mac}, 3.0)
    data = _dig_data(resp)
    return (data.get("ip") or "") if isinstance(data, dict) else ""


async def push_op(payload: dict) -> int:
    """向所有在线网关推送一条下行指令，返回成功发送的连接数。"""
    sent = 0
    for ws in list(_ws_links):
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            sent += 1
        except Exception:  # noqa: BLE001
            _ws_unregister(ws)
    logger.info("WS 下发 %s -> %d 个连接, payload=%s",
                payload.get("op"), sent, json.dumps(payload, ensure_ascii=False)[:200])
    return sent


async def push_client_allow(mac: str, ip: str = "", gw_id: str = "", token: str = "") -> int:
    """云模式放行：门户登录/开通套餐后，由后台向网关推 op=auth。"""
    if not ip:
        try:
            ip = await fetch_client_ip(mac)
        except Exception:  # noqa: BLE001
            logger.exception("查询客户端 IP 失败")
    return await push_op({
        "req_id": "srv-" + new_token(8),
        "op": "auth",
        "token": token or "",
        "client_ip": ip or "",
        "client_mac": (mac or "").lower(),
        "gw_id": gw_id or "",
        "once_auth": False,
    })


async def push_client_deny(mac: str, ip: str = "", gw_id: str = "") -> int:
    return await push_op({
        "req_id": "srv-" + new_token(8),
        "op": "del_auth_client",
        "client_ip": ip or "",
        "client_mac": (mac or "").lower(),
        "gw_id": gw_id or "",
        "gw_channel": "apfree",
    })


# ------------------------------------------------------------------ WebSocket 长连接（apfree-wifidog 9.x）
@router.websocket("/ws")
async def gateway_ws(websocket: WebSocket):
    """apfree-wifidog 9.x 云模式长连接：握手成功网关才把认证服务器标记为在线。"""
    await websocket.accept()
    _ws_register(websocket)
    logger.info("网关 WebSocket 连接建立: %s", websocket.client)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                msg = None
            if isinstance(msg, dict):
                rid0 = msg.get("req_id")
                # 网关心跳/连接消息需要服务端回执，否则 60s 后判定认证服务器离线
                if rid0 is not None and "response" not in msg:
                    try:
                        await websocket.send_text(json.dumps({
                            "req_id": rid0,
                            "response": "200",
                            "msg": "ok",
                        }))
                    except Exception:  # noqa: BLE001
                        break
                rid = msg.get("req_id")
                fut = _ws_pending.get(rid) if rid else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                continue
            logger.debug("网关上行: %s", raw[:160])
    except WebSocketDisconnect:
        logger.info("网关 WebSocket 断开: %s", websocket.client)
    finally:
        _ws_unregister(websocket)
