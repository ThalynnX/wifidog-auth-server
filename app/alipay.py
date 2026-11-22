"""支付宝开放平台客户端（RSA2 公钥模式）。

覆盖：
- 手机网站支付 alipay.trade.wap.pay
- 电脑网站支付 alipay.trade.page.pay（可选能力，A 范围不主动使用）
- 当面付预下单 alipay.trade.precreate（可选能力，A 范围不主动使用）
- 交易查询 / 关单 / 退款 / 退款查询
- 账单下载与 CSV 解析（对账）
- 授权登录 alipay.system.oauth.token + alipay.user.info.share
- 异步通知 / 应用网关验签

说明：本模块只支持“公钥模式”。若后台使用“公钥证书模式”，需要额外三张证书和
证书验签逻辑，当前不兼容。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import textwrap
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import config

logger = logging.getLogger(__name__)


class AlipayError(RuntimeError):
    """支付宝接口调用失败。"""


def _configured(value: str) -> bool:
    return bool(value) and not (value.startswith("<") and value.endswith(">"))


def amount_text(fen: int) -> str:
    """分 -> 支付宝金额字符串（两位小数）。"""
    return f"{Decimal(int(fen)) / Decimal(100):.2f}"


def fen_from_yuan(value: Any) -> int:
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AlipayError(f"金额格式不正确：{value}") from exc


def _pem(value: str, kind: str) -> str:
    value = (value or "").replace("\\n", "\n").strip()
    if "BEGIN" in value:
        return value
    body = "\n".join(textwrap.wrap(value, 64))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n"


def _private_key():
    if not _configured(config.ALIPAY_PRIVATE_KEY):
        raise AlipayError("未配置支付宝应用私钥 WIFIDOG_ALIPAY_PRIVATE_KEY")
    try:
        return serialization.load_pem_private_key(
            _pem(config.ALIPAY_PRIVATE_KEY, "PRIVATE KEY").encode(), password=None
        )
    except Exception as exc:  # noqa: BLE001
        raise AlipayError("支付宝应用私钥格式不正确，应为 PKCS#8 PEM") from exc


def _public_key():
    if not _configured(config.ALIPAY_PUBLIC_KEY):
        raise AlipayError("未配置支付宝平台公钥 WIFIDOG_ALIPAY_PUBLIC_KEY")
    try:
        return serialization.load_pem_public_key(
            _pem(config.ALIPAY_PUBLIC_KEY, "PUBLIC KEY").encode()
        )
    except Exception as exc:  # noqa: BLE001
        raise AlipayError("支付宝平台公钥格式不正确") from exc


def _sign(content: str) -> str:
    signature = _private_key().sign(
        content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode("ascii")


def _sign_content(params: dict[str, Any]) -> str:
    return "&".join(
        f"{k}={v}" for k, v in sorted(params.items())
        if k not in ("sign",) and v is not None and str(v) != ""
    )


def _sign_params(params: dict[str, Any]) -> dict[str, Any]:
    params = {k: v for k, v in params.items() if v is not None and str(v) != ""}
    params["sign"] = _sign(_sign_content(params))
    return params


def verify_params(data: dict[str, Any]) -> bool:
    """校验支付宝异步通知 / 应用网关 POST 的 RSA2 签名。"""
    sign = str(data.get("sign") or "").replace(" ", "+")
    if not sign:
        return False
    sign_type = str(data.get("sign_type") or "RSA2").upper()
    if sign_type != "RSA2":
        logger.warning("支付宝回调 sign_type=%s，仅接受 RSA2", sign_type)
        return False
    content = _sign_content({k: v for k, v in data.items() if k not in ("sign", "sign_type")})
    signature = base64.b64decode(sign)
    last_exc: Exception | None = None
    # 支付宝回调中的中文参数（如 subject）签名原文可能是 GBK/GB18030；
    # 与同步响应一致，依次尝试常见编码，任一通过即可。
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            _public_key().verify(
                signature, content.encode(encoding), padding.PKCS1v15(), hashes.SHA256()
            )
            if encoding != "utf-8":
                logger.info("支付宝回调使用 %s 编码验签成功", encoding)
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    logger.warning("支付宝签名校验失败（已尝试 UTF-8/GBK/GB18030）: %s", last_exc)
    return False


def _verify_response(raw_text: str, data: dict[str, Any]) -> bool:
    """校验开放平台同步响应签名。

    支付宝只对响应 JSON 中 ``xxx_response`` 节点的原始值验签，
    不是对去掉 sign 后的整个外层报文验签。这里用 ``raw_decode``
    精确截取该节点的原始文本，保留其原始转义格式。
    """
    sign = str(data.get("sign") or "")
    if not sign:
        return False
    node_key = next(
        (k for k, v in data.items() if k.endswith("_response") and isinstance(v, dict)),
        None,
    )
    if not node_key:
        logger.warning("支付宝同步响应缺少 xxx_response 节点")
        return False
    key_token = f'"{node_key}"'
    key_pos = raw_text.find(key_token)
    if key_pos < 0:
        logger.warning("支付宝同步响应找不到节点 %s 的原始文本", node_key)
        return False
    colon_pos = raw_text.find(":", key_pos + len(key_token))
    if colon_pos < 0:
        return False
    start = colon_pos + 1
    while start < len(raw_text) and raw_text[start].isspace():
        start += 1
    try:
        _, end = json.JSONDecoder().raw_decode(raw_text[start:])
    except ValueError:
        logger.warning("支付宝同步响应节点 %s 不是合法 JSON", node_key)
        return False
    unsigned = raw_text[start:start + end]
    # 支付宝返回的 JSON 文本本身是 UTF-8，但同步响应的签名原文可能是
    # 按 GBK/GB18030 编码后计算的（实测中文错误信息节点即如此）。
    # 依次尝试常见编码，任一通过即视为验签成功；不改变签名覆盖的节点原文。
    last_exc: Exception | None = None
    signature = base64.b64decode(sign)
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            _public_key().verify(
                signature, unsigned.encode(encoding), padding.PKCS1v15(), hashes.SHA256()
            )
            if encoding != "utf-8":
                logger.info("支付宝同步响应使用 %s 编码验签成功", encoding)
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    logger.warning("支付宝同步响应验签失败（已尝试 UTF-8/GBK/GB18030）: %s", last_exc)
    return False


def _base_params(method: str, **extra: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "app_id": config.ALIPAY_APPID,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
    }
    params.update({k: v for k, v in extra.items() if v is not None and str(v) != ""})
    return params


def _call(method: str, biz: dict[str, Any], timeout: int = 20, **extra: Any) -> dict[str, Any]:
    """调用支付宝 OpenAPI，返回对应 *_response 节点。"""
    if not _configured(config.ALIPAY_APPID):
        raise AlipayError("未配置支付宝 APPID WIFIDOG_ALIPAY_APPID")
    params = _base_params(method, **extra)
    params["biz_content"] = json.dumps(biz, ensure_ascii=False, separators=(",", ":"))
    params = _sign_params(params)
    try:
        response = requests.post(config.ALIPAY_GATEWAY_URL, data=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AlipayError(f"支付宝接口网络错误：{exc}") from exc

    raw = response.text
    try:
        payload = response.json()
    except ValueError as exc:
        raise AlipayError("支付宝接口返回非 JSON") from exc
    if not _verify_response(raw, payload):
        raise AlipayError("支付宝同步响应验签失败（检查 WIFIDOG_ALIPAY_PUBLIC_KEY 是否为支付宝平台公钥）")

    node = payload.get(method.replace(".", "_") + "_response")
    if not isinstance(node, dict):
        raise AlipayError("支付宝接口返回结构异常")
    if str(node.get("code")) != "10000":
        sub = node.get("sub_code") or node.get("code")
        msg = node.get("sub_msg") or node.get("msg") or "未知错误"
        raise AlipayError(f"支付宝业务失败：{sub} {msg}")
    return node


def pay_ready() -> bool:
    """手机网站支付是否可用：APPID / 应用私钥 / 支付宝平台公钥 / 回调地址。"""
    return bool(
        _configured(config.ALIPAY_APPID)
        and _configured(config.ALIPAY_PRIVATE_KEY)
        and _configured(config.ALIPAY_PUBLIC_KEY)
        and str(config.ALIPAY_NOTIFY_URL).startswith(("http://", "https://"))
        and not str(config.ALIPAY_NOTIFY_URL).startswith("https://your-domain")
    )


def oauth_ready() -> bool:
    """授权登录是否可用：APPID / 应用私钥 / 支付宝平台公钥 / 授权回调地址。"""
    return bool(
        _configured(config.ALIPAY_APPID)
        and _configured(config.ALIPAY_PRIVATE_KEY)
        and _configured(config.ALIPAY_PUBLIC_KEY)
        and str(config.ALIPAY_OAUTH_REDIRECT_URL).startswith(("http://", "https://"))
    )


def status() -> dict[str, Any]:
    """给后台的状态摘要，不返回任何私钥内容。"""
    appid = config.ALIPAY_APPID or ""
    masked = (appid[:4] + "****" + appid[-4:]) if len(appid) >= 8 else ("****" if appid else "")
    return {
        "app_id": masked,
        "app_id_configured": _configured(appid),
        "private_key_configured": _configured(config.ALIPAY_PRIVATE_KEY),
        "public_key_configured": _configured(config.ALIPAY_PUBLIC_KEY),
        "notify_url": config.ALIPAY_NOTIFY_URL,
        "return_url": config.ALIPAY_RETURN_URL,
        "oauth_redirect_url": config.ALIPAY_OAUTH_REDIRECT_URL,
        "gateway_url": config.ALIPAY_GATEWAY_URL,
        "app_gateway_url": str(config.ALIPAY_NOTIFY_URL).replace("/pay/notify/alipay", "/alipay/gateway"),
        "pay_ready": pay_ready(),
        "oauth_ready": oauth_ready(),
        "mode": "public_key_rsa2",
    }


def create_wap_pay(
    order_no: str,
    amount_fen: int,
    subject: str,
    notify_url: Optional[str] = None,
    return_url: Optional[str] = None,
) -> str:
    """手机网站支付：返回可跳转的支付宝收银台 URL。"""
    notify_url = notify_url or config.ALIPAY_NOTIFY_URL
    return_url = return_url or config.ALIPAY_RETURN_URL
    biz = {
        "out_trade_no": order_no,
        "total_amount": amount_text(amount_fen),
        "subject": (subject or "WiFi 上网套餐")[:256],
        "product_code": "QUICK_WAP_WAY",
    }
    params = _base_params("alipay.trade.wap.pay", notify_url=notify_url, return_url=return_url)
    params["biz_content"] = json.dumps(biz, ensure_ascii=False, separators=(",", ":"))
    params = _sign_params(params)
    return f"{config.ALIPAY_GATEWAY_URL}?{urlencode(params)}"


def create_page_pay(order_no: str, amount_fen: int, subject: str, **kwargs: Any) -> str:
    """电脑网站支付（A 范围未启用，保留接口）。"""
    notify_url = kwargs.get("notify_url") or config.ALIPAY_NOTIFY_URL
    return_url = kwargs.get("return_url") or config.ALIPAY_RETURN_URL
    biz = {
        "out_trade_no": order_no,
        "total_amount": amount_text(amount_fen),
        "subject": (subject or "WiFi 上网套餐")[:256],
        "product_code": "FAST_INSTANT_TRADE_PAY",
    }
    params = _base_params("alipay.trade.page.pay", notify_url=notify_url, return_url=return_url)
    params["biz_content"] = json.dumps(biz, ensure_ascii=False, separators=(",", ":"))
    params = _sign_params(params)
    return f"{config.ALIPAY_GATEWAY_URL}?{urlencode(params)}"


def create_qr_pay(order_no: str, amount_fen: int, subject: str) -> str:
    """当面付预下单（A 范围未启用，保留接口）。"""
    node = _call("alipay.trade.precreate", {
        "out_trade_no": order_no,
        "total_amount": amount_text(amount_fen),
        "subject": (subject or "WiFi 上网套餐")[:256],
    })
    return str(node.get("qr_code") or "")


def query_trade(order_no: str) -> dict[str, Any]:
    return _call("alipay.trade.query", {"out_trade_no": order_no})


def close_trade(order_no: str) -> dict[str, Any]:
    return _call("alipay.trade.close", {"out_trade_no": order_no})


def refund_trade(order_no: str, amount_fen: int, reason: str, out_request_no: str) -> dict[str, Any]:
    reason_text = (reason or "用户申请退款")[:256]
    payload = {
        "out_trade_no": order_no,
        "refund_amount": amount_text(amount_fen),
        "refund_reason": reason_text,
        "out_request_no": out_request_no,
    }
    try:
        return _call("alipay.trade.refund", payload)
    except AlipayError as exc:
        # 实测部分网关对中文 refund_reason 的请求签名校验不一致，
        # 会返回 isv.invalid-signature；同 refund_no 重试是幂等的。
        # 本地仍保留中文原因，仅将渠道侧原因降级为 ASCII。
        if "isv.invalid-signature" not in str(exc) or reason_text.isascii():
            raise
        logger.warning("支付宝退款中文原因签名失败，降级 ASCII 后重试: %s", exc)
        payload["refund_reason"] = f"Refund {out_request_no}"[:256]
        return _call("alipay.trade.refund", payload)


def refund_query(order_no: str, out_request_no: str) -> dict[str, Any]:
    return _call("alipay.trade.fastpay.refund.query", {
        "out_trade_no": order_no,
        "out_request_no": out_request_no,
    })


def bill_download_url(bill_date: str) -> str:
    node = _call("alipay.data.dataservice.bill.downloadurl.query", {
        "bill_type": "trade",
        "bill_date": bill_date,
    })
    return str(node.get("bill_download_url") or "")


def fetch_bill_rows(bill_date: str) -> dict[str, Any]:
    """下载交易账单 ZIP 并解析 CSV，供后台对账。"""
    url = bill_download_url(bill_date)
    if not url.startswith(("http://", "https://")):
        raise AlipayError("支付宝未返回账单下载地址")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AlipayError(f"账单下载失败：{exc}") from exc

    content = resp.content
    text = ""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            candidates = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not candidates:
                raise AlipayError("账单压缩包内没有 CSV")
            raw = zf.read(candidates[0])
    else:
        raw = content
    for encoding in ("gb18030", "utf-8", "utf-8-sig"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise AlipayError("账单编码无法识别")

    rows = list(csv.reader(io.StringIO(text)))
    header_index = None
    for i, row in enumerate(rows):
        joined = ",".join(row)
        if "商户订单号" in joined and "支付宝交易号" in joined:
            header_index = i
            break
    if header_index is None:
        raise AlipayError("账单缺少表头（商户订单号 / 支付宝交易号）")
    header = [str(c).strip().lstrip("\ufeff") for c in rows[header_index]]
    data = []
    for row in rows[header_index + 1:]:
        if not row or len(row) < 3:
            continue
        item = {header[i]: row[i].strip() if i < len(row) else "" for i in range(len(header))}
        if item.get("商户订单号") or item.get("支付宝交易号"):
            data.append(item)
    return {"bill_date": bill_date, "header": header, "rows": data, "count": len(data)}


def oauth_authorize_url(state: str, redirect_uri: Optional[str] = None) -> str:
    redirect_uri = redirect_uri or config.ALIPAY_OAUTH_REDIRECT_URL
    if not redirect_uri:
        raise AlipayError("未配置授权回调地址 WIFIDOG_ALIPAY_OAUTH_REDIRECT_URL")
    query = urlencode({
        "app_id": config.ALIPAY_APPID,
        "scope": config.ALIPAY_OAUTH_SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"{config.ALIPAY_AUTH_URL}?{query}"


def oauth_token(auth_code: str) -> dict[str, Any]:
    return _call("alipay.system.oauth.token", {
        "grant_type": "authorization_code",
        "code": auth_code,
    })


def user_info(access_token: str) -> dict[str, Any]:
    return _call("alipay.user.info.share", {"auth_token": access_token})


def public_key_fingerprint() -> str:
    """平台公钥指纹，仅用于后台核对，不泄露公钥全文。"""
    try:
        der = _public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(der).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def notify_event_key(data: dict[str, Any]) -> str:
    notify_id = str(data.get("notify_id") or "").strip()
    if notify_id:
        return f"alipay:notify:{notify_id}"
    trade_no = str(data.get("trade_no") or "").strip()
    status = str(data.get("trade_status") or "").strip()
    return f"alipay:notify:{trade_no}:{status}"
