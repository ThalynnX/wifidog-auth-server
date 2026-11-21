"""密码哈希、令牌签发与校验。"""
import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

import bcrypt

from .config import SECRET_KEY


def hash_password(raw: str) -> str:
    """bcrypt 哈希（$2b$ 格式）。

    直接调用 bcrypt，不依赖 passlib：passlib 1.7.4 与 bcrypt>=4.1（尤其 5.x）不兼容，
    会在首次建默认管理员时直接抛 ValueError。bcrypt 只接受前 72 字节，超出部分截断
    （与 passlib 默认行为一致），并兼容历史 passlib 生成的哈希。
    """
    return bcrypt.hashpw(raw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def _b64e(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _b64d(s: str) -> Optional[str]:
    try:
        pad = "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s + pad).decode()
    except Exception:
        return None


def make_signed_token(prefix: str, subject: str, ttl: int) -> str:
    """生成 `prefix.<b64(subject)>.<expire>.<sig>`；subject 经 base64 编码，
    可包含任意字符（含 "."），不会破坏分段解析。"""
    exp = int(time.time()) + ttl
    payload = f"{_b64e(subject)}.{exp}"
    return f"{prefix}.{payload}.{sign(payload)}"


def parse_signed_token(token: str, prefix: str) -> Optional[str]:
    """校验并返回 subject；失败返回 None。使用常量时间比较防时序侧信道。"""
    try:
        parts = token.split(".")
        if len(parts) != 4:
            return None
        p, subj_b64, exp, sig = parts
        if p != prefix:
            return None
        subject = _b64d(subj_b64)
        if subject is None:
            return None
        expected = sign(f"{subj_b64}.{exp}")
        if not hmac.compare_digest(expected, sig):
            return None
        if int(exp) < time.time():
            return None
        return subject
    except Exception:
        return None
