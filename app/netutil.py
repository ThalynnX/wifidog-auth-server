"""可信代理下的客户端 IP 解析。

默认只信任本机 Nginx（127.0.0.1 / ::1）。只有直连 peer 在可信代理列表内时，
才读取 X-Forwarded-For；并且从右往左取第一个非可信代理地址，避免客户端
伪造 XFF 第一段绕过限流。
"""
import ipaddress
import os
from typing import Any

TRUSTED_PROXIES = {
    x.strip()
    for x in os.getenv("WIFIDOG_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if x.strip()
}


def _is_trusted(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    if v in TRUSTED_PROXIES:
        return True
    try:
        addr = ipaddress.ip_address(v.split("%")[0])
    except ValueError:
        return False
    for item in TRUSTED_PROXIES:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        if net.version == addr.version and addr in net:
            return True
    return False


def client_ip(request: Any) -> str:
    """返回真实客户端 IP；request 可兼容 FastAPI Request 与 WebSocket。"""
    peer = ""
    client = getattr(request, "client", None)
    if client is not None:
        peer = getattr(client, "host", "") or ""
    headers = getattr(request, "headers", None)
    xff = ""
    try:
        xff = (headers.get("x-forwarded-for") or "") if headers is not None else ""
    except Exception:
        xff = ""
    if peer and _is_trusted(peer) and xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        for item in reversed(parts):
            if not _is_trusted(item):
                try:
                    ipaddress.ip_address(item.split("%")[0])
                except ValueError:
                    continue
                return item
        if parts:
            return parts[-1]
    return peer or ""


def client_ip_from_headers(headers, peer: str = "") -> str:
    """供非 Request 场景（如网关 WS）使用。"""
    xff = (headers.get("x-forwarded-for") or "") if headers is not None else ""
    if peer and _is_trusted(peer) and xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        for item in reversed(parts):
            if not _is_trusted(item):
                return item
        if parts:
            return parts[-1]
    return peer or ""
