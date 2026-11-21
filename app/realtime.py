"""网关实时流量统计缓存。

数据源：网关 `wdctlx status client` 的 JSON（traffic.download_bytes / upload_bytes，
以及 incoming_rate / outgoing_rate）。服务端用两次采样的字节差算真实速率。
"""

import time

_LATEST = {}
TTL = 30.0


def _num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _store(mac, ip, rx, tx, rssi, gw_rate_down, gw_rate_up, gw_id):
    now = time.time()
    old = _LATEST.get(mac)
    down = up = 0.0
    if old and (now - old["ts"]) > 0.2:
        dt = now - old["ts"]
        if rx > old["rx"]:
            down = (rx - old["rx"]) * 8 / dt / 1e6
        if tx > old["tx"]:
            up = (tx - old["tx"]) * 8 / dt / 1e6
    # 字节差算不出速率（空载/首次采样）时，用网关自己给的 rate 兜底
    if down <= 0 and gw_rate_down > 0:
        down = gw_rate_down * 8 / 1e6
    if up <= 0 and gw_rate_up > 0:
        up = gw_rate_up * 8 / 1e6
    _LATEST[mac] = {
        "mac": mac,
        "ip": ip,
        "rx": rx,
        "tx": tx,
        "down_mbps": round(down, 3),
        "up_mbps": round(up, 3),
        "rssi": rssi,
        "ts": now,
        "gw_id": gw_id or "",
        "online": True,
    }


def ingest_gateway(clients, gw_id=""):
    """处理网关原样上报的 clients 数组（wdctlx status client 格式）。"""
    n = 0
    for c in clients or []:
        if not isinstance(c, dict):
            continue
        if c.get("status") not in (1, "1", None):
            continue
        mac = (c.get("mac") or "").strip().lower()
        if not mac:
            continue
        t = c.get("traffic") or {}
        _store(
            mac,
            c.get("ip") or "",
            _num(t.get("download_bytes")) + _num(t.get("download_bytes_v6")),
            _num(t.get("upload_bytes")) + _num(t.get("upload_bytes_v6")),
            c.get("signal") or "",
            _num(t.get("incoming_rate")),
            _num(t.get("outgoing_rate")),
            gw_id or c.get("gw_id") or "",
        )
        n += 1
    return n


def ingest(gw_id, clients):
    """兼容旧格式 {mac, ip, rx, tx, rssi}。"""
    n = 0
    for c in clients or []:
        if not isinstance(c, dict):
            continue
        mac = (c.get("mac") or "").strip().lower()
        if not mac:
            continue
        _store(mac, c.get("ip") or "", _num(c.get("rx")), _num(c.get("tx")),
               c.get("rssi") or "", 0, 0, gw_id)
        n += 1
    return n


def get(mac: str):
    d = _LATEST.get((mac or "").strip().lower())
    if not d:
        return None
    if time.time() - d["ts"] > TTL:
        return None
    return d


def online_count():
    """网关当前在线终端数（30 秒内有上报的终端）。"""
    now = time.time()
    return sum(1 for v in _LATEST.values() if now - v["ts"] <= TTL)
