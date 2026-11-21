"""短信发送：mock / http webhook / aliyun。

provider 由调用方按运行模式决定（settings_store.sms_provider）：
  test 模式      -> mock（验证码写入日志并回显，便于联调）
  production 模式 -> 配置的真实通道；未配置就绪时也回退 mock 并告警
"""
import base64
import hashlib
import hmac
import json
import logging
import random
import secrets
import time
import urllib.parse

import requests

from .config import (
    SMS_ACCESS_KEY, SMS_CODE_EXPIRE, SMS_SECRET_KEY, SMS_SIGN_NAME, SMS_TEMPLATE_CODE,
)

logger = logging.getLogger("wifidog.sms")


def generate_code() -> str:
    return f"{random.randint(1000, 9999)}"


def _special_url_encode(s: str) -> str:
    """阿里云 POP 签名要求的编码方式。"""
    return (
        urllib.parse.quote(s, safe="")
        .replace("+", "%20")
        .replace("*", "%2A")
        .replace("%7E", "~")
    )


def _send_aliyun(phone: str, code: str) -> bool:
    params = {
        "AccessKeyId": SMS_ACCESS_KEY,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou",
        "SignName": SMS_SIGN_NAME,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "TemplateCode": SMS_TEMPLATE_CODE,
        "TemplateParam": json.dumps({"code": code}, separators=(",", ":")),
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
    }
    sorted_params = sorted(params.items())
    canon = "&".join(f"{_special_url_encode(k)}={_special_url_encode(v)}" for k, v in sorted_params)
    string_to_sign = "GET&%2F&" + _special_url_encode(canon)
    sig = hmac.new(
        (SMS_SECRET_KEY + "&").encode(), string_to_sign.encode(), hashlib.sha1
    ).digest()
    params["Signature"] = base64.b64encode(sig).decode()
    try:
        r = requests.get("https://dysmsapi.aliyuncs.com/", params=params, timeout=10)
        data = r.json()
        ok = data.get("Code") == "OK"
        if not ok:
            logger.warning("aliyun sms failed: %s", data)
        return ok
    except Exception as e:  # noqa: BLE001
        logger.error("aliyun sms error: %s", e)
        return False


def _send_http(phone: str, code: str) -> bool:
    """转发到自建短信网关：POST {phone, code}（URL 配置在 SMS_SECRET_KEY）。"""
    try:
        r = requests.post(SMS_SECRET_KEY, json={"phone": phone, "code": code}, timeout=10)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.error("http sms error: %s", e)
        return False


def send_code(phone: str, code: str, provider: str = "mock") -> bool:
    """发送验证码。返回是否发送成功。"""
    if provider == "aliyun":
        return _send_aliyun(phone, code)
    if provider == "http":
        return _send_http(phone, code)
    # mock：测试模式使用，验证码写入日志并在接口回显
    logger.info("[MOCK SMS] phone=%s code=%s (expires in %ss)", phone, code, SMS_CODE_EXPIRE)
    return True


def mask_phone(phone):
    if not phone or len(phone) < 7:
        return phone or ""
    return f"{phone[:3]}****{phone[-4:]}"
