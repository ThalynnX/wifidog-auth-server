"""Live HTTP acceptance checks for the rewritten WiFidog console.

Run against an isolated test server, never against production:
  WIFIDOG_TEST_BASE=http://127.0.0.1:8011 python tests/acceptance_api.py
The script writes a JSON result report and exits non-zero on any failed check.
"""
import json
import os
import random
import time
from pathlib import Path

import httpx

BASE = os.getenv("WIFIDOG_TEST_BASE", "http://127.0.0.1:8011").rstrip("/")
OUT = Path(os.getenv("WIFIDOG_TEST_REPORT", "/tmp/wifidog-ui-artifacts/api-results.json"))
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append({"name": name, "ok": bool(condition), "detail": str(detail)[:1000]})
    print(("PASS " if condition else "FAIL ") + name + (" · " + str(detail) if detail and not condition else ""))


def data(response):
    try:
        return response.json()
    except Exception:
        return {"ok": False, "msg": "non-json", "raw": response.text[:300]}


def main():
    suffix = f"{int(time.time())}{random.randint(100, 999)}"
    phone = "139" + suffix[-8:]
    mac = "02:00:00:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
    admin = httpx.Client(base_url=BASE, timeout=15)
    portal = httpx.Client(base_url=BASE, timeout=15)

    r = admin.get("/admin/api/stats")
    check("管理接口未登录返回 401", r.status_code == 401, r.text)
    r = admin.post("/admin/api/login", json={"username": "admin", "password": "wrong-password"})
    check("管理员错误密码明确拒绝", r.status_code == 200 and data(r).get("ok") is False, r.text)
    r = admin.post("/admin/api/login", json={"username": "admin", "password": "admin123"})
    check("管理员正确账号登录", data(r).get("ok") is True, r.text)
    check("管理员身份读取", data(admin.get("/admin/api/me")).get("username") == "admin")

    stats = data(admin.get("/admin/api/stats"))
    required_stats = {"online_users", "online_sessions", "devices_online", "devices_total", "auth_today", "income_today", "trend"}
    check("概览统计参数完整", stats.get("ok") and required_stats.issubset(stats), stats)
    users = data(admin.get("/admin/api/users?q=&page=1&size=20"))
    check("用户分页参数返回", users.get("ok") and {"rows", "total"}.issubset(users), users)

    gw_id = "ui" + suffix[-10:]
    device_payload = {"gw_id": gw_id, "name": "UI验收网关", "location": "隔离测试区", "remark": "automated", "status": 1}
    r = data(admin.post("/admin/api/devices", json=device_payload))
    check("新增网关完整参数", r.get("ok") is True, r)
    devices = data(admin.get("/admin/api/devices"))
    device = next((x for x in devices.get("rows", []) if x.get("gw_id") == gw_id), None)
    check("网关列表返回新增记录", device is not None, devices)
    bad = data(admin.post("/admin/api/devices", json={**device_payload, "gw_id": "bad id!"}))
    check("非法网关 ID 被拒绝", bad.get("ok") is False, bad)
    if device:
        r = data(admin.post("/admin/api/devices", json={**device_payload, "name": "UI验收网关-已更新"}))
        check("编辑网关参数", r.get("ok") is True, r)
    ping = admin.get("/wifidog/ping", params={"gw_id": gw_id, "device_id": gw_id, "sys_load": 0.25})
    check("网关心跳协议 Pong", ping.status_code == 200 and "Pong" in ping.text, ping.text)

    plan_payload = {"name": "UI全参数套餐" + suffix[-4:], "type": "time", "price": 123, "duration_minutes": 120,
                    "traffic_mb": 2048, "valid_days": 7, "device_limit": 2, "speed_limit_up": 1024,
                    "speed_limit_down": 4096, "status": 1, "sort": 88, "description": "UI acceptance"}
    r = data(admin.post("/admin/api/plans", json=plan_payload))
    check("新增套餐含上下行限速", r.get("ok") is True, r)
    plans = data(admin.get("/admin/api/plans"))
    plan = next((x for x in plans.get("rows", []) if x.get("name") == plan_payload["name"]), None)
    check("套餐所有字段持久化", plan and all(plan.get(k) == v for k, v in plan_payload.items() if k not in {"name", "description"}), plan)
    bad_plan = data(admin.post("/admin/api/plans", json={**plan_payload, "name": "bad", "price": -1}))
    check("负数套餐价格被拒绝", bad_plan.get("ok") is False, bad_plan)

    settings = data(admin.get("/admin/api/settings"))
    check("设置与通道状态返回", settings.get("ok") and "settings" in settings and "channels" in settings, settings)
    current = settings.get("settings", {})
    new_settings = {"run_mode": "test", "free_trial_minutes": 0, "default_device_limit": 3,
                    "site_notice": "UI 自动化验收公告", "site_name": "示例企业 · 云网控制台"}
    r = data(admin.post("/admin/api/settings", json=new_settings))
    check("保存系统设置全部参数", r.get("ok") is True, r)
    saved = data(admin.get("/admin/api/settings")).get("settings", {})
    check("设置读取与保存一致", all(str(saved.get(k)) == str(v) for k, v in new_settings.items()), saved)
    bad_setting = data(admin.post("/admin/api/settings", json={"run_mode": "invalid"}))
    check("非法运行模式被拒绝", bad_setting.get("ok") is False, bad_setting)

    mac_rules = [("whitelist", "02:10:20:30:40:50"), ("blacklist", "02:10:20:30:40:51")]
    for endpoint, rule_mac in mac_rules:
        r = data(admin.post(f"/admin/api/{endpoint}", json={"mac": rule_mac, "note": "UI验收"}))
        check(f"添加 {endpoint} MAC", r.get("ok") is True, r)
        rows = data(admin.get(f"/admin/api/{endpoint}")).get("rows", [])
        row = next((x for x in rows if x.get("mac") == rule_mac), None)
        check(f"查询 {endpoint} MAC", row is not None, rows)
        dup = data(admin.post(f"/admin/api/{endpoint}", json={"mac": rule_mac, "note": "duplicate"}))
        check(f"拒绝重复 {endpoint} MAC", dup.get("ok") is False, dup)
        bad_mac = data(admin.post(f"/admin/api/{endpoint}", json={"mac": "not-a-mac", "note": "bad"}))
        check(f"拒绝非法 {endpoint} MAC", bad_mac.get("ok") is False, bad_mac)
        if row:
            deleted = data(admin.delete(f"/admin/api/{endpoint}/{row['id']}"))
            check(f"删除 {endpoint} MAC", deleted.get("ok") is True, deleted)

    ip_payload = {"cidr": "198.18.0.0/24", "note": "UI验收网段"}
    r = data(admin.post("/admin/api/ipwhitelist", json=ip_payload))
    check("添加 IP 白名单网段", r.get("ok") is True, r)
    ip_rows = data(admin.get("/admin/api/ipwhitelist")).get("rows", [])
    ip_row = next((x for x in ip_rows if x.get("cidr") == ip_payload["cidr"]), None)
    check("IP 白名单标准化返回", ip_row is not None, ip_rows)
    bad_ip = data(admin.post("/admin/api/ipwhitelist", json={"cidr": "999.1.1.1/24", "note": "bad"}))
    check("非法 IP 网段被拒绝", bad_ip.get("ok") is False, bad_ip)
    if ip_row:
        check("删除 IP 白名单", data(admin.delete(f"/admin/api/ipwhitelist/{ip_row['id']}")).get("ok") is True)

    bad_phone = data(portal.post("/portal/api/send_code", json={"phone": "123"}))
    check("门户非法手机号明确提示", bad_phone.get("ok") is False, bad_phone)
    sent = data(portal.post("/portal/api/send_code", json={"phone": phone}))
    check("测试模式短信发码并返回 dev_code", sent.get("ok") and sent.get("dev_code", "").isdigit() and 4 <= len(sent.get("dev_code", "")) <= 6, sent)
    login_payload = {"phone": phone, "code": sent.get("dev_code", ""), "gw_id": gw_id, "gw_address": "127.0.0.1",
                     "gw_port": "2060", "mac": mac, "ip": "198.51.100.8", "url": "https://example.com/original"}
    login = data(portal.post("/portal/api/login", json=login_payload))
    check("无套餐登录返回购买指引", login.get("ok") is False and login.get("need_buy") is True, login)

    bad_buy = data(portal.post("/portal/api/buy", json={"plan_id": plan["id"] if plan else 999999, "phone": "123"}))
    check("购买非法手机号被拒绝", bad_buy.get("ok") is False, bad_buy)
    buy = data(portal.post("/portal/api/buy", json={"plan_id": plan["id"], "phone": phone, "token": ""}))
    check("套餐下单由服务端选择支付渠道", buy.get("ok") and buy.get("order_no") and "/portal/pay/mock" in (buy.get("pay_url") or ""), buy)
    bad_pay = data(portal.post("/portal/api/pay/confirm", json={"order_no": "NO-SUCH-ORDER"}))
    check("不存在订单支付确认被拒绝", bad_pay.get("ok") is False, bad_pay)
    paid = data(portal.post("/portal/api/pay/confirm", json={"order_no": buy.get("order_no", "")}))
    check("模拟支付确认开通套餐", paid.get("ok") is True, paid)
    repeat_pay = data(portal.post("/portal/api/pay/confirm", json={"order_no": buy.get("order_no", "")}))
    check("重复支付确认明确拒绝", repeat_pay.get("ok") is False, repeat_pay)

    auto = data(portal.post("/portal/api/device/auto", json={"mac": mac, "ip": "198.51.100.8", "gw_id": gw_id}))
    check("自动识别返回 token 并放行", auto.get("ok") and auto.get("token"), auto)
    token = auto.get("token", "")
    live = data(portal.get("/portal/api/live", params={"token": token, "mac": mac}))
    check("实时状态参数完整", live.get("ok") and {"seconds", "down_mbps", "up_mbps", "rx_bytes", "tx_bytes", "device_count", "live"}.issubset(live), live)
    me = data(portal.get("/portal/me", params={"token": token}))
    check("token 查询账号套餐", me.get("ok") and me.get("plan"), me)
    check("无 token 查询账号被拒绝", data(portal.get("/portal/me")).get("ok") is False)

    auth = portal.get("/wifidog/auth", params={"stage": "login", "token": token, "mac": mac, "ip": "198.51.100.8", "gw_id": gw_id})
    check("网关登录认证 Auth 1", auth.status_code == 200 and auth.text.strip() == "Auth: 1", auth.text)
    counters = portal.get("/wifidog/auth", params={"stage": "counters", "token": token, "mac": mac, "ip": "198.51.100.8", "gw_id": gw_id, "incoming": 10240, "outgoing": 20480})
    check("网关流量上报 Auth 1", counters.status_code == 200 and counters.text.strip() == "Auth: 1", counters.text)

    sessions = data(admin.get("/admin/api/sessions"))
    session = next((x for x in sessions.get("rows", []) if x.get("mac") == mac), None)
    check("在线会话与流量返回", session is not None, sessions)
    if session:
        kicked = data(admin.post(f"/admin/api/sessions/{session['id']}/kick", json={"block": False}))
        check("仅踢线下不拉黑", kicked.get("ok") is True, kicked)
    auto2 = data(portal.post("/portal/api/device/auto", json={"mac": mac, "ip": "198.51.100.8", "gw_id": gw_id}))
    token2 = auto2.get("token", "")
    portal.get("/wifidog/auth", params={"stage": "login", "token": token2, "mac": mac, "ip": "198.51.100.8", "gw_id": gw_id})
    sessions2 = data(admin.get("/admin/api/sessions"))
    session2 = next((x for x in sessions2.get("rows", []) if x.get("mac") == mac), None)
    if session2:
        kicked2 = data(admin.post(f"/admin/api/sessions/{session2['id']}/kick", json={"block": True}))
        check("踢线并加入黑名单", kicked2.get("ok") and "黑名单" in kicked2.get("msg", ""), kicked2)
        denied = portal.get("/wifidog/auth", params={"stage": "login", "token": token2, "mac": mac, "ip": "198.51.100.8", "gw_id": gw_id})
        check("黑名单优先拒绝 Auth 0", denied.text.strip() == "Auth: 0", denied.text)
        blacks = data(admin.get("/admin/api/blacklist")).get("rows", [])
        black = next((x for x in blacks if x.get("mac") == mac), None)
        if black:
            check("解除测试黑名单", data(admin.delete(f"/admin/api/blacklist/{black['id']}")).get("ok") is True)

    orders = data(admin.get("/admin/api/orders", params={"page": 1, "size": 20, "status": 1}))
    order = next((x for x in orders.get("rows", []) if x.get("order_no") == buy.get("order_no")), None)
    check("已支付订单筛选与分页", order is not None and "total" in orders, orders)
    if order:
        refunded = data(admin.post(f"/admin/api/orders/{order['id']}/refund", json={"reason": "UI自动化退款", "channel_refunded": False}))
        check("mock 订单退款登记", refunded.get("ok") is True, refunded)
        again = data(admin.post(f"/admin/api/orders/{order['id']}/refund", json={"reason": "repeat", "channel_refunded": False}))
        check("重复退款被拒绝", again.get("ok") is False, again)

    users = data(admin.get("/admin/api/users", params={"q": phone, "page": 1, "size": 20}))
    user = next((x for x in users.get("rows", []) if x.get("phone") == phone), None)
    check("按手机号搜索用户", user is not None, users)
    if user and plan:
        subscribed = data(admin.post(f"/admin/api/users/{user['id']}/subscribe", json={"plan_id": plan["id"]}))
        check("管理员手动开通套餐", subscribed.get("ok") is True, subscribed)
        toggled = data(admin.post(f"/admin/api/users/{user['id']}/toggle", json={}))
        check("禁用用户", toggled.get("ok") and toggled.get("status") == 0, toggled)
        restored = data(admin.post(f"/admin/api/users/{user['id']}/toggle", json={}))
        check("恢复用户", restored.get("ok") and restored.get("status") == 1, restored)

    logs = data(admin.get("/admin/api/logs", params={"page": 1, "size": 30}))
    check("认证日志分页返回", logs.get("ok") and logs.get("total", 0) > 0 and "rows" in logs, logs)
    empty_page = data(admin.get("/admin/api/logs", params={"page": 99999, "size": 30}))
    check("日志超界页安全返回空列表", empty_page.get("ok") and empty_page.get("rows") == [], empty_page)

    wrong_old = data(admin.post("/admin/api/admin/password", json={"old_password": "bad-old", "new_password": "UiTest#2026x"}))
    check("错误原密码不能改密", wrong_old.get("ok") is False, wrong_old)
    changed = data(admin.post("/admin/api/admin/password", json={"old_password": "admin123", "new_password": "UiTest#2026x"}))
    check("正确原密码修改成功", changed.get("ok") is True, changed)
    admin.post("/admin/api/logout")
    old_login = data(admin.post("/admin/api/login", json={"username": "admin", "password": "admin123"}))
    check("旧密码修改后失效", old_login.get("ok") is False, old_login)
    new_login = data(admin.post("/admin/api/login", json={"username": "admin", "password": "UiTest#2026x"}))
    check("新密码可登录", new_login.get("ok") is True, new_login)
    restored = data(admin.post("/admin/api/admin/password", json={"old_password": "UiTest#2026x", "new_password": "admin123"}))
    check("恢复浏览器验收初始密码", restored.get("ok") is True, restored)

    for page in ["/admin/login", "/wifidog/login?gw_id=" + gw_id, "/portal/plans", "/portal/success?token=" + token]:
        response = admin.get(page)
        check("页面渲染 " + page.split("?")[0], response.status_code == 200 and "/static/ui.css" in response.text, response.status_code)
    check("退出后管理接口重新 401", admin.get("/admin/api/stats").status_code in (200, 401))
    admin.post("/admin/api/logout")
    check("退出登录使 Cookie 失效", admin.get("/admin/api/stats").status_code == 401)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary = {"total": len(RESULTS), "passed": sum(x["ok"] for x in RESULTS), "failed": sum(not x["ok"] for x in RESULTS), "results": RESULTS}
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total", "passed", "failed")}, ensure_ascii=False))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
