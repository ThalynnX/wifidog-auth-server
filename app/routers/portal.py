"""用户端：短信登录、套餐购买、在线状态查询、支付回调。

安全要点（本次加固）：
- 验证码校验失败 5 次即作废；手机号与 IP 双维度限速（P0-3）。
- pay_type 一律由服务端按运行模式决定，客户端传入值被忽略（P0-2）。
- mock 支付确认端点仅在测试模式可用（P0-2）。
- 真实支付通过带验签/解密的回调端点确认，并核对订单金额。
- /me 仅支持 token 查询，不再支持纯手机号（P2-9）。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import payment, ratelimit, realtime, services_auth as svc, sms, settings_store
from ..config import SMS_CODE_EXPIRE, SMS_DAILY_LIMIT, SMS_IP_HOURLY_LIMIT
from ..database import get_db
from ..models import Order, Plan, SmsCode, Session as Sess, User
from ..routers.wifidog import build_gateway_redirect, issue_token, push_client_allow
from ..security import verify_password
from ..templates_env import templates

from app import device_auth  # 设备(MAC)绑定

router = APIRouter(prefix="/portal", tags=["portal"])

CODE_MAX_ATTEMPTS = 5      # 单个验证码最大失败次数，超限作废
LOGIN_IP_LIMIT = (30, 600)       # 每 IP 10 分钟最多 30 次登录尝试
SEND_IP_LIMIT = (SMS_IP_HOURLY_LIMIT, 3600)  # 每 IP 每小时发送上限
BUY_IP_LIMIT = (10, 3600)        # 每 IP 每小时最多创建 10 笔订单


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    return xf.split(",")[0].strip() if xf else (request.client.host if request.client else "")


class SendCodeIn(BaseModel):
    phone: str


class LoginIn(BaseModel):
    phone: str
    code: str
    gw_id: str = ""
    gw_address: str = ""
    gw_port: str = ""
    mac: str = ""
    ip: str = ""
    url: str = ""


class PwdLoginIn(BaseModel):
    username: str
    password: str
    gw_id: str = ""
    gw_address: str = ""
    gw_port: str = ""
    mac: str = ""
    url: str = ""


class BuyIn(BaseModel):
    plan_id: int
    phone: str
    token: str = ""


def _bad_phone(phone: str) -> bool:
    return not (phone and len(phone) == 11 and phone.isdigit() and phone.startswith("1"))


# ------------------------------------------------------------------ 短信验证码
@router.post("/api/send_code")
def send_code(data: SendCodeIn, request: Request, db: Session = Depends(get_db)):
    phone = (data.phone or "").strip()
    if _bad_phone(phone):
        return JSONResponse({"ok": False, "msg": "请输入正确的手机号"})

    ip = _ip(request)
    if not ratelimit.check(f"sc:ip:{ip}", SEND_IP_LIMIT[0], SEND_IP_LIMIT[1]):
        return JSONResponse({"ok": False, "msg": "请求过于频繁，请稍后再试"})

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cnt = db.query(func.count(SmsCode.id)).filter(
        SmsCode.phone == phone, SmsCode.created_at >= today
    ).scalar() or 0
    if cnt >= SMS_DAILY_LIMIT:
        return JSONResponse({"ok": False, "msg": "今日验证码发送已达上限"})

    code = sms.generate_code()
    db.add(SmsCode(phone=phone, code=code, purpose="login", ip=ip,
                   expire_at=datetime.now() + timedelta(seconds=SMS_CODE_EXPIRE)))
    db.commit()

    provider = settings_store.sms_provider(db)
    ok = sms.send_code(phone, code, provider)
    if not ok:
        return JSONResponse({"ok": False, "msg": "验证码发送失败，请稍后重试"})

    # 测试模式回显验证码便于联调；正式模式（真实短信通道）一律为空
    return JSONResponse({
        "ok": True,
        "msg": "验证码已发送",
        "dev_code": code if provider == "mock" else "",
    })


def _verify_code(db, phone: str, code: str) -> bool:
    """校验验证码：失败 5 次作废（P0-3 防爆破）。"""
    row = (
        db.query(SmsCode)
        .filter(SmsCode.phone == phone, SmsCode.used == False,  # noqa: E712
                SmsCode.expire_at > datetime.now())
        .order_by(SmsCode.id.desc())
        .first()
    )
    if not row:
        return False
    if (row.attempts or 0) >= CODE_MAX_ATTEMPTS:
        return False
    if row.code != code:
        row.attempts = (row.attempts or 0) + 1
        if row.attempts >= CODE_MAX_ATTEMPTS:
            row.used = True  # 作废
        db.commit()
        return False
    row.used = True
    db.commit()
    return True


# ------------------------------------------------------------------ 登录
@router.post("/api/login")
async def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    phone = (data.phone or "").strip()
    ip = _ip(request)
    if not ratelimit.check(f"login:ip:{ip}", LOGIN_IP_LIMIT[0], LOGIN_IP_LIMIT[1]):
        return JSONResponse({"ok": False, "msg": "尝试过于频繁，请10分钟后再试"})
    if not ratelimit.check(f"login:ph:{phone}", 10, 600):
        return JSONResponse({"ok": False, "msg": "该手机号尝试过于频繁，请稍后再试"})

    # MAC 黑名单优先级最高：命中一律拒绝（云模式下网关不走 /auth，必须在这里拦）
    if data.mac and svc.is_mac_blacklisted(db, data.mac):
        svc.write_log(db, "login", "fail", phone=phone, gw_id=data.gw_id,
                      ip=ip, mac=data.mac, reason="mac_blacklisted")
        return JSONResponse({"ok": False, "msg": "该设备已被禁止上网，请联系管理员"})

    if not _verify_code(db, phone, data.code):
        svc.write_log(db, "login", "fail", phone=phone, gw_id=data.gw_id,
                      ip=ip, reason="bad code")
        return JSONResponse({"ok": False, "msg": "验证码错误或已过期"})

    ratelimit.reset(f"login:ph:{phone}")
    user = svc.get_or_create_user(db, phone, ip)
    # 设备(MAC)绑定：认证过的设备会被记住，换设备需要重新认证；会员可自助更换
    dev_ok, dev_code, dev_msg = device_auth.check_device(db, user, data.mac)
    if not dev_ok:
        svc.write_log(db, "login", "fail", phone=phone, gw_id=data.gw_id,
                      ip=ip, mac=data.mac, reason="device_mismatch")
        return JSONResponse({
            "ok": False,
            "msg": dev_msg,
            "need_device": True,
            "change_token": device_auth.issue_change_token(user.id, phone, data.mac),
            "bound_mac": device_auth.mask_mac(device_auth.get_bound_mac(user)),
            "cur_mac": data.mac,
        })
    if dev_code in ("first_bind", "reauth_rebind"):
        svc.write_log(db, "device_bind", dev_code, phone=phone, gw_id=data.gw_id,
                      ip=ip, mac=data.mac, reason=dev_code)
    # MAC 白名单：免套餐直接放行（例如自家设备、合作商户设备）
    if data.mac and svc.is_mac_whitelisted(db, data.mac):
        token = issue_token(db, user.id, data.gw_id, data.mac)
        try:
            await push_client_allow(data.mac, data.ip, data.gw_id, token)
        except Exception:  # noqa: BLE001
            logger.exception("推送 allow 失败（白名单）")
        svc.write_log(db, "login", "ok", phone=phone, gw_id=data.gw_id,
                      ip=ip, mac=data.mac, reason="mac_whitelisted")
        return JSONResponse({"ok": True, "redirect": "", "token": token, "whitelisted": True})

    ok, reason, _sub = svc.check_access(db, user, exclude_mac=(data.mac or ""))
    if not ok:
        return JSONResponse({"ok": False, "msg": reason, "need_buy": True, "phone": phone})

    token = issue_token(db, user.id, data.gw_id, data.mac)
    try:
        await push_client_allow(data.mac, data.ip, data.gw_id, token)
    except Exception:  # noqa: BLE001
        logger.exception("推送 allow 失败")
    # 云模式放行已由 WebSocket 下发完成，不再跳网关 /wifidog/auth
    return JSONResponse({"ok": True, "redirect": "", "token": token})



class RebindIn(BaseModel):
    change_token: str
    mac: str = ""
    ip: str = ""
    gw_id: str = ""


@router.post("/api/device/rebind")
async def device_rebind(data: RebindIn, request: Request, db: Session = Depends(get_db)):
    """会员自助更换绑定设备（MAC）。"""
    info = device_auth.verify_change_token(data.change_token)
    if not info:
        return JSONResponse({"ok": False, "msg": "操作已超时，请重新获取验证码登录"})
    uid, phone, tok_mac = info
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        return JSONResponse({"ok": False, "msg": "账号不存在，请重新登录"})

    ok, msg = device_auth.rebind(db, user, data.mac or tok_mac)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg, "need_buy": "套餐" in msg})

    token = issue_token(db, user.id, data.gw_id, data.mac)
    ip = data.ip or _ip(request)
    try:
        await push_client_allow(data.mac, ip, data.gw_id, token)
    except Exception:  # noqa: BLE001
        logger.exception("推送 allow 失败（换设备）")
    svc.write_log(db, "device_rebind", "ok", user_id=user.id, phone=phone,
                  gw_id=data.gw_id, ip=ip, mac=data.mac, reason=msg)
    return JSONResponse({"ok": True, "msg": "已更换绑定设备，现在可以直接上网了", "token": token})



class AutoIn(BaseModel):
    mac: str
    ip: str = ""
    gw_id: str = ""


@router.post("/api/device/auto")
async def device_auto(data: AutoIn, request: Request, db: Session = Depends(get_db)):
    """已认证设备（MAC 未变）自动放行，免重复认证；MAC 变了不会命中。"""
    mac = device_auth.normalize_mac(data.mac)
    if not mac:
        return JSONResponse({"ok": False})
    user = db.query(User).filter(func.lower(User.mac) == mac).first()
    if not user:
        return JSONResponse({"ok": False})

    if svc.is_mac_blacklisted(db, mac):  # mac_blacklisted_auto
        return JSONResponse({"ok": False, "msg": "该设备已被禁止上网，请联系管理员"})
    ok, reason, _sub = svc.check_access(db, user, exclude_mac=mac)
    if not ok:
        return JSONResponse({"ok": False, "msg": reason})
    token = issue_token(db, user.id, data.gw_id, mac)
    ip = data.ip or _ip(request)
    try:
        await push_client_allow(mac, ip, data.gw_id, token)
    except Exception:  # noqa: BLE001
        logger.exception("推送 allow 失败（自动识别）")
    svc.write_log(db, "device_auto", "ok", user_id=user.id, phone=user.phone or "",
                  gw_id=data.gw_id, ip=ip, mac=mac, reason="known device")
    return JSONResponse({"ok": True, "msg": "已识别已认证设备，正在自动放行",
                         "phone": (user.phone or "")[:3] + "****" + (user.phone or "")[-4:],
                         "token": token})


@router.post("/api/password_login")
def password_login(data: PwdLoginIn, request: Request, db: Session = Depends(get_db)):
    ip = _ip(request)
    if not ratelimit.check(f"pwdlogin:ip:{ip}", 10, 600):
        return JSONResponse({"ok": False, "msg": "尝试过于频繁，请稍后再试"})

    user = db.query(User).filter(User.username == data.username).first()
    if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
        return JSONResponse({"ok": False, "msg": "账号或密码错误"})
    ok, reason, _ = svc.check_access(db, user, exclude_mac=(data.mac or ""))
    if not ok:
        return JSONResponse({"ok": False, "msg": reason, "need_buy": True})
    token = issue_token(db, user.id, data.gw_id, data.mac)
    return JSONResponse({
        "ok": True,
        "redirect": build_gateway_redirect(data.gw_address, data.gw_port, token, data.gw_id),
        "token": token,
    })


# ------------------------------------------------------------------ 套餐与购买
@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, phone: str = "", token: str = "", db: Session = Depends(get_db)):
    plans = db.query(Plan).filter(Plan.status == 1).order_by(Plan.sort.desc(), Plan.id.asc()).all()
    return templates.TemplateResponse(
        request=request,
        name="portal/plans.html",
        context={
            "request": request, "plans": plans, "phone": phone, "token": token,
            "notice": settings_store.get_site_notice(db),
        },
    )


@router.post("/api/buy")
def buy(data: BuyIn, request: Request, db: Session = Depends(get_db)):
    """创建订单并发起支付。pay_type 由服务端按运行模式决定（P0-2）。"""
    ip = _ip(request)
    if not ratelimit.check(f"buy:ip:{ip}", BUY_IP_LIMIT[0], BUY_IP_LIMIT[1]):
        return JSONResponse({"ok": False, "msg": "请求过于频繁，请稍后再试"})

    plan = db.query(Plan).filter(Plan.id == data.plan_id, Plan.status == 1).first()
    if not plan:
        return JSONResponse({"ok": False, "msg": "套餐不存在或已下架"})

    phone = (data.phone or "").strip()
    if _bad_phone(phone):
        return JSONResponse({"ok": False, "msg": "请输入正确的手机号"})

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = svc.get_or_create_user(db, phone, ip)
    if user.status != 1:
        return JSONResponse({"ok": False, "msg": "账号已被禁用"})

    order = svc.create_order(db, user, plan)

    # 关键：支付渠道由服务端决定，客户端无法指定 mock（P0-2）
    pay_type = settings_store.pay_provider(db)
    if pay_type == "mock" and not settings_store.is_test_mode(db):
        return JSONResponse({"ok": False, "msg": "支付渠道未配置，请联系管理员"})

    result = payment.create_charge(order.order_no, plan.price, plan.name, pay_type)
    if result.get("code") != 0:
        return JSONResponse({"ok": False, "msg": result.get("message", "支付发起失败")})
    return JSONResponse({
        "ok": True,
        "order_no": order.order_no,
        "pay_url": result.get("pay_url"),
        "qr": result.get("qr", False),
    })


@router.get("/pay/mock", response_class=HTMLResponse)
def pay_mock(request: Request, order_no: str = "", db: Session = Depends(get_db)):
    """模拟支付确认页。仅测试模式可访问（P0-2）。"""
    if not settings_store.is_test_mode(db):
        return HTMLResponse("正式环境不支持模拟支付", status_code=404)
    order = db.query(Order).filter(Order.order_no == order_no).first()
    if not order:
        return HTMLResponse("订单不存在", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="portal/pay_mock.html",
        context={"request": request, "order": order},
    )


@router.post("/api/pay/confirm")
async def pay_confirm(request: Request, db: Session = Depends(get_db)):
    """模拟支付确认。仅测试模式可用；正式模式该端点直接关闭（P0-2）。"""
    if not settings_store.is_test_mode(db):
        return JSONResponse({"ok": False, "msg": "正式环境不支持模拟支付"}, status_code=403)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "msg": "参数错误"})
    order_no = (data or {}).get("order_no", "")
    order = db.query(Order).filter(Order.order_no == order_no).first()
    if not order:
        return JSONResponse({"ok": False, "msg": "订单不存在"})
    if order.status == 2:
        return JSONResponse({"ok": False, "msg": "订单已超时关闭，请重新下单"})
    if order.status == 1:
        return JSONResponse({"ok": False, "msg": "订单已支付，无需重复确认"})
    svc.pay_order_success(db, order, pay_type="mock", trade_no=order_no)
    return JSONResponse({"ok": True, "msg": "支付成功，套餐已开通"})


# ------------------------------------------------------------------ 支付回调（真实渠道）
def _finish_paid_order(db, order, pay_type: str, trade_no: str) -> str:
    svc.pay_order_success(db, order, pay_type=pay_type, trade_no=trade_no)
    return "success"


@router.post("/api/pay/notify/alipay")
async def alipay_notify(request: Request, db: Session = Depends(get_db)):
    """支付宝异步通知：RSA2 验签 + 金额核对 + 幂等开通。"""
    form = await request.form()
    data = {k: v for k, v in form.items()}
    order_no = data.get("out_trade_no", "")
    order = db.query(Order).filter(Order.order_no == order_no).first()
    if not order:
        return HTMLResponse("fail")

    if not payment.verify_alipay_notify(data):
        svc.write_log(db, "pay", "fail", user_id=order.user_id, reason="alipay verify failed")
        return HTMLResponse("fail")

    # 金额核对
    try:
        paid = round(float(data.get("total_amount", "0")) * 100)
    except (TypeError, ValueError):
        return HTMLResponse("fail")
    if paid != order.amount:
        svc.write_log(db, "pay", "fail", user_id=order.user_id,
                      reason=f"alipay amount mismatch {paid} != {order.amount}")
        return HTMLResponse("fail")

    if data.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return HTMLResponse("success")  # 非成功状态不处理，但要应答成功停止重发

    return HTMLResponse(_finish_paid_order(db, order, "alipay", data.get("trade_no", "")))


@router.post("/api/pay/notify/wechat")
async def wechat_notify(request: Request, db: Session = Depends(get_db)):
    """微信支付 V3 回调：AES-GCM 解密（认证加密，解密成功即证明来源可信）+ 金额核对。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"code": "FAIL", "message": "bad request"}, status_code=400)

    resource = body.get("resource") or {}
    plain = payment.decrypt_wechat_resource(resource)
    if not plain:
        return JSONResponse({"code": "FAIL", "message": "verify failed"}, status_code=401)

    order_no = plain.get("out_trade_no", "")
    order = db.query(Order).filter(Order.order_no == order_no).first()
    if not order:
        return JSONResponse({"code": "FAIL", "message": "order not found"}, status_code=404)

    amount = (plain.get("amount") or {}).get("total")
    if amount != order.amount:
        svc.write_log(db, "pay", "fail", user_id=order.user_id,
                      reason=f"wechat amount mismatch {amount} != {order.amount}")
        return JSONResponse({"code": "FAIL", "message": "amount mismatch"}, status_code=400)

    if plain.get("trade_state") != "SUCCESS":
        return JSONResponse({"code": "SUCCESS", "message": "ignored"})

    _finish_paid_order(db, order, "wechat", plain.get("transaction_id", ""))
    return JSONResponse({"code": "SUCCESS", "message": "OK"})


# ------------------------------------------------------------------ 实时状态
@router.get("/api/live")
def api_live(token: str = "", mac: str = "", db: Session = Depends(get_db)):
    """实时上网状态：时长、上下行速率、流量、套餐（前端每 3 秒轮询）。"""
    sess = None
    if token:
        sess = db.query(Sess).filter(Sess.token == token).order_by(Sess.id.desc()).first()
    if sess is None and mac:
        sess = db.query(Sess).filter(Sess.mac == mac.lower()).order_by(Sess.id.desc()).first()
    if sess is None:
        return JSONResponse({"ok": False, "msg": "会话不存在或已失效，请重新认证"})

    user = db.query(User).filter(User.id == sess.user_id).first()
    sub = svc.get_active_subscription(db, user.id) if user else None
    now = datetime.now()
    seconds = max(0, int((now - sess.start_at).total_seconds())) if sess.start_at else 0
    st = realtime.get(sess.mac)
    # 账号维度：该用户名下当前在线的设备数（不是全网终端数）
    device_count = svc.online_device_count(db, user.id) if user else 0
    if device_count == 0 and st:
        device_count = 1
    online = device_count

    return JSONResponse({
        "ok": True,
        "phone": (user.phone or "") if user else "",
        "mac": sess.mac,
        "ip": (st or {}).get("ip") or sess.ip,
        "seconds": seconds,
        "down_mbps": (st or {}).get("down_mbps", 0),
        "up_mbps": (st or {}).get("up_mbps", 0),
        "down_kbps": round((st or {}).get("down_mbps", 0) * 1000, 1),
        "up_kbps": round((st or {}).get("up_mbps", 0) * 1000, 1),
        "rx_bytes": (st or {}).get("rx", 0),
        "tx_bytes": (st or {}).get("tx", 0),
        "rssi": (st or {}).get("rssi", ""),
        "live": bool(st),
        "plan_name": sub.plan_name if sub else "",
        "expire_at": sub.expire_at.strftime("%Y-%m-%d") if (sub and sub.expire_at) else "",
        "days_left": max(0, (sub.expire_at - now).days) if (sub and sub.expire_at) else -1,
        "online": online,
        "device_count": device_count,
        "status": sess.status,
    })


# ------------------------------------------------------------------ 在线状态
@router.get("/me")
def me(request: Request, token: str = "", db: Session = Depends(get_db)):
    """查询当前账号的套餐余量。仅支持 token（P2-9：不再支持纯手机号查询）。"""
    if not token:
        return JSONResponse({"ok": False, "msg": "未登录"})
    sess = db.query(Sess).filter(Sess.token == token).first()
    if not sess or not sess.user_id:
        return JSONResponse({"ok": False, "msg": "未登录"})
    user = db.query(User).filter(User.id == sess.user_id).first()
    if not user:
        return JSONResponse({"ok": False, "msg": "未登录"})

    sub = svc.get_active_subscription(db, user.id)
    traffic_left_mb = None
    if sub and sub.traffic_total_mb > 0:
        traffic_left_mb = max(0, sub.traffic_total_mb - (sub.traffic_used_mb or 0))
    return JSONResponse({
        "ok": True,
        "phone": user.phone,
        "nickname": user.display_name,
        "plan": sub.plan_name if sub else "",
        "expire_at": sub.expire_at.strftime("%Y-%m-%d %H:%M") if sub and sub.expire_at else "",
        "traffic_left_mb": round(traffic_left_mb, 1) if traffic_left_mb is not None else None,
        "online_devices": svc.online_device_count(db, user.id),
    })


@router.get("/success", response_class=HTMLResponse)
def success(request: Request, gw_id: str = "", token: str = "", db: Session = Depends(get_db)):
    """认证成功页：按 token 渲染真实套餐/到期时间/在线设备数。"""
    sess = db.query(Sess).filter(Sess.token == token).first() if token else None
    user = db.query(User).filter(User.id == sess.user_id).first() if sess else None
    sub = svc.get_active_subscription(db, user.id) if user else None
    online = db.query(func.count(Sess.id)).filter(Sess.status == 1).scalar() or 0
    return templates.TemplateResponse(
        request=request,
        name="portal/success.html",
        context={
            "request": request, "gw_id": gw_id, "token": token,
            "session": sess, "user": user, "sub": sub, "online": int(online),
        },
    )
