"""支付宝授权登录、授权回调与应用网关。

路由：
  GET  /portal/alipay/oauth/start
  GET  /portal/alipay/oauth/callback
  POST /portal/api/alipay/gateway

支付异步通知仍保留在 /portal/api/pay/notify/alipay，避免改动支付宝后台已填地址。
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import device_auth
from .. import alipay, netutil, services_auth as svc
from ..config import ALIPAY_APPID, ALIPAY_SELLER_ID, COOKIE_SECURE
from ..database import get_db
from ..models import AlipayOAuthState, Order, PaymentEvent, User, Session as Sess
from .wifidog import issue_token, push_client_allow

logger = logging.getLogger("wifidog.alipay")
router = APIRouter(prefix="/portal", tags=["alipay"])


TOKEN_COOKIE = "wd_token"
DEVICE_COOKIE = "wd_device"


def _ip(request: Request) -> str:
    return netutil.client_ip(request)


def _valid_phone(phone: str) -> bool:
    return bool(phone and len(phone) == 11 and phone.isdigit() and phone.startswith("1"))


def _html(title: str, message: str, status_code: int = 400) -> HTMLResponse:
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b1220;color:#e6edf7;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}}
.card{{max-width:420px;background:#111c2e;border:1px solid #20324d;border-radius:16px;padding:24px}}
h1{{font-size:20px;margin:0 0 10px}}p{{color:#9fb0c8;line-height:1.7}}a{{color:#28d3c8}}
</style><div class="card"><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p><a href="/wifidog/login">返回登录页</a></p></div></html>"""
    return HTMLResponse(body, status_code=status_code)


@router.get("/alipay/oauth/start")
def oauth_start(
    request: Request,
    gw_id: str = "",
    mac: str = "",
    ip: str = "",
    phone: str = "",
    url: str = "",
    db: Session = Depends(get_db),
):
    """跳转支付宝授权页；state 落库一次性使用，10 分钟内有效。"""
    if not alipay.oauth_ready():
        return _html("支付宝登录未配置", "管理员尚未配置支付宝 APPID、应用私钥、平台公钥或授权回调地址。", 503)
    if gw_id and not svc.gateway_active(db, gw_id):
        return _html("网关不可用", "网关未登记或已停用，请联系管理员。", 403)
    if mac and svc.is_mac_blacklisted(db, mac):
        return _html("设备已禁用", "该设备已被管理员禁止上网。", 403)
    # 顺手清理过期 state，避免表无限增长
    db.query(AlipayOAuthState).filter(AlipayOAuthState.expire_at < datetime.now()).delete()
    db.commit()
    state = secrets.token_urlsafe(32)
    payload = json.dumps({
        "gw_id": gw_id, "mac": (mac or "").lower(), "ip": ip or _ip(request),
        "phone": (phone or "").strip(), "url": url,
    }, ensure_ascii=False)
    db.add(AlipayOAuthState(
        state=state, payload=payload, used=False,
        expire_at=datetime.now() + timedelta(minutes=10),
    ))
    db.commit()
    redirect_uri = alipay.config.ALIPAY_OAUTH_REDIRECT_URL or (
        str(request.base_url).rstrip("/") + "/portal/alipay/oauth/callback"
    )
    return RedirectResponse(alipay.oauth_authorize_url(state, redirect_uri), status_code=302)


@router.get("/alipay/oauth/callback")
async def oauth_callback(
    request: Request,
    state: str = "",
    auth_code: str = "",
    db: Session = Depends(get_db),
):
    """支付宝授权回调：换 token -> 拉用户信息 -> 绑定/创建用户 -> 签发上网 token。"""
    state = (state or "").strip()
    auth_code = (auth_code or "").strip()
    if not state or not auth_code:
        return _html("授权参数缺失", "支付宝没有返回 state 或 auth_code，请重新发起登录。")
    row = db.query(AlipayOAuthState).filter(AlipayOAuthState.state == state).first()
    if not row or row.used or row.expire_at < datetime.now():
        return _html("授权已失效", "本次授权已使用或超过 10 分钟，请返回登录页重新发起。")
    row.used = True
    db.commit()
    try:
        payload = json.loads(row.payload or "{}")
    except ValueError:
        payload = {}
    gw_id = str(payload.get("gw_id") or "")
    mac = str(payload.get("mac") or "").lower()
    ip = str(payload.get("ip") or _ip(request))
    input_phone = str(payload.get("phone") or "").strip()

    try:
        token_resp = alipay.oauth_token(auth_code)
    except alipay.AlipayError as exc:
        logger.warning("alipay oauth token failed: %s", exc)
        return _html("支付宝授权失败", f"换取访问令牌失败：{exc}")
    alipay_uid = str(token_resp.get("user_id") or token_resp.get("alipay_user_id") or "")
    access_token = str(token_resp.get("access_token") or "")
    if not alipay_uid or not access_token:
        return _html("支付宝授权失败", "支付宝未返回 user_id 或 access_token。")

    info: dict = {}
    try:
        info = alipay.user_info(access_token)
    except alipay.AlipayError as exc:
        # 未开通“获取会员信息”时仍可凭 user_id 登录，头像昵称降级为默认值
        logger.warning("alipay user info failed: %s", exc)

    nickname = str(info.get("nick_name") or info.get("nickname") or "支付宝用户")[:64]
    avatar = str(info.get("avatar") or "")[:255]

    user = db.query(User).filter(User.alipay_user_id == alipay_uid).first()
    if user is None:
        if input_phone and _valid_phone(input_phone):
            user = db.query(User).filter(User.phone == input_phone).first()
            if user and user.alipay_user_id and user.alipay_user_id != alipay_uid:
                return _html("手机号已绑定其他支付宝", "该手机号已绑定其他支付宝账号，请使用原支付宝登录。")
        if user is None:
            user = User(
                phone=input_phone if _valid_phone(input_phone) else None,
                nickname=nickname, avatar=avatar,
                status=1, balance=0, alipay_user_id=alipay_uid,
                alipay_nickname=nickname, alipay_avatar=avatar,
                alipay_bound_at=datetime.now(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
    else:
        if input_phone and _valid_phone(input_phone):
            if user.phone and user.phone != input_phone:
                return _html("手机号不一致", "当前支付宝账号已绑定其他手机号，请使用原手机号。")
            if not user.phone:
                other = db.query(User).filter(User.phone == input_phone, User.id != user.id).first()
                if other:
                    return _html("手机号已被占用", "该手机号已被其他账号绑定。")
                user.phone = input_phone
        user.alipay_nickname = nickname
        user.alipay_avatar = avatar
        user.alipay_bound_at = user.alipay_bound_at or datetime.now()
        db.commit()

    if user.status != 1:
        return _html("账号已禁用", "该账号已被管理员禁用，请联系客服。")
    if gw_id and not svc.gateway_active(db, gw_id):
        return _html("网关不可用", "网关未登记或已停用，请联系管理员。", 403)
    if mac and svc.is_mac_blacklisted(db, mac):
        svc.write_log(db, "login", "fail", user_id=user.id, phone=user.phone or "",
                      gw_id=gw_id, ip=ip, mac=mac, reason="mac_blacklisted")
        return _html("设备已禁用", "该设备已被管理员禁止上网。", 403)

    if mac:
        dev_ok, dev_code, dev_msg = device_auth.check_device(db, user, mac)
        if not dev_ok:
            svc.write_log(db, "login", "fail", user_id=user.id, phone=user.phone or "",
                          gw_id=gw_id, ip=ip, mac=mac, reason="alipay_device_mismatch")
            return _html("设备不匹配", dev_msg + " 请返回登录页使用短信登录并更换设备。")

    user.last_login_at = datetime.now()
    user.last_login_ip = ip
    db.commit()

    ok, reason, _sub = svc.check_access(db, user, exclude_mac=mac)
    token = issue_token(db, user.id, gw_id, mac)
    sess = db.query(Sess).filter(Sess.token == token).first()
    if ok and gw_id and mac:
        sent = 0
        try:
            sent = await push_client_allow(
                mac, ip, gw_id, token,
                down_kbps=(_sub.speed_limit_down if _sub else 0),
                up_kbps=(_sub.speed_limit_up if _sub else 0),
            )
        except Exception:  # noqa: BLE001
            logger.exception("支付宝登录推送 allow 失败")
        if sess and sent:
            sess.status = 1
            sess.stop_at = None
            sess.last_update_at = datetime.now()
            db.commit()
            svc.mark_authenticated(db, sess, "login", user_id=user.id, phone=user.phone or "",
                                  gw_id=gw_id, mac=mac, ip=ip, reason="alipay_oauth")
        if sent:
            resp = RedirectResponse(f"/portal/success?gw_id={gw_id}", status_code=302)
        else:
            resp = RedirectResponse(f"/portal/success?gw_id={gw_id}&retry=1", status_code=302)
        resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE, max_age=86400)
        if mac:
            resp.set_cookie(
                DEVICE_COOKIE, device_auth.issue_device_cookie(user.id, mac),
                httponly=True, samesite="lax", secure=COOKIE_SECURE,
                max_age=device_auth.DEVICE_COOKIE_TTL,
            )
        return resp

    phone_q = user.phone or ""
    # 上网 token 不放在 URL（会进入浏览器历史/代理日志），改由 HttpOnly Cookie 携带。
    resp = RedirectResponse(
        f"/portal/plans?phone={phone_q}", status_code=302
    )
    resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax",
                    secure=COOKIE_SECURE, max_age=86400)
    return resp


@router.post("/api/alipay/gateway")
async def app_gateway(request: Request, db: Session = Depends(get_db)):
    """支付宝应用网关：验签、留痕、幂等应答。

    应用网关消息不直接改订单状态，避免把非支付类消息误当支付成功；
    支付结果仍以 /portal/api/pay/notify/alipay 为准。
    """
    form = await request.form()
    data = {k: str(v) for k, v in form.items()}
    if not alipay.verify_params(data):
        return PlainTextResponse("fail", status_code=400)
    if ALIPAY_APPID and data.get("app_id") and data.get("app_id") != ALIPAY_APPID:
        return PlainTextResponse("fail", status_code=400)
    if ALIPAY_SELLER_ID and data.get("seller_id") and data.get("seller_id") != ALIPAY_SELLER_ID:
        return PlainTextResponse("fail", status_code=400)

    event_key = "alipay:gateway:" + (
        data.get("notify_id") or data.get("msg_id")
        or hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    )
    if db.query(PaymentEvent).filter(PaymentEvent.event_key == event_key[:160]).first():
        return PlainTextResponse("success")

    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    biz = {}
    if data.get("biz_content"):
        try:
            biz = json.loads(data["biz_content"])
        except ValueError:
            biz = {}
    order_no = str(biz.get("out_trade_no") or data.get("out_trade_no") or "")[:64]
    db.add(PaymentEvent(
        channel="alipay", event_type="gateway", event_key=event_key[:160],
        order_no=order_no, trade_no=str(data.get("trade_no") or "")[:128],
        payload_hash=hashlib.sha256(text.encode()).hexdigest(),
        payload=text[:4000], status="received", note=str(data.get("msg_method") or "")[:255],
    ))
    db.commit()
    return PlainTextResponse("success")
