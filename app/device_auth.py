"""设备(MAC)绑定：记住已认证设备；换设备需重新认证；会员可自助更换。"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

from app.config import SECRET_KEY

CHANGE_TOKEN_TTL = 900        # 换绑令牌有效期 15 分钟
MAX_CHANGES_PER_30D = 3       # 每 30 天最多自助更换 3 次


def normalize_mac(mac: str) -> str:
    return (mac or "").strip().lower()


def mask_mac(mac: str) -> str:
    m = normalize_mac(mac)
    if len(m) < 8:
        return m
    return f"{m[:5]}**:**{m[-5:]}"


def get_bound_mac(user) -> str:
    return normalize_mac(getattr(user, "mac", "") or "")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload_b64: str) -> str:
    key = (SECRET_KEY or "").encode()
    return hmac.new(key, payload_b64.encode(), hashlib.sha256).hexdigest()


def issue_change_token(user_id: int, phone: str, mac: str) -> str:
    payload = {
        "u": int(user_id),
        "p": phone or "",
        "m": normalize_mac(mac),
        "t": int(time.time()),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def verify_change_token(token: str):
    """校验换绑令牌，返回 (user_id, phone, mac)；失败返回 None。"""
    try:
        body, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        data = json.loads(_unb64(body))
        if int(time.time()) - int(data.get("t", 0)) > CHANGE_TOKEN_TTL:
            return None
        return int(data["u"]), data.get("p", ""), normalize_mac(data.get("m", ""))
    except Exception:
        return None


def bind(db, user, mac: str) -> None:
    user.mac = normalize_mac(mac)
    user.mac_updated_at = datetime.now()
    db.commit()


def check_device(db, user, mac: str):
    """登录时的设备校验。返回 (是否允许, 结果码, 提示语)。

    结果码：
      no_mac          本次请求没带 MAC（无法绑定）
      first_bind      首次认证，已记住该设备
      ok              与已绑定设备一致
      reauth_rebind   非会员换了设备：本次短信认证视为重新认证，已改绑
      device_mismatch 会员换了设备：需要自助更换设备
    """
    from app.services_auth import get_active_subscription

    m = normalize_mac(mac)
    if not m:
        return True, "no_mac", ""
    bound = get_bound_mac(user)
    if not bound:
        bind(db, user, m)
        return True, "first_bind", ""
    if bound == m:
        return True, "ok", ""

    sub = get_active_subscription(db, user.id)
    if not sub:
        bind(db, user, m)
        return True, "reauth_rebind", ""

    return (
        False,
        "device_mismatch",
        f"该手机号已绑定其他设备（{mask_mac(bound)}）。会员可在本页点『更换为当前设备』自助改绑，"
        f"或使用原设备连接。",
    )


def rebind(db, user, mac: str):
    """会员自助更换绑定设备，返回 (是否成功, 提示语)。"""
    from app.services_auth import get_active_subscription

    sub = get_active_subscription(db, user.id)
    if not sub:
        return False, "没有可用套餐，请先购买后再更换设备"

    m = normalize_mac(mac)
    if not m:
        return False, "未获取到当前设备的 MAC，请断开后重新连接 WiFi 再试"

    if get_bound_mac(user) == m:
        return True, "当前设备已经是您的绑定设备"

    last = getattr(user, "mac_updated_at", None)
    cnt = int(getattr(user, "mac_change_count", 0) or 0)
    if last and (datetime.now() - last) < timedelta(days=30):
        if cnt >= MAX_CHANGES_PER_30D:
            return False, "30 天内更换设备次数已达上限，请联系客服处理"
        user.mac_change_count = cnt + 1
    else:
        user.mac_change_count = 1

    bind(db, user, m)
    return True, "已更换绑定设备，现在可以直接上网了"
