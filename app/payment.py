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
from typing import Dict, Optional

import requests

from .config import (
    ALIPAY_APPID, ALIPAY_NOTIFY_URL, ALIPAY_PRIVATE_KEY, ALIPAY_PUBLIC_KEY,
    WECHAT_API_V3, WECHAT_APPID, WECHAT_MCHID, WECHAT_NOTIFY_URL,
    WECHAT_PRIVATE_KEY, WECHAT_SERIAL_NO,
)

logger = logging.getLogger("wifidog.pay")


def create_charge(order_no: str, amount_fen: int, subject: str, pay_type: str) -> Dict:
    """发起支付。pay_type 必须由服务端决定后传入。"""
    if pay_type == "mock":
        return {"code": 0, "pay_url": f"/portal/pay/mock?order_no={order_no}", "qr": False}
    if pay_type == "alipay":
        return _alipay_wap(order_no, amount_fen, subject)
    if pay_type == "wechat":
        return _wechat_native(order_no, amount_fen, subject)
    return {"code": 1, "message": f"unsupported pay_type {pay_type}"}


# ------------------------------------------------------------------ 支付宝
def _alipay_sign(unsigned: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(ALIPAY_PRIVATE_KEY.encode(), password=None)
    sig = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _alipay_wap(order_no: str, amount_fen: int, subject: str) -> Dict:
    if not (ALIPAY_APPID and ALIPAY_PRIVATE_KEY):
        return {"code": 1, "message": "支付宝参数未配置"}
    biz = {
        "out_trade_no": order_no,
        "total_amount": f"{amount_fen / 100:.2f}",
        "subject": subject,
        "product_code": "QUICK_WAP_WAY",
    }
    params = {
        "app_id": ALIPAY_APPID,
        "method": "alipay.trade.wap.pay",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": ALIPAY_NOTIFY_URL,
        "biz_content": json.dumps(biz, separators=(",", ":")),
    }
    unsigned = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["sign"] = _alipay_sign(unsigned)
    from urllib.parse import urlencode

    return {"code": 0, "pay_url": "https://openapi.alipay.com/gateway.do?" + urlencode(params)}


def verify_alipay_notify(data: Dict) -> bool:
    """支付宝异步通知验签（RSA2 / RSA）。
    注意：cryptography 的 verify() 需要传入【原文】，由库内部完成摘要（旧版传
    digest 是错误实现，永远验签失败）。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    data = dict(data or {})
    sign = data.pop("sign", None)
    sign_type = data.pop("sign_type", "RSA2")
    if not sign or not ALIPAY_PUBLIC_KEY:
        return False
    unsigned = "&".join(f"{k}={v}" for k, v in sorted(data.items()) if v not in (None, ""))
    try:
        pub = serialization.load_pem_public_key(ALIPAY_PUBLIC_KEY.encode())
        pub.verify(
            base64.b64decode(sign),
            unsigned.encode(),
            padding.PKCS1v15(),
            hashes.SHA256() if sign_type == "RSA2" else hashes.SHA1(),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("alipay verify failed: %s", e)
        return False


# ------------------------------------------------------------------ 微信支付 V3
def _wechat_sign(method: str, url_path: str, timestamp: str, nonce: str, body: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"
    key = serialization.load_pem_private_key(WECHAT_PRIVATE_KEY.encode(), password=None)
    sig = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _wechat_native(order_no: str, amount_fen: int, subject: str) -> Dict:
    if not (WECHAT_MCHID and WECHAT_PRIVATE_KEY and WECHAT_SERIAL_NO):
        return {"code": 1, "message": "微信支付参数未配置"}
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
            },
            timeout=15,
        )
        data = r.json()
        if "code_url" in data:
            return {"code": 0, "pay_url": data["code_url"], "qr": True}
        return {"code": 1, "message": data.get("message", "wechat error")}
    except Exception as e:  # noqa: BLE001
        logger.error("wechat pay error: %s", e)
        return {"code": 1, "message": str(e)}


def decrypt_wechat_resource(resource: Dict) -> Optional[Dict]:
    """解密微信回调 resource（AES-256-GCM，密钥为 APIv3 key）。

    AES-GCM 是认证加密：解密成功即证明密文由持有 APIv3 key 的一方生成，
    因此"解密成功 + 金额核对"即可作为回调可信依据（不依赖平台证书验签）。"""
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
