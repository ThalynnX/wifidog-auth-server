"""支付：mock / alipay(RSA2) / wechat(V3)。

安全规则（P0-2 修复）：
- pay_type 一律由服务端根据运行模式决定（settings_store.pay_provider），
  客户端传入的 pay_type 一律忽略，杜绝 mock 旁路。
- 支付结果只能通过带验签/解密的回调端点确认：
  POST /portal/api/pay/notify/alipay   （RSA2 验签 + 金额核对）
  POST /portal/api/pay/notify/wechat   （APIv3 AES-GCM 解密 + 金额核对）
- mock 确认端点仅在测试模式可用。
"""
import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Dict, Optional

import requests

from .config import (
    WECHAT_API_V3, WECHAT_APPID, WECHAT_MCHID, WECHAT_NOTIFY_URL,
    WECHAT_PRIVATE_KEY, WECHAT_PUBLIC_KEY, WECHAT_PUBLIC_KEY_ID, WECHAT_SERIAL_NO,
    WECHAT_PLATFORM_CERT, WECHAT_PLATFORM_SERIAL,
)
from . import alipay

logger = logging.getLogger("wifidog.pay")


def create_charge(order_no: str, amount_fen: int, subject: str, pay_type: str, return_url: str = "") -> Dict:
    """发起支付。pay_type 必须由服务端决定后传入。"""
    if pay_type == "mock":
        return {"code": 0, "pay_url": f"/portal/pay/mock?order_no={order_no}", "qr": False}
    if pay_type == "alipay":
        try:
            if not alipay.pay_ready():
                return {"code": 1, "message": "支付宝参数未配置（APPID/私钥/平台公钥/回调地址）"}
            return {"code": 0, "pay_url": alipay.create_wap_pay(order_no, amount_fen, subject, return_url=return_url or None), "qr": False}
        except alipay.AlipayError as exc:
            logger.error("alipay create charge failed: %s", exc)
            return {"code": 1, "message": str(exc)}
    if pay_type == "wechat":
        return _wechat_native(order_no, amount_fen, subject)
    return {"code": 1, "message": f"unsupported pay_type {pay_type}"}


# ------------------------------------------------------------------ 支付宝
def verify_alipay_notify(data: Dict) -> bool:
    """支付宝异步通知验签（RSA2 公钥模式）。"""
    return alipay.verify_params(dict(data or {}))


# ------------------------------------------------------------------ 微信支付 V3
def _wechat_sign(method: str, url_path: str, timestamp: str, nonce: str, body: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"
    key = serialization.load_pem_private_key(WECHAT_PRIVATE_KEY.encode(), password=None)
    sig = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _headers_map(headers) -> Dict[str, str]:
    """把 requests/Starlette 的 headers 统一成小写字典。"""
    if headers is None:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        items = dict(headers or {}).items()
    return {str(k).lower(): str(v) for k, v in items}


def verify_wechat_signature(
    headers,
    body: bytes | str,
    *,
    public_key: Optional[str] = None,
    public_key_id: Optional[str] = None,
    platform_cert: Optional[str] = None,
    platform_serial: Optional[str] = None,
    now: Optional[int] = None,
    max_skew: int = 300,
) -> bool:
    """校验 V3 应答/回调签名，兼容公钥与平台证书双模式。

    签名串：timestamp\\nnonce\\nbody\\n。
    - `Wechatpay-Serial` 为公钥 ID 时用微信支付公钥验签；
    - 为平台证书序列号时用平台证书验签；
    - 灰度期两种都可能出现，按请求头精确选择，不能只用 AES-GCM 解密判断来源。
    """
    hdr = _headers_map(headers)
    timestamp = hdr.get("wechatpay-timestamp", "")
    nonce = hdr.get("wechatpay-nonce", "")
    signature = hdr.get("wechatpay-signature", "")
    serial = hdr.get("wechatpay-serial", "").strip()
    sig_type = hdr.get("wechatpay-signature-type", "")
    if sig_type and sig_type != "WECHATPAY2-SHA256-RSA2048":
        raise ValueError("wechat signature type mismatch")
    if not (timestamp and nonce and signature and serial):
        raise ValueError("wechat signature headers missing")
    if not timestamp.isdigit():
        raise ValueError("wechat timestamp invalid")
    current = int(time.time()) if now is None else int(now)
    if abs(current - int(timestamp)) > max_skew:
        raise ValueError("wechat timestamp expired")

    expected_id = (public_key_id if public_key_id is not None else WECHAT_PUBLIC_KEY_ID).strip()
    expected_platform = (
        platform_serial if platform_serial is not None else WECHAT_PLATFORM_SERIAL
    ).strip()

    selected_pem = ""
    if expected_id and serial == expected_id:
        selected_pem = public_key if public_key is not None else WECHAT_PUBLIC_KEY
        if not selected_pem:
            raise ValueError("wechat public key not configured")
    elif expected_platform and serial == expected_platform:
        selected_pem = platform_cert if platform_cert is not None else WECHAT_PLATFORM_CERT
        if not selected_pem:
            raise ValueError("wechat platform certificate not configured")
    elif not expected_id and serial.startswith("PUB_KEY_ID_"):
        selected_pem = public_key if public_key is not None else WECHAT_PUBLIC_KEY
        if not selected_pem:
            raise ValueError("wechat public key not configured")
    else:
        raise ValueError(f"wechat serial not trusted: {serial}")

    raw_body = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    if "BEGIN CERTIFICATE" in selected_pem:
        key = x509.load_pem_x509_certificate(selected_pem.encode()).public_key()
    else:
        key = serialization.load_pem_public_key(selected_pem.encode())
    message = f"{timestamp}\n{nonce}\n{raw_body}\n".encode("utf-8")
    try:
        key.verify(
            base64.b64decode(signature),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError("wechat signature invalid") from exc
    return True


def verify_wechat_notify(headers, raw_body: bytes) -> bool:
    """异步通知验签；必须在 AES-GCM 解密前执行。"""
    return verify_wechat_signature(headers, raw_body)


def _wechat_native(order_no: str, amount_fen: int, subject: str) -> Dict:
    if not (WECHAT_MCHID and WECHAT_PRIVATE_KEY and WECHAT_SERIAL_NO and WECHAT_PUBLIC_KEY):
        return {"code": 1, "message": "微信支付参数未配置（商户号/私钥/证书序列号/微信支付公钥）"}
    url_path = "/v3/pay/transactions/native"
    body = json.dumps(
        {
            "appid": WECHAT_APPID,
            "mchid": WECHAT_MCHID,
            "description": subject,
            "out_trade_no": order_no,
            "notify_url": WECHAT_NOTIFY_URL,
            "amount": {"total": amount_fen, "currency": "CNY"},
        },
        separators=(",", ":"),
    )
    import time as _t

    ts = str(int(_t.time()))
    nonce = secrets.token_hex(16)
    auth = (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{WECHAT_MCHID}",'
        f'nonce_str="{nonce}",signature="{_wechat_sign("POST", url_path, ts, nonce, body)}",'
        f'timestamp="{ts}",serial_no="{WECHAT_SERIAL_NO}"'
    )
    try:
        r = requests.post(
            "https://api.mch.weixin.qq.com" + url_path,
            data=body.encode(),
            headers={
                "Authorization": auth,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "wifidog-auth/1.0",
                **({"Wechatpay-Serial": WECHAT_PUBLIC_KEY_ID} if WECHAT_PUBLIC_KEY_ID else {}),
            },
            timeout=15,
        )
        try:
            verify_wechat_signature(r.headers, r.content)
        except ValueError as exc:
            logger.error("wechat response verify failed: %s", exc)
            return {"code": 1, "message": "微信支付应答验签失败，已拒绝处理"}
        data = r.json()
        if "code_url" in data:
            return {"code": 0, "pay_url": data["code_url"], "qr": True}
        return {"code": 1, "message": data.get("message", "wechat error")}
    except Exception as e:  # noqa: BLE001
        logger.error("wechat pay error: %s", e)
        return {"code": 1, "message": str(e)}


def decrypt_wechat_resource(resource: Dict) -> Optional[Dict]:
    """解密微信回调 resource（AES-256-GCM，密钥为 APIv3 key）。

    解密只是回调处理的第二步；第一步必须先用微信支付公钥验签 HTTP 头，
    否则持有 APIv3 key 的其他系统也可能构造出可解密的报文。
    """
    if not WECHAT_API_V3:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = WECHAT_API_V3.encode()
        nonce = resource["nonce"].encode()
        ad = (resource.get("associated_data") or "").encode()
        ciphertext = base64.b64decode(resource["ciphertext"])
        plain = AESGCM(key).decrypt(nonce, ciphertext, ad)
        return json.loads(plain)
    except Exception as e:  # noqa: BLE001
        logger.warning("wechat resource decrypt failed: %s", e)
        return None
