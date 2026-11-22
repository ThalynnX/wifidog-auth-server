"""管理后台：鉴权、统计与各项管理接口。"""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import alipay, ratelimit, redis_bus as ws_bus, services_auth as svc, settings_store
from ..config import ADMIN_TOKEN_EXPIRE, DEVICE_OFFLINE_TIMEOUT, ONLINE_WINDOW_SECONDS
from ..database import get_db
from ..models import (
    Admin, AuthLog, Device, IpWhitelist, MacBlacklist, MacWhitelist, Order, Plan,
    Session as Sess, Setting, Subscription, User,
)
from ..security import hash_password, make_signed_token, parse_signed_token, verify_password
from ..templates_env import templates
from . import wifidog as wd

router = APIRouter(prefix="/admin", tags=["admin"])

_LOGIN_LOCK = (5, 600)  # 失败 5 次，锁定 10 分钟
_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$")


# ------------------------------------------------------------------ 鉴权
def current_admin(request: Request, db: Session = Depends(get_db)) -> Admin:
    token = request.cookies.get("admin_token") or request.headers.get("x-admin-token") or ""
    username = parse_signed_token(token, "admin")
    if not username:
        raise HTTPException(status_code=401, detail="未登录")
    adm = db.query(Admin).filter(Admin.username == username, Admin.status == 1).first()
    if not adm:
        raise HTTPException(status_code=401, detail="账号不可用")
    return adm


class LoginIn(BaseModel):
    username: str
    password: str


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    from ..config import SITE_NAME

    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={"request": request, "site_name": SITE_NAME},
    )


@router.post("/api/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else ""
    lock_key = f"admlogin:{ip}"
    if ratelimit.blocked(lock_key, _LOGIN_LOCK[0], _LOGIN_LOCK[1]):
        return JSONResponse({"ok": False, "msg": "失败次数过多，请10分钟后再试"}, status_code=429)

    adm = db.query(Admin).filter(Admin.username == data.username).first()
    if not adm or not verify_password(data.password, adm.password_hash):
        ratelimit.check(lock_key, _LOGIN_LOCK[0] * 10, _LOGIN_LOCK[1])  # 记录失败
        return JSONResponse({"ok": False, "msg": "用户名或密码错误"})

    ratelimit.reset(lock_key)
    adm.last_login_at = datetime.now()
    adm.last_login_ip = ip
    db.commit()
    token = make_signed_token("admin", adm.username, ADMIN_TOKEN_EXPIRE)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax", max_age=ADMIN_TOKEN_EXPIRE)
    return resp


@router.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("admin_token")
    return resp


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    try:
        current_admin(request, db)
    except HTTPException:
        return RedirectResponse(url="/admin/login", status_code=302)
    resp = templates.TemplateResponse(
        request=request,
        name="admin/index.html",
        context={"request": request, "site_name": settings_store.get_site_name(db)},
    )
    # 后台页面禁止缓存：避免改版后浏览器仍用旧页面导致"点了没反应"
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


# ------------------------------------------------------------------ 统计
@router.get("/api/stats")
def stats(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()
    live_cutoff = now - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    device_cutoff = now - timedelta(seconds=DEVICE_OFFLINE_TIMEOUT)
    auth_actions = ("login", "device_auto", "device_rebind")

    online_users = db.query(func.count(func.distinct(Sess.user_id))).filter(
        Sess.status == 1, Sess.last_update_at >= live_cutoff, Sess.user_id > 0
    ).scalar() or 0
    online_sessions = db.query(func.count(Sess.id)).filter(
        Sess.status == 1, Sess.last_update_at >= live_cutoff
    ).scalar() or 0
    devices_total = db.query(func.count(Device.id)).scalar() or 0
    devices_online = db.query(func.count(Device.id)).filter(
        Device.online == 1, Device.status == 1, Device.last_seen_at >= device_cutoff
    ).scalar() or 0
    users_total = db.query(func.count(User.id)).scalar() or 0
    users_today = db.query(func.count(User.id)).filter(User.created_at >= today).scalar() or 0

    auth_today = db.query(func.count(AuthLog.id)).filter(
        AuthLog.created_at >= today, AuthLog.action.in_(auth_actions), AuthLog.result == "success"
    ).scalar() or 0

    income_today = db.query(func.coalesce(func.sum(Order.amount), 0)).filter(
        Order.status == 1, Order.paid_at >= today
    ).scalar() or 0
    income_total = db.query(func.coalesce(func.sum(Order.amount), 0)).filter(
        Order.status == 1
    ).scalar() or 0
    orders_pending = db.query(func.count(Order.id)).filter(Order.status == 0).scalar() or 0
    last_gateway = db.query(func.max(Device.last_seen_at)).scalar()

    # 近 7 日认证趋势：一次查询按日分组（兼容 SQLite date() 与 MySQL DATE()）
    week_ago = today - timedelta(days=6)
    rows = (
        db.query(func.date(AuthLog.created_at), func.count(AuthLog.id))
        .filter(
            AuthLog.created_at >= week_ago, AuthLog.created_at < today + timedelta(days=1),
            AuthLog.action.in_(auth_actions), AuthLog.result == "success",
        )
        .group_by(func.date(AuthLog.created_at))
        .all()
    )
    by_day = {str(d): c for d, c in rows}
    trend = []
    for i in range(6, -1, -1):
        d0 = (datetime.now() - timedelta(days=i)).date()
        trend.append({"date": d0.strftime("%m-%d"), "count": by_day.get(str(d0), 0)})

    return {
        "ok": True,
        "online_users": online_users,
        "online_sessions": online_sessions,
        "devices_total": devices_total,
        "devices_online": devices_online,
        "users_total": users_total,
        "users_today": users_today,
        "auth_today": auth_today,
        "income_today": income_today,
        "income_total": income_total,
        "orders_pending": orders_pending,
        "trend": trend,
        "ws_clients": wd.ws_client_count(),
        "redis_ok": ws_bus.redis_ok(),
        "last_gateway_report": (
            last_gateway.strftime("%Y-%m-%d %H:%M:%S") if last_gateway else ""
        ),
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "db_ok": True,
        "online_window_seconds": ONLINE_WINDOW_SECONDS,
        "device_window_seconds": DEVICE_OFFLINE_TIMEOUT,
    }


# ------------------------------------------------------------------ 用户管理
@router.get("/api/users")
def users(request: Request, q: str = "", page: int = 1, size: int = 20, db: Session = Depends(get_db)):
    current_admin(request, db)
    page = max(1, page)
    size = min(100, max(1, size))
    query = db.query(User)
    if q:
        query = query.filter(User.phone.like(f"%{q}%") | User.username.like(f"%{q}%") | User.nickname.like(f"%{q}%"))
    total = query.count()
    rows = query.order_by(User.id.desc()).offset((page - 1) * size).limit(size).all()

    # 批量取有效订阅，避免逐用户多次查询
    ids = [u.id for u in rows]
    sub_map = {}
    if ids:
        now = datetime.now()
        subs = (
            db.query(Subscription)
            .filter(Subscription.user_id.in_(ids), Subscription.status == 1)
            .order_by(Subscription.expire_at.is_(None).desc(), Subscription.expire_at.desc())
            .all()
        )
        for s in subs:
            if s.user_id in sub_map:
                continue
            expired = s.expire_at and s.expire_at < now
            exhausted = s.traffic_total_mb > 0 and (s.traffic_used_mb or 0) >= s.traffic_total_mb
            if expired or exhausted:
                continue
            sub_map[s.user_id] = s
    out = []
    for u in rows:
        sub = sub_map.get(u.id)
        out.append({
            "id": u.id, "phone": u.phone, "username": u.username, "nickname": u.display_name,
            "status": u.status, "balance": u.balance,
            "plan": sub.plan_name if sub else "",
            "expire_at": sub.expire_at.strftime("%Y-%m-%d %H:%M") if sub and sub.expire_at else "",
            "online": svc.online_device_count(db, u.id),
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M"),
        })
    return {"ok": True, "total": total, "rows": out}


@router.post("/api/users/{uid}/toggle")
def user_toggle(uid: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    u = db.query(User).filter(User.id == uid).first()
    if not u:
        return {"ok": False, "msg": "用户不存在"}
    u.status = 0 if u.status == 1 else 1
    db.commit()
    if u.status == 0:  # 禁用时踢下线
        for s in db.query(Sess).filter(Sess.user_id == uid, Sess.status == 1).all():
            s.status = 0
            s.stop_at = datetime.now()
            s.revoked = True
        db.commit()
    return {"ok": True, "status": u.status}


# ------------------------------------------------------------------ 设备管理
@router.get("/api/devices")
def devices(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    now = datetime.now()
    cutoff = now - timedelta(seconds=DEVICE_OFFLINE_TIMEOUT)
    rows = db.query(Device).order_by(Device.id.desc()).limit(500).all()
    return {"ok": True, "rows": [{
        "id": d.id, "gw_id": d.gw_id, "name": d.name, "location": d.location,
        "status": d.status,
        "online": 1 if (d.online == 1 and d.status == 1 and d.last_seen_at and d.last_seen_at >= cutoff) else 0,
        "last_seen_at": d.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if d.last_seen_at else "",
        "last_ip": d.last_ip, "sys_load": d.sys_load, "remark": d.remark,
    } for d in rows]}


class DeviceIn(BaseModel):
    gw_id: str
    name: str = ""
    location: str = ""
    remark: str = ""
    status: int = 1


@router.post("/api/devices")
def device_save(data: DeviceIn, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    if not re.match(r"^[A-Za-z0-9_\-.]{1,64}$", data.gw_id):
        return {"ok": False, "msg": "网关 ID 仅允许字母/数字/._- 且不超过 64 字符"}
    d = db.query(Device).filter(Device.gw_id == data.gw_id).first()
    if not d:
        d = Device(gw_id=data.gw_id)
        db.add(d)
    d.name = data.name          # 允许清空（不再 or 保留旧值）
    d.location = data.location
    d.remark = data.remark
    d.status = data.status
    db.commit()
    return {"ok": True, "id": d.id}


@router.delete("/api/devices/{did}")
def device_del(did: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    d = db.query(Device).filter(Device.id == did).first()
    if d:
        db.delete(d)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 套餐管理
@router.get("/api/plans")
def plans(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    rows = db.query(Plan).order_by(Plan.sort.desc(), Plan.id.asc()).all()
    return {"ok": True, "rows": [{
        "id": p.id, "name": p.name, "type": p.type, "price": p.price,
        "duration_minutes": p.duration_minutes, "traffic_mb": p.traffic_mb,
        "valid_days": p.valid_days, "device_limit": p.device_limit,
        "speed_limit_up": p.speed_limit_up, "speed_limit_down": p.speed_limit_down,
        "status": p.status, "sort": p.sort, "description": p.description,
    } for p in rows]}


class PlanIn(BaseModel):
    id: int | None = None
    name: str
    type: str = "time"
    price: int = 0
    duration_minutes: int = 0
    traffic_mb: int = 0
    valid_days: int = 0
    device_limit: int = 0
    speed_limit_up: int = 0
    speed_limit_down: int = 0
    status: int = 1
    sort: int = 0
    description: str = ""


@router.post("/api/plans")
def plan_save(data: PlanIn, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    if data.type not in ("time", "flow", "period", "free"):
        return {"ok": False, "msg": "无效的套餐类型"}
    if data.price < 0 or min(data.duration_minutes, data.traffic_mb, data.valid_days) < 0:
        return {"ok": False, "msg": "参数不能为负数"}
    if data.id:
        p = db.query(Plan).filter(Plan.id == data.id).first()
        if not p:
            return {"ok": False, "msg": "套餐不存在"}
    else:
        p = Plan()
        db.add(p)
    for k, v in data.dict(exclude={"id"}).items():
        setattr(p, k, v)
    db.commit()
    return {"ok": True, "id": p.id}


@router.delete("/api/plans/{pid}")
def plan_del(pid: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    p = db.query(Plan).filter(Plan.id == pid).first()
    if not p:
        return {"ok": True}
    # 仅系统内置的免费体验套餐（隐藏、status=0）受保护；自建的 free 套餐允许删除
    if p.type == "free" and p.status == 0:
        return {"ok": False, "msg": "这是系统内置的免费体验套餐（隐藏），不可删除；如需关闭免费体验，请到「系统设置」把免费体验分钟数设为 0"}
    # 有生效中的订阅引用时禁止删除，避免数据悬空
    in_use = db.query(func.count(Subscription.id)).filter(
        Subscription.plan_id == pid, Subscription.status == 1
    ).scalar() or 0
    if in_use:
        return {"ok": False, "msg": f"该套餐有 {in_use} 个生效中的订阅，请先等订阅过期或下架"}
    db.delete(p)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 订单
@router.get("/api/orders")
def orders(request: Request, page: int = 1, size: int = 20, status: int = -1, db: Session = Depends(get_db)):
    current_admin(request, db)
    page = max(1, page)
    size = min(100, max(1, size))
    q = db.query(Order)
    if status >= 0:
        q = q.filter(Order.status == status)
    total = q.count()
    rows = q.order_by(Order.id.desc()).offset((page - 1) * size).limit(size).all()
    # 仅查询本页涉及的用户（不再全表加载）
    uids = {o.user_id for o in rows}
    umap = {}
    if uids:
        umap = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()}
    return {"ok": True, "total": total, "rows": [{
        "id": o.id, "order_no": o.order_no, "plan_name": o.plan_name,
        "amount": o.amount, "status": o.status, "pay_type": o.pay_type,
        "phone": umap[o.user_id].phone if o.user_id in umap else "",
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
        "paid_at": o.paid_at.strftime("%Y-%m-%d %H:%M") if o.paid_at else "",
        "refund_at": o.refund_at.strftime("%Y-%m-%d %H:%M") if o.refund_at else "",
        "refund_reason": o.refund_reason or "",
        "refund_no": o.refund_no or "",
        "trade_no": o.trade_no or "",
        "notify_id": o.notify_id or "",
        "alipay_trade_status": o.alipay_trade_status or "",
        "last_query_at": o.last_query_at.strftime("%Y-%m-%d %H:%M") if o.last_query_at else "",
    } for o in rows]}


# ------------------------------------------------------------------ 订单退款
class RefundIn(BaseModel):
    reason: str = ""
    channel_refunded: bool = False   # 微信等未接入自动退款的渠道：已在渠道后台退过款，这里只登记


@router.post("/api/orders/{oid}/refund")
def order_refund(oid: int, data: RefundIn, request: Request, db: Session = Depends(get_db)):
    """退款/退单：支付宝订单自动原路退款；微信订单仍需渠道后台退款后登记。"""
    adm = current_admin(request, db)
    order = db.query(Order).filter(Order.id == oid).first()
    if not order:
        return {"ok": False, "msg": "订单不存在"}
    auto_refunded = False
    if order.status == 1 and order.pay_type == "alipay" and not data.channel_refunded:
        if not alipay.pay_ready():
            return {"ok": False, "msg": "支付宝参数未配置，无法自动退款"}
        refund_no = order.refund_no or ("RF" + order.order_no)
        try:
            alipay.refund_trade(order.order_no, order.amount, data.reason or "管理员退款", refund_no)
        except alipay.AlipayError as exc:
            return {"ok": False, "msg": f"支付宝退款失败：{exc}"}
        order.refund_no = refund_no
        db.commit()
        auto_refunded = True
    ok, msg = svc.refund_order(db, order, data.reason, data.channel_refunded or auto_refunded)
    if ok:
        svc.write_log(db, "refund", "ok", user_id=order.user_id,
                      reason=f"{order.order_no} {data.reason} by {adm.username}"[:250])
    return {"ok": ok, "msg": msg}


# ------------------------------------------------------------------ 在线会话
@router.get("/api/sessions")
def sessions(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    cutoff = datetime.now() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    rows = db.query(Sess).filter(
        Sess.status == 1, Sess.last_update_at >= cutoff
    ).order_by(Sess.last_update_at.desc()).limit(500).all()
    uids = {s.user_id for s in rows if s.user_id}
    umap = {}
    if uids:
        umap = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()}
    return {"ok": True, "rows": [{
        "id": s.id, "token": s.token[:12] + "...", "gw_id": s.gw_id, "mac": s.mac, "ip": s.ip,
        "phone": umap[s.user_id].phone if s.user_id in umap else "(白名单)",
        "start_at": s.start_at.strftime("%Y-%m-%d %H:%M:%S"),
        "last_update_at": s.last_update_at.strftime("%Y-%m-%d %H:%M:%S"),
        "incoming": s.incoming_bytes, "outgoing": s.outgoing_bytes,
    } for s in rows]}


@router.post("/api/sessions/{sid}/kick")
async def kick(sid: int, request: Request, db: Session = Depends(get_db)):
    """踢下线。body {"block": true} 时同时把该 MAC 加入黑名单（对免认证网段同样有效）。"""
    current_admin(request, db)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    block = bool((data or {}).get("block"))

    s = db.query(Sess).filter(Sess.id == sid).first()
    if not s:
        return {"ok": False, "msg": "会话不存在"}
    s.status = 0
    s.stop_at = datetime.now()
    s.revoked = True
    db.commit()

    # 立即通知网关删除该客户端授权；只改数据库会等下一次 counters 才断开。
    if s.mac:
        try:
            await wd.push_client_deny(s.mac, s.ip, s.gw_id)
        except Exception:  # noqa: BLE001
            pass
    msg = "已踢下线"
    if block:
        if s.mac:
            if svc.blacklist_mac(db, s.mac, note=f"后台踢线拉黑（会话 {sid}）"):
                msg = "已踢下线并加入黑名单，该设备将无法再上网"
            else:
                msg = "已踢下线（该 MAC 已在黑名单中）"
        else:
            msg = "已踢下线（该会话没有 MAC，无法拉黑）"
    return {"ok": True, "msg": msg}


class MacIn(BaseModel):
    mac: str
    note: str = ""


# ------------------------------------------------------------------ MAC 黑名单（强制断网）
@router.get("/api/blacklist")
def blacklist(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    rows = db.query(MacBlacklist).order_by(MacBlacklist.id.desc()).all()
    return {"ok": True, "rows": [{"id": w.id, "mac": w.mac, "note": w.note,
                                  "created_at": w.created_at.strftime("%Y-%m-%d %H:%M")} for w in rows]}


@router.post("/api/blacklist")
async def blacklist_add(data: MacIn, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    mac = data.mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        return {"ok": False, "msg": "MAC 格式错误，应为 00:11:22:33:44:55"}
    if not svc.blacklist_mac(db, mac, note=data.note):
        return {"ok": False, "msg": "该 MAC 已在黑名单中"}
    # 拉黑立即生效：数据库会话下线 + 网关即时 del_auth_client
    old = db.query(Sess).filter(Sess.mac == mac, Sess.status == 1).order_by(Sess.id.desc()).first()
    ip = old.ip if old else ""
    gw = old.gw_id if old else ""
    svc.close_sessions_for_mac(db, mac)
    try:
        await wd.push_client_deny(mac, ip, gw)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


@router.delete("/api/blacklist/{bid}")
def blacklist_del(bid: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    w = db.query(MacBlacklist).filter(MacBlacklist.id == bid).first()
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 白名单
@router.get("/api/whitelist")
def whitelist(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    rows = db.query(MacWhitelist).order_by(MacWhitelist.id.desc()).all()
    return {"ok": True, "rows": [{"id": w.id, "mac": w.mac, "note": w.note, "status": w.status} for w in rows]}


@router.post("/api/whitelist")
def whitelist_add(data: MacIn, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    mac = data.mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        return {"ok": False, "msg": "MAC 格式错误，应为 00:11:22:33:44:55"}
    if db.query(MacWhitelist).filter(MacWhitelist.mac == mac).first():
        return {"ok": False, "msg": "该 MAC 已存在"}
    db.add(MacWhitelist(mac=mac, note=data.note, status=1))
    db.commit()
    return {"ok": True}


@router.delete("/api/whitelist/{wid}")
def whitelist_del(wid: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    w = db.query(MacWhitelist).filter(MacWhitelist.id == wid).first()
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ IP 免认证白名单（单 IP / CIDR 网段，可用于有线网段整体放行）
@router.get("/api/ipwhitelist")
def ip_whitelist(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    rows = db.query(IpWhitelist).order_by(IpWhitelist.id.desc()).all()
    return {"ok": True, "rows": [{"id": w.id, "cidr": w.cidr, "note": w.note, "status": w.status} for w in rows]}


class IpIn(BaseModel):
    cidr: str
    note: str = ""


@router.post("/api/ipwhitelist")
def ip_whitelist_add(data: IpIn, request: Request, db: Session = Depends(get_db)):
    import ipaddress

    current_admin(request, db)
    cidr = data.cidr.strip()
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return {"ok": False, "msg": "格式错误：填单 IP（192.168.6.100）或网段（192.168.6.0/24）"}
    norm = str(net)
    if db.query(IpWhitelist).filter(IpWhitelist.cidr == norm).first():
        return {"ok": False, "msg": "该 IP/网段已存在"}
    # 重叠检查：新规则若已被现有网段覆盖，提示冗余
    existing = db.query(IpWhitelist).filter(IpWhitelist.status == 1).all()
    for r in existing:
        try:
            e = ipaddress.ip_network(r.cidr, strict=False)
        except ValueError:
            continue
        if net.subnet_of(e):
            return {"ok": False, "msg": f"该范围已被 {r.cidr} 覆盖，无需重复添加"}
    db.add(IpWhitelist(cidr=norm, note=data.note, status=1))
    db.commit()
    return {"ok": True, "cidr": norm}


@router.delete("/api/ipwhitelist/{wid}")
def ip_whitelist_del(wid: int, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    w = db.query(IpWhitelist).filter(IpWhitelist.id == wid).first()
    if w:
        db.delete(w)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 认证日志
@router.get("/api/logs")
def logs(request: Request, page: int = 1, size: int = 30, db: Session = Depends(get_db)):
    current_admin(request, db)
    page = max(1, page)
    size = min(100, max(1, size))
    q = db.query(AuthLog)
    total = q.count()
    rows = q.order_by(AuthLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"ok": True, "total": total, "rows": [{
        "id": l.id, "phone": l.phone, "gw_id": l.gw_id, "mac": l.mac, "ip": l.ip,
        "action": l.action, "result": l.result, "reason": l.reason,
        "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for l in rows]}


# ------------------------------------------------------------------ 系统设置（已接线，保存即生效）
@router.get("/api/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    from .. import config

    rows = db.query(Setting).all()
    stored = {r.key: r.value for r in rows}
    return {
        "ok": True,
        "settings": {
            "run_mode": settings_store.get_run_mode(db),
            "free_trial_minutes": str(settings_store.get_free_trial_minutes(db)),
            "default_device_limit": str(settings_store.get_device_limit(db)),
            "site_notice": stored.get("site_notice", ""),
            "site_name": stored.get("site_name", config.SITE_NAME),
        },
        # 通道配置状态（只读，密钥不回显）
        "channels": {
            "sms_provider": config.SMS_PROVIDER,
            "sms_ready": config.sms_ready(),
            "pay_provider": config.PAY_PROVIDER,
            "pay_ready": config.pay_ready(),
        },
        "editable_keys": sorted(settings_store.EDITABLE_KEYS),
    }


class SettingsIn(BaseModel):
    run_mode: str | None = None
    free_trial_minutes: int | None = None
    default_device_limit: int | None = None
    site_notice: str | None = None
    site_name: str | None = None


@router.post("/api/settings")
def save_settings(data: SettingsIn, request: Request, db: Session = Depends(get_db)):
    current_admin(request, db)
    if data.run_mode is not None:
        if data.run_mode not in ("test", "production"):
            return {"ok": False, "msg": "运行模式只能是 test 或 production"}
        settings_store.set_setting(db, "run_mode", data.run_mode)
    if data.free_trial_minutes is not None:
        if data.free_trial_minutes < 0 or data.free_trial_minutes > 100000:
            return {"ok": False, "msg": "免费体验分钟数不合法"}
        settings_store.set_setting(db, "free_trial_minutes", str(data.free_trial_minutes))
    if data.default_device_limit is not None:
        if data.default_device_limit < 0 or data.default_device_limit > 100:
            return {"ok": False, "msg": "设备数限制不合法"}
        settings_store.set_setting(db, "default_device_limit", str(data.default_device_limit))
    if data.site_notice is not None:
        settings_store.set_setting(db, "site_notice", data.site_notice[:500])
    if data.site_name is not None:
        settings_store.set_setting(db, "site_name", data.site_name[:64])
    return {"ok": True}


@router.post("/api/admin/password")
async def change_password(request: Request, db: Session = Depends(get_db)):
    """管理员修改自己的密码。"""
    adm = current_admin(request, db)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": "参数错误"}
    old, new = (data or {}).get("old_password", ""), (data or {}).get("new_password", "")
    if not verify_password(old, adm.password_hash):
        return {"ok": False, "msg": "原密码错误"}
    if len(new) < 8:
        return {"ok": False, "msg": "新密码至少 8 位"}
    adm.password_hash = hash_password(new)
    db.commit()
    return {"ok": True}


@router.get("/api/me")
def me(request: Request, db: Session = Depends(get_db)):
    adm = current_admin(request, db)
    return {"ok": True, "username": adm.username, "role": adm.role}


# ------------------------------------------------------------------ 管理员手动开通订阅
class SubscribeIn(BaseModel):
    plan_id: int


@router.post("/api/users/{uid}/subscribe")
def user_subscribe(uid: int, data: SubscribeIn, request: Request, db: Session = Depends(get_db)):
    """管理员手动给用户开通套餐/会员。"""
    current_admin(request, db)
    
    u = db.query(User).filter(User.id == uid).first()
    if not u:
        return {"ok": False, "msg": "用户不存在"}
    
    p = db.query(Plan).filter(Plan.id == data.plan_id, Plan.status == 1).first()
    if not p:
        return {"ok": False, "msg": "套餐不存在或已下架"}
    
    # 调用现有的激活订阅函数
    sub = svc.activate_subscription(db, u, p, order_id=None)
    
    return {"ok": True, "msg": f"已给用户 {u.display_name} 开通套餐「{p.name}」", 
            "expire_at": sub.expire_at.strftime("%Y-%m-%d %H:%M") if sub.expire_at else "长期有效"}
