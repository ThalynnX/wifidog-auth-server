"""wifidog 网关协议接口：ping / login / auth / portal / message。

安全与正确性要点：
- ping 无鉴权但受运行模式约束：正式模式不自动登记未知网关（防垃圾数据）；
  gw_id 做格式校验。
- MAC 白名单仅在 login 阶段放行并纳入会话管理；counters 走正常计费；
  白名单心跳不再逐条写日志（防 AuthLog 膨胀）。
- 上网 token 有效期 PORTAL_TOKEN_EXPIRE，过期会话拒绝重新激活。
"""
import asyncio
import ipaddress
import json
import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import alipay, netutil, realtime, redis_bus as ws_bus, services_auth as svc
from ..config import GATEWAY_PROTO, GWSTATS_KEY, WS_ALLOW_IPS, WS_REQUIRE_SECRET
from ..database import get_db
from ..models import Device, Plan, Session as Sess, User
from ..security import new_token
from ..settings_store import get_run_mode, get_site_name, get_site_notice, sms_provider
from ..templates_env import templates

router = APIRouter(prefix="/wifidog", tags=["wifidog"])

logger = logging.getLogger("wifidog")

_GW_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")


def _client_ip(request: Request) -> str:
    return netutil.client_ip(request)


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
    if gw_id and not svc.gateway_active(db, gw_id):
        return HTMLResponse("网关未登记或已停用，请联系管理员", status_code=403)
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
    sms_mode = sms_provider(db)
    sms_local_display = not sms_mode
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
            "sms_available": True,
            "sms_local_display": sms_local_display,
            "sms_test_mode": sms_mode == "mock",
            "alipay_ready": alipay.oauth_ready(),
            "alipay_pay_ready": alipay.pay_ready(),
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
    # 未知/禁用网关不允许认证；防止伪造 gw_id 在后台刷会话。
    if not svc.gateway_active(db, gw_id):
        svc.write_log(db, "denied", "fail", gw_id=gw_id, mac=mac, ip=ip,
                      reason="gateway_unknown_or_disabled")
        return "Auth: 0\n"

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


def _v2_client_allowed(db, c: dict, gw_id: str) -> tuple[bool, object]:
    """判断 counters_v2 上报的单个客户端是否仍允许上网，并返回其有效套餐。"""
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
        return False, None

    if ip and svc.is_ip_whitelisted(db, ip):
        wtok = svc.ip_whitelist_token(ip)
        s = svc.create_session_by_token(db, wtok, user_id=0, gw_id=gw_id, mac=mac, ip=ip)
        svc.update_traffic(db, s, incoming, outgoing, None)
        return True, None

    if mac and svc.is_mac_whitelisted(db, mac):
        svc.create_whitelist_session(db, gw_id, mac, ip)
        return True, None

    res = _auth_counters(db, token, mac, ip, incoming, outgoing, gw_id)
    if "Auth: 1" not in res:
        return False, None
    sess = db.query(Sess).filter(Sess.token == token).first() if token else None
    user = db.query(User).filter(User.id == sess.user_id).first() if sess and sess.user_id else None
    return True, (svc.get_active_subscription(db, user.id) if user else None)


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
    active_gw = svc.gateway_active(db, device_id or gw_id)

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
            sub = None
            try:
                allowed, sub = _v2_client_allowed(db, c, sess_gw)
                if not active_gw:
                    allowed, sub = False, None
            except Exception as e:  # noqa: BLE001  单个客户端异常不影响整批
                logger.warning("counters_v2 client error: %s", e)
                allowed = False
            op = {"id": cid, "auth_code": 1 if allowed else 0}
            if allowed and sub is not None:
                down_bps = max(0, int(getattr(sub, "speed_limit_down", 0) or 0)) * 1000
                up_bps = max(0, int(getattr(sub, "speed_limit_up", 0) or 0)) * 1000
                if down_bps or up_bps:
                    op.update({"qos_present": True, "downrate_bps": down_bps, "uprate_bps": up_bps})
            ops.append(op)
        result.append({"gw_id": gid, "auth_op": ops})

    if not result:
        result = [{"gw_id": gw_id or "default", "auth_op": []}]
    if clients_total and not seen:
        logger.warning("counters_v2: clients present but no id parsed, raw=%s", raw[:500])

    return json.dumps({"result": result})


def _auth_login(db, token: str, mac: str, ip: str, gw_id: str) -> str:
    if not svc.gateway_active(db, gw_id):
        svc.write_log(db, "login", "fail", gw_id=gw_id, mac=mac, ip=ip,
                      reason="gateway_unknown_or_disabled")
        return "Auth: 0\n"
    if not token:
        svc.write_log(db, "login", "fail", gw_id=gw_id, mac=mac, ip=ip, reason="no token")
        return "Auth: 0\n"
    sess = svc.get_session(db, token)
    if not sess:
        svc.write_log(db, "login", "fail", gw_id=gw_id, mac=mac, ip=ip, reason="invalid token")
        return "Auth: 0\n"
    if getattr(sess, "revoked", False):
        svc.write_log(db, "login", "fail", user_id=sess.user_id, gw_id=gw_id, mac=mac,
                      ip=ip, reason="session revoked")
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
    svc.mark_authenticated(db, sess, "login", user_id=user.id, phone=user.phone or "",
                          gw_id=gw_id, mac=mac, ip=ip, reason="gateway_auth")
    return "Auth: 1\n"


def _auth_counters(db, token: str, mac: str, ip: str, incoming: int, outgoing: int, gw_id: str) -> str:
    """周期性流量上报：更新流量并在套餐耗尽/过期/超时/禁用时踢下线。"""
    if not svc.gateway_active(db, gw_id):
        return "Auth: 0\n"
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
        if ok and sub is not None and sub.status == 3:
            ok, reason = False, "流量已用尽"
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
    online = svc.online_device_count(db, user.id) if user else 0
    return templates.TemplateResponse(
        request=request,
        name="portal/success.html",
        context={"request": request, "gw_id": gw_id, "token": token,
                 "session": sess, "user": user, "sub": sub, "online": int(online),
                 "site_name": get_site_name(db)},
    )


@router.get("/message", response_class=PlainTextResponse)
def message(gw_id: str = "", db: Session = Depends(get_db)):
    """网关拉取的提示消息（后台公告）。"""
    return get_site_notice(db) or ""


# ------------------------------------------------------------------ 供 portal 使用的工具


@router.post("/gwstats")
async def gwstats(request: Request, db: Session = Depends(get_db)):
    """网关实时统计上报：网关原样 POST `wdctlx status client` 的 JSON。

    除写入内存实时缓存外，同步回写 sessions / devices：
    - 有 token 的终端把会话置为在线并更新流量；
    - 用 gw_id 登记网关，避免后台“设备管理”始终为空。
    """
    raw = await request.body()
    try:
        data = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": "bad json"}
    key = request.headers.get("x-gw-key") or (data.get("key") if isinstance(data, dict) else "") or ""
    if not GWSTATS_KEY or key != GWSTATS_KEY:
        return {"ok": False, "msg": "bad key"}

    clients = data.get("clients") if isinstance(data, dict) else None
    if not isinstance(clients, list):
        clients = []
    gw_id = (data.get("gw_id") if isinstance(data, dict) else "") or ""
    if not gw_id:
        for c in clients:
            if isinstance(c, dict) and c.get("gw_id"):
                gw_id = str(c.get("gw_id") or "")
                break

    def _to_int(v):
        try:
            return int(float(v or 0))
        except (TypeError, ValueError):
            return 0

    # 登记/刷新网关在线状态；gwstats 使用固定密钥，来源可信
    if _valid_gw_id(gw_id):
        try:
            svc.touch_device(db, gw_id, _client_ip(request), 0, 0, 0.0, 0, auto_register=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("gwstats touch_device failed: %s (gid=%s)", e, gw_id)

    active_gw = svc.gateway_active(db, gw_id)
    # 同步终端会话：gwstats 的 status=0 表示空闲，不等于离线；
    # 但必须重新校验黑名单/套餐/设备数，不能把已拉黑或已过期的会话写回在线。
    synced = 0
    denied = 0
    reconciled = 0
    seen_tokens = set()
    seen_macs = set()
    deny_targets = []
    now = datetime.now()
    for c in clients:
        if not isinstance(c, dict):
            continue
        mac = (c.get("mac") or "").strip().lower()
        if not mac:
            continue
        ip = str(c.get("ip") or "")
        c_gw = str(c.get("gw_id") or gw_id or "")
        token = str(c.get("token") or "")
        if token:
            seen_tokens.add(token)
        seen_macs.add(mac)
        t = c.get("traffic") or {}
        incoming = _to_int(t.get("download_bytes")) + _to_int(t.get("download_bytes_v6"))
        outgoing = _to_int(t.get("upload_bytes")) + _to_int(t.get("upload_bytes_v6"))
        if not token:
            continue

        sess = db.query(Sess).filter(Sess.token == token).first()
        if not sess:
            continue

        was_online = sess.status == 1
        user = db.query(User).filter(User.id == sess.user_id).first() if sess.user_id else None
        allowed = True
        deny_reason = ""
        sub = None

        if not active_gw:
            allowed = False
            deny_reason = "gateway_unknown_or_disabled"
        elif svc.is_mac_blacklisted(db, mac):
            allowed = False
            deny_reason = "mac_blacklisted"
        elif getattr(sess, "revoked", False):
            allowed = False
            deny_reason = "session_revoked"
        elif not sess.user_id:
            # 白名单固定会话：没有账号，直接放行
            allowed = True
        elif user is None:
            allowed = False
            deny_reason = "user_not_found"
        elif svc.token_expired(sess):
            allowed = False
            deny_reason = "token_expired"
        elif user:
            ok, why, sub = svc.check_access(db, user, exclude_session_id=sess.id)
            if not ok:
                allowed = False
                deny_reason = why

        sess.gw_id = c_gw or sess.gw_id
        sess.mac = mac
        sess.ip = ip or sess.ip

        if allowed:
            sess.status = 1
            sess.stop_at = None
            svc.update_traffic(db, sess, incoming, outgoing, sub)
            if sub is not None and sub.status == 3:
                allowed = False
                deny_reason = "traffic_exhausted"
            if allowed:
                synced += 1
                if not getattr(sess, "auth_logged", False):
                    svc.mark_authenticated(
                        db, sess, "login" if user else "device_auto",
                        user_id=sess.user_id, phone=(user.phone or "") if user else "",
                        gw_id=sess.gw_id, mac=mac, ip=ip, reason="gwstats",
                    )
        if not allowed:
            if was_online:
                denied += 1
                deny_targets.append((mac, ip, c_gw))
                svc.write_log(db, "denied", "fail", user_id=sess.user_id,
                              phone=(user.phone or "") if user else "",
                              gw_id=c_gw, mac=mac, ip=ip, reason=deny_reason)
            sess.status = 0
            sess.stop_at = now
            db.commit()

    # 路由器上报的 clients 是当前全量终端；超过 20 秒仍未出现的会话视为已断开。
    # 这样网关断线/终端离开时，后台不会继续显示“在线”。
    if _valid_gw_id(gw_id):
        stale_cutoff = now - timedelta(seconds=20)
        rows = db.query(Sess).filter(Sess.gw_id == gw_id, Sess.status == 1).all()
        for old in rows:
            if old.token in seen_tokens or old.mac in seen_macs:
                continue
            if (old.last_update_at or old.start_at or stale_cutoff) < stale_cutoff:
                old.status = 0
                old.stop_at = now
                reconciled += 1
        if reconciled:
            db.commit()

    if deny_targets:
        svc.enqueue_kick(db, deny_targets, reason="gwstats_deny")
    for mac, ip, c_gw in deny_targets:
        try:
            await push_client_deny(mac, ip, c_gw)
        except Exception as e:  # noqa: BLE001
            logger.warning("gwstats push deny failed: %s (mac=%s)", e, mac)

    n = realtime.ingest_gateway(clients, gw_id)
    return {"ok": True, "n": n, "synced": synced, "denied": denied, "reconciled": reconciled}

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
                status=0, start_at=datetime.now(), last_update_at=datetime.now(),
                auth_logged=False, revoked=False, down_mbps=0, up_mbps=0)
    db.add(sess)
    db.commit()
    return token


# ------------------------------------------------------------------ WebSocket 下行指令
# 单进程本地连接；多 worker 时由 Redis 总线广播到持有网关连接的进程。
_ws_links: list = []
_ws_gw: dict = {}
_ws_conn_ids: dict = {}
_ws_pending: dict = {}


async def _ws_register(ws, gw_id: str = "") -> None:
    _ws_links.append(ws)
    _ws_gw[ws] = (gw_id or "").strip()
    cid = f"{ws_bus.WORKER_ID}:{new_token(10)}"
    _ws_conn_ids[ws] = cid
    await ws_bus.register_conn(cid, gw_id)


async def _ws_unregister(ws) -> None:
    try:
        _ws_links.remove(ws)
    except ValueError:
        pass
    _ws_gw.pop(ws, None)
    cid = _ws_conn_ids.pop(ws, "")
    if cid:
        await ws_bus.unregister_conn(cid)


def ws_client_count() -> int:
    """全局网关长连接数；Redis 不可用时回退本进程计数。"""
    return max(len(_ws_links), ws_bus.global_conn_count())


async def ws_call(payload: dict, timeout: float = 3.0, target_gw_id: str = ""):
    """向指定 gw_id 的网关发请求并等待回执（按 req_id 关联，支持跨 worker）。"""
    rid = payload.get("req_id") or ("srv-" + new_token(8))
    payload["req_id"] = rid
    fut = asyncio.get_running_loop().create_future()
    _ws_pending[rid] = fut
    try:
        if not await push_op(payload, target_gw_id):
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


async def fetch_client_ip(mac: str, gw_id: str = "") -> str:
    mac = (mac or "").lower()
    if not mac:
        return ""
    resp = await ws_call({"op": "get_client_info", "mac": mac}, 3.0, gw_id)
    data = _dig_data(resp)
    return (data.get("ip") or "") if isinstance(data, dict) else ""


async def _ws_deliver_local(payload: dict, target_gw_id: str = "") -> int:
    """只向本 worker 持有的、且 gw_id 匹配目标的网关连接发送。

    旧实现遍历所有连接广播，导致 A 网关的踢线/限速指令被发到 B 网关，
    多网关时可能误踢同 MAC 的客户端。这里按连接携带的 gw_id 严格过滤。
    """
    target = (target_gw_id or payload.get("_gw_id") or "").strip()
    if not target:
        logger.warning("拒绝无目标 gw_id 的 WS 指令 op=%s", payload.get("op"))
        return 0
    sent = 0
    for ws in list(_ws_links):
        if _ws_gw.get(ws) != target:
            continue
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            sent += 1
        except Exception:  # noqa: BLE001
            await _ws_unregister(ws)
    if sent:
        logger.info("WS 本地下发 %s -> gw=%s %d 个连接 worker=%s",
                    payload.get("op"), target, sent, ws_bus.WORKER_ID)
    return sent


async def ws_bus_deliver(payload: dict) -> None:
    """Redis 总线回调：收到其他 worker 的命令后，只发本进程匹配 gw_id 的连接。"""
    await _ws_deliver_local(payload, payload.get("_gw_id") or "")


async def ws_bus_ack(data: dict) -> None:
    """Redis 总线回调：解析其他 worker 送回的网关回执。"""
    msg = data.get("msg") if isinstance(data, dict) else None
    if not isinstance(msg, dict):
        return
    rid = msg.get("req_id")
    fut = _ws_pending.get(rid) if rid else None
    if fut is not None and not fut.done():
        fut.set_result(msg)


async def push_op(payload: dict, target_gw_id: str = "") -> int:
    """向指定 gw_id 下发指令：本进程直接发，同时通过 Redis 通知其他 worker。"""
    target = (target_gw_id or "").strip()
    if not target:
        logger.warning("拒绝无目标 gw_id 的 WS 指令 op=%s", payload.get("op"))
        return 0
    payload = dict(payload)
    payload["_gw_id"] = target
    local_sent = await _ws_deliver_local(payload, target)
    published = await ws_bus.publish_command(payload)
    if local_sent:
        return local_sent
    if published and ws_bus.global_conn_count() > 0:
        return 1
    logger.info("WS 下发 %s -> gw=%s 0 个连接（无网关在线）", payload.get("op"), target)
    return 0


async def _get_client_id(mac: str, gw_id: str = "") -> int | None:
    """向指定网关查询客户端内部 id；counters_response 的 auth_op 需要这个 id。"""
    resp = await ws_call({"op": "get_client_info", "mac": (mac or "").lower()}, 2.5, gw_id)
    logger.info("客户端查询 mac=%s resp=%s", mac, str(resp)[:300])
    data = _dig_data(resp)
    try:
        cid = data.get("id")
        return int(cid) if cid is not None else None
    except (TypeError, ValueError):
        return None


async def _send_counter_action(cid: int, gw_id: str = "", auth_code: int = 0,
                               down_bps: int = 0, up_bps: int = 0) -> int:
    """通过 counters_response 直接操作网关客户端。

    网关支持的踢下线不是 del_auth_client，而是 counters_response 里
    auth_op[].auth_code=0；它同时会删除防火墙放行规则与 eBPF QoS。
    """
    op = {"id": int(cid), "auth_code": int(auth_code)}
    if auth_code == 1 and (down_bps > 0 or up_bps > 0):
        op.update({
            "qos_present": True,
            "downrate_bps": max(0, int(down_bps)),
            "uprate_bps": max(0, int(up_bps)),
        })
    return await push_op({
        "op": "counters_response",
        "req_id": "srv-" + new_token(8),
        "response": "counters_response",
        "data": {
            "type": "counters_response",
            "result": [{"gw_id": gw_id or "", "auth_op": [op]}],
        },
    }, gw_id)


async def _apply_qos_after_auth(mac: str, gw_id: str, down_kbps: int, up_kbps: int) -> None:
    """放行后补一次 counters_response，把套餐限速立即写进网关 eBPF。"""
    down_bps = max(0, int(down_kbps or 0)) * 1000
    up_bps = max(0, int(up_kbps or 0)) * 1000
    if not (down_bps or up_bps):
        return
    cid = None
    for _ in range(4):
        await asyncio.sleep(0.35)
        cid = await _get_client_id(mac, gw_id)
        if cid is not None:
            break
    if cid is None:
        logger.info("QoS 下发延迟：网关暂未返回客户端 id mac=%s", mac)
        return
    sent = await _send_counter_action(cid, gw_id, 1, down_bps, up_bps)
    logger.info("QoS 下发 mac=%s down_bps=%s up_bps=%s sent=%s", mac, down_bps, up_bps, sent)


async def push_client_allow(mac: str, ip: str = "", gw_id: str = "", token: str = "",
                           down_kbps: int = 0, up_kbps: int = 0) -> int:
    """云模式放行：门户登录/开通套餐后，由后台向网关推 op=auth。

    auth 指令本身只负责放行；限速字段一并带上做兼容，同时异步补发
    counters_response，让网关立即应用 eBPF QoS。
    """
    target_gw = (gw_id or "").strip()
    if not target_gw:
        logger.warning("push_client_allow 缺少 gw_id mac=%s", mac)
        return 0
    if not ip:
        try:
            ip = await fetch_client_ip(mac, target_gw)
        except Exception:  # noqa: BLE001
            logger.exception("查询客户端 IP 失败")
    down_bps = max(0, int(down_kbps or 0)) * 1000
    up_bps = max(0, int(up_kbps or 0)) * 1000
    sent = await push_op({
        "req_id": "srv-" + new_token(8),
        "op": "auth",
        "token": token or "",
        "client_ip": ip or "",
        "client_mac": (mac or "").lower(),
        "gw_id": gw_id or "",
        "once_auth": False,
        "qos_present": bool(down_bps or up_bps),
        "downrate_bps": down_bps,
        "uprate_bps": up_bps,
    }, target_gw)
    if sent and (down_bps or up_bps):
        asyncio.create_task(_apply_qos_after_auth((mac or "").lower(), gw_id or "",
                                                  int(down_kbps or 0), int(up_kbps or 0)))
    return sent


async def push_client_deny(mac: str, ip: str = "", gw_id: str = "") -> int:
    """踢下线：先查网关客户端 id，再用 auth_code=0 的 counters_response 移除。"""
    mac = (mac or "").lower()
    target_gw = (gw_id or "").strip()
    if not mac or not target_gw:
        logger.info("踢下线跳过 mac=%s gw=%s：目标不完整", mac, target_gw)
        return 0
    cid = await _get_client_id(mac, target_gw)
    if cid is None:
        logger.info("踢下线跳过 mac=%s gw=%s：网关已无该客户端", mac, target_gw)
        return 0
    sent = await _send_counter_action(cid, target_gw, 0)
    logger.info("踢下线 mac=%s ip=%s gw=%s cid=%s sent=%s", mac, ip or "", target_gw, cid, sent)
    return sent


async def push_client_deny_ack(mac: str, ip: str = "", gw_id: str = "") -> tuple[bool, str]:
    """带确认的踢线：查询 id -> 下发 auth_code=0 -> 再查询一次确认客户端消失。

    返回 (是否确认成功, 说明)。网关离线/超时返回 False，由 KickTask 重试。
    """
    mac = (mac or "").lower()
    target_gw = (gw_id or "").strip()
    if not mac or not target_gw:
        return False, "缺少 MAC 或 gw_id"
    resp = await ws_call({"op": "get_client_info", "mac": mac}, 2.5, target_gw)
    if resp is None:
        return False, "网关未响应（可能离线）"
    data = _dig_data(resp)
    cid = data.get("id") if isinstance(data, dict) else None
    if cid is None:
        return True, "网关已无该客户端（无需下发）"
    try:
        cid_i = int(cid)
    except (TypeError, ValueError):
        return False, f"网关返回的客户端 id 非法: {cid!r}"
    sent = await _send_counter_action(cid_i, target_gw, 0)
    if not sent:
        return False, "指令未送达网关"
    await asyncio.sleep(0.25)
    resp2 = await ws_call({"op": "get_client_info", "mac": mac}, 2.5, target_gw)
    if resp2 is None:
        return False, "下发后网关无确认响应"
    data2 = _dig_data(resp2)
    again = data2.get("id") if isinstance(data2, dict) else None
    if again is None:
        return True, "已确认客户端从网关移除"
    return False, f"网关仍返回客户端 id={again}，稍后重试"


# ------------------------------------------------------------------ WebSocket 鉴权
def _ws_ip_allowed(ip: str) -> bool:
    if not WS_ALLOW_IPS:
        return True
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    for item in [x.strip() for x in WS_ALLOW_IPS.split(",") if x.strip()]:
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


async def _ws_authorize(
    websocket: WebSocket, gw_id: str, device_id: str = "",
    enforce_device_id: bool = False,
) -> tuple[bool, str]:
    """校验网关 WS：必须已登记且启用；可配共享 secret 与来源 IP 白名单。

    secret 通过 query `secret=` 或 header `x-gateway-secret` 传递。
    APFree 的 WS 地址由 ws_server->path 决定，可在 path 后追加 ?secret=...
    `device_id` 来自 APFree 首帧（get_device_id），首次认证时绑定，
    之后不一致直接拒绝，避免仅凭可猜的 gw_id 冒充网关。
    """
    from ..database import SessionLocal
    from ..models import Device

    if not _valid_gw_id(gw_id):
        return False, "gw_id 格式错误"
    ip = netutil.client_ip(websocket)
    if not _ws_ip_allowed(ip):
        logger.warning("拒绝 WS 来源 IP=%s gw=%s（不在白名单）", ip, gw_id)
        return False, "来源 IP 不在白名单"
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.gw_id == gw_id).first()
        if not dev or dev.status != 1:
            logger.warning("拒绝 WS 未知/禁用网关 gw=%s ip=%s", gw_id, ip)
            return False, "网关未登记或已停用"
        provided = (
            websocket.query_params.get("secret")
            or websocket.headers.get("x-gateway-secret") or ""
        ).strip()
        expected = (getattr(dev, "secret", "") or "").strip()
        if expected:
            if not provided or not secrets.compare_digest(provided, expected):
                logger.warning("拒绝 WS secret 不匹配 gw=%s ip=%s", gw_id, ip)
                return False, "网关密钥错误"
        elif WS_REQUIRE_SECRET:
            logger.warning("拒绝 WS 未配置 secret gw=%s（WS_REQUIRE_SECRET=1）", gw_id)
            return False, "网关未配置共享密钥"

        reported = (device_id or "").strip()
        if reported.upper() == "UNKNOWN":
            reported = ""
        bound = (getattr(dev, "device_id", "") or "").strip()
        if enforce_device_id and not reported:
            logger.warning("拒绝 WS 未上报 device_id gw=%s ip=%s", gw_id, ip)
            return False, "网关未上报设备标识"
        if bound:
            if reported and not secrets.compare_digest(reported, bound):
                logger.warning("拒绝 WS device_id 不匹配 gw=%s ip=%s", gw_id, ip)
                return False, "网关设备标识不匹配"
            if not reported:
                logger.warning("拒绝 WS device_id 缺失 gw=%s ip=%s", gw_id, ip)
                return False, "网关设备标识缺失"
        elif reported:
            # TOFU：首次连接绑定，后续只能由同一 device_id 使用该 gw_id。
            dev.device_id = reported
            db.commit()
            logger.info("WS 首次绑定 device_id gw=%s device=%s", gw_id, reported)
        return True, ""
    finally:
        db.close()


# ------------------------------------------------------------------ WebSocket 长连接（apfree-wifidog 9.x）
@router.websocket("/ws")
async def gateway_ws(websocket: WebSocket):
    """apfree-wifidog 9.x 云模式长连接：握手成功网关才把认证服务器标记为在线。"""
    gw_id = (websocket.query_params.get("gw_id")
             or websocket.query_params.get("device_id") or "").strip()
    ok, why = await _ws_authorize(websocket, gw_id)
    if not ok:
        await websocket.close(code=1008, reason=why)
        return
    await websocket.accept()
    await _ws_register(websocket, gw_id)
    cid = _ws_conn_ids.get(websocket, "")
    logger.info("网关 WebSocket 连接建立: %s worker=%s conn=%s",
                websocket.client, ws_bus.WORKER_ID, cid)
    try:
        while True:
            raw = await websocket.receive_text()
            await ws_bus.heartbeat_conn(cid)
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
                # 跨 worker：本进程可能不是发起请求的 worker
                if rid is not None and "response" in msg:
                    await ws_bus.publish_ack(msg)
                continue
            logger.debug("网关上行: %s", raw[:160])
    except WebSocketDisconnect:
        logger.info("网关 WebSocket 断开: %s", websocket.client)
    finally:
        await _ws_unregister(websocket)
