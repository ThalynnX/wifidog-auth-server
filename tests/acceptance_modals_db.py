"""Modal/sub-window button and immediate SQLite persistence acceptance.

Runs only against an isolated local test server and database. For every major
form this checks Cancel, X, validation failure, OK, duplicate-click protection,
direct database state, and read-after-reload consistency.
"""
import json
import os
import sqlite3
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("WIFIDOG_TEST_BASE", "http://127.0.0.1:8011").rstrip("/")
DB = os.getenv("WIFIDOG_TEST_DB", "/tmp/wifidog-ui-acceptance.db")
OUT = Path(os.getenv("WIFIDOG_TEST_ARTIFACTS", "/tmp/wifidog-ui-artifacts"))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
ERRORS = []


def check(name, condition=True, detail=""):
    RESULTS.append({"name": name, "ok": bool(condition), "detail": str(detail)[:1000]})
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name + " " + str(detail))


def row(sql, args=()):
    with sqlite3.connect(DB, timeout=5) as con:
        con.row_factory = sqlite3.Row
        result = con.execute(sql, args).fetchone()
        return dict(result) if result else None


def rows(sql, args=()):
    with sqlite3.connect(DB, timeout=5) as con:
        con.row_factory = sqlite3.Row
        return [dict(x) for x in con.execute(sql, args).fetchall()]


def db_check(name, sql, args, predicate):
    started = time.monotonic()
    current = None
    for _ in range(50):
        current = row(sql, args)
        if current is not None and predicate(current):
            elapsed = round((time.monotonic() - started) * 1000)
            check(name, True, f"database committed and readable in {elapsed} ms")
            return current
        time.sleep(0.1)
    check(name, False, current)


def db_gone(name, sql, args=()):
    time.sleep(0.2)
    check(name, row(sql, args) is None, row(sql, args))


def visible(page, selector):
    page.locator(selector).wait_for(state="visible", timeout=10000)


def ok(page):
    page.locator(".modal-layer [data-ok]").last.click()


def double_ok(page):
    page.evaluate("""() => { const b=document.querySelector('.modal-layer [data-ok]'); b.click(); b.click(); }""")


def cancel(page):
    page.locator(".modal-layer .modal-foot [data-close]").last.click()


def close_x(page):
    page.locator(".modal-layer .modal-close").last.click()


def no_modal(page):
    page.locator(".modal-layer").wait_for(state="detached")


def search_user(page, phone):
    page.fill("#uq", phone)
    with page.expect_response("**/admin/api/users?*"):
        page.click("#userSearch")
    page.wait_for_function("phone => document.querySelector('#usersBox').textContent.includes(phone) && document.querySelectorAll('#usersBox tbody tr').length === 1", arg=phone)


def json_request(request, method, path, payload=None):
    return request.fetch(BASE + path, method=method, data=json.dumps(payload or {}), headers={"Content-Type": "application/json"})


def seed_paid_user(request, phone, mac, gw, plan_id, activate=False):
    sent = json_request(request, "POST", "/portal/api/send_code", {"phone": phone}).json()
    login = json_request(request, "POST", "/portal/api/login", {"phone": phone, "code": sent["dev_code"], "mac": mac, "ip": "198.51.100.80", "gw_id": gw, "url": "https://example.com/db-check"}).json()
    if not login.get("need_buy"):
        raise AssertionError("seed user expected need_buy: " + str(login))
    bought = json_request(request, "POST", "/portal/api/buy", {"plan_id": plan_id, "phone": phone, "token": ""}).json()
    return bought


def main():
    suffix = str(int(time.time()))[-8:]
    phone = "137" + suffix
    phone2 = "136" + suffix
    mac = "02:00:22:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
    mac2 = "02:00:33:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
    gw = "dbmodal" + suffix[-6:]
    with sync_playwright() as p:
        request = p.request.new_context(base_url=BASE)
        login = json_request(request, "POST", "/admin/api/login", {"username": "admin", "password": "admin123"}).json()
        check("专项测试管理员登录", login.get("ok") is True)
        json_request(request, "POST", "/admin/api/settings", {"run_mode": "test", "free_trial_minutes": 0, "default_device_limit": 3, "site_notice": "数据库专项验收", "site_name": "示例企业 · 云网控制台"})
        plans = request.get(BASE + "/admin/api/plans").json()["rows"]
        paid = seed_paid_user(request, phone, mac, gw, plans[0]["id"])
        json_request(request, "POST", "/portal/api/pay/confirm", {"order_no": paid["order_no"]})

        browser = p.chromium.launch(executable_path="/usr/local/bin/chromium", headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda exc: ERRORS.append("pageerror: " + str(exc)))
        page.on("console", lambda msg: ERRORS.append("console: " + msg.text) if msg.type == "error" else None)
        page.goto(BASE + "/admin/login")
        page.fill("#username", "admin"); page.fill("#password", "admin123"); page.click("#loginBtn"); page.wait_for_url("**/admin/")

        # Device modal: Cancel, X, validation, duplicate OK, DB, reload, edit, delete.
        page.click('[data-page="devices"]')
        page.click("#deviceAdd"); page.fill("#d_gw", "cancelgw"); cancel(page); no_modal(page)
        db_gone("设备取消按钮不写数据库", "SELECT * FROM devices WHERE gw_id=?", ("cancelgw",))
        page.click("#deviceAdd"); page.fill("#d_gw", "closegw"); close_x(page); no_modal(page)
        db_gone("设备关闭按钮不写数据库", "SELECT * FROM devices WHERE gw_id=?", ("closegw",))
        page.click("#deviceAdd"); page.fill("#d_gw", "bad id!"); ok(page); visible(page, ".modal-msg.show")
        db_gone("设备非法参数不写数据库", "SELECT * FROM devices WHERE gw_id=?", ("bad id!",)); close_x(page)
        page.click("#deviceAdd"); page.fill("#d_gw", gw); page.fill("#d_name", "落盘网关"); page.fill("#d_loc", "一楼机房"); page.fill("#d_rem", "全部字段"); page.select_option("#d_st", "1")
        double_ok(page); no_modal(page)
        db_check("设备确定后立即写入全部字段", "SELECT * FROM devices WHERE gw_id=?", (gw,), lambda r: r["name"] == "落盘网关" and r["location"] == "一楼机房" and r["remark"] == "全部字段" and r["status"] == 1)
        check("设备防重复点击仅一条记录", row("SELECT COUNT(*) AS n FROM devices WHERE gw_id=?", (gw,))["n"] == 1)
        page.reload(); visible(page, "#page-devices.active"); check("设备刷新页面后读回名称", "落盘网关" in page.locator("#devicesBox").inner_text())
        device_api = request.get(BASE + "/admin/api/devices").json()["rows"]
        check("设备读取接口包含数据库备注", next(x for x in device_api if x["gw_id"] == gw)["remark"] == "全部字段")
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="编辑").click(); page.fill("#d_name", "取消编辑"); cancel(page)
        check("设备编辑取消保持原值", row("SELECT * FROM devices WHERE gw_id=?", (gw,))["name"] == "落盘网关")
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="编辑").click(); page.fill("#d_name", "关闭编辑"); close_x(page)
        check("设备编辑关闭保持原值", row("SELECT * FROM devices WHERE gw_id=?", (gw,))["name"] == "落盘网关")
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="编辑").click(); page.fill("#d_name", "落盘网关-已更新"); page.fill("#d_loc", "二楼机房"); page.fill("#d_rem", "更新备注"); page.select_option("#d_st", "0"); ok(page); no_modal(page)
        db_check("设备编辑确定立即更新数据库", "SELECT * FROM devices WHERE gw_id=?", (gw,), lambda r: r["name"] == "落盘网关-已更新" and r["location"] == "二楼机房" and r["remark"] == "更新备注" and r["status"] == 0)
        page.reload(); visible(page, "#page-devices.active"); check("设备编辑后页面读回更新", "已更新" in page.locator("#devicesBox").inner_text())
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="删除").click(); cancel(page)
        check("设备删除取消保留记录", row("SELECT * FROM devices WHERE gw_id=?", (gw,)) is not None)
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="删除").click(); close_x(page)
        check("设备删除关闭保留记录", row("SELECT * FROM devices WHERE gw_id=?", (gw,)) is not None)
        page.locator("#devicesBox tr", has_text=gw).locator("button", has_text="删除").click(); ok(page); no_modal(page)
        db_gone("设备删除确定立即移除数据库记录", "SELECT * FROM devices WHERE gw_id=?", (gw,))

        # Failed save must not create dirty data.
        page.click("#deviceAdd"); page.fill("#d_gw", "networkgw"); page.fill("#d_name", "网络失败")
        page.route("**/admin/api/devices", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
        ok(page); page.wait_for_function("document.querySelector('.modal-msg.show')?.textContent.includes('网络')")
        db_gone("网络失败设备保存不写脏数据", "SELECT * FROM devices WHERE gw_id=?", ("networkgw",)); close_x(page); page.unroute("**/admin/api/devices")

        # Plan modal and every persisted numeric field.
        page.click('[data-page="plans"]')
        page.click("#planAdd"); page.fill("#p_name", "取消套餐"); cancel(page); db_gone("套餐取消不写库", "SELECT * FROM plans WHERE name=?", ("取消套餐",))
        page.click("#planAdd"); page.fill("#p_name", "关闭套餐"); close_x(page); db_gone("套餐关闭不写库", "SELECT * FROM plans WHERE name=?", ("关闭套餐",))
        page.click("#planAdd"); page.fill("#p_name", "落盘全参数套餐"); page.select_option("#p_type", "time"); page.fill("#p_price", "456"); page.fill("#p_dur", "240"); page.fill("#p_mb", "4096"); page.fill("#p_days", "9"); page.fill("#p_dev", "4"); page.fill("#p_up", "1111"); page.fill("#p_down", "2222"); page.fill("#p_sort", "66"); page.fill("#p_desc", "数据库读回")
        double_ok(page); no_modal(page)
        db_check("套餐确定后立即写入全部参数", "SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",), lambda r: r["price"] == 456 and r["duration_minutes"] == 240 and r["traffic_mb"] == 4096 and r["valid_days"] == 9 and r["device_limit"] == 4 and r["speed_limit_up"] == 1111 and r["speed_limit_down"] == 2222 and r["sort"] == 66 and r["description"] == "数据库读回")
        check("套餐防重复点击仅一条记录", row("SELECT COUNT(*) AS n FROM plans WHERE name=?", ("落盘全参数套餐",))["n"] == 1)
        page.reload(); visible(page, "#page-plans.active"); check("套餐刷新后读回限速与说明", "1111" in page.locator("#plansBox").inner_text() and "数据库读回" in page.locator("#plansBox").inner_text())
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="编辑").click(); page.fill("#p_up", "9999"); cancel(page)
        check("套餐编辑取消不写库", row("SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",))["speed_limit_up"] == 1111)
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="编辑").click(); page.fill("#p_up", "8888"); close_x(page)
        check("套餐编辑关闭不写库", row("SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",))["speed_limit_up"] == 1111)
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="编辑").click(); page.fill("#p_up", "3333"); page.fill("#p_down", "4444"); ok(page); no_modal(page)
        db_check("套餐编辑确定立即更新限速", "SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",), lambda r: r["speed_limit_up"] == 3333 and r["speed_limit_down"] == 4444)
        page.reload(); visible(page, "#page-plans.active"); check("套餐编辑刷新后读回新限速", "3333" in page.locator("#plansBox").inner_text())
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="删除").click(); cancel(page)
        check("套餐删除取消保留", row("SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",)) is not None)
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="删除").click(); close_x(page)
        check("套餐删除关闭保留", row("SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",)) is not None)
        page.locator("#plansBox tr", has_text="落盘全参数套餐").locator("button", has_text="删除").click(); ok(page); no_modal(page)
        db_gone("套餐删除确定立即写库", "SELECT * FROM plans WHERE name=?", ("落盘全参数套餐",))

        # User toggles and subscription dialog.
        user = row("SELECT * FROM users WHERE phone=?", (phone,)); uid = user["id"]
        page.click('[data-page="users"]'); search_user(page, phone)
        page.locator("#usersBox button", has_text="禁用").click(); cancel(page); check("用户禁用取消保持正常", row("SELECT * FROM users WHERE id=?", (uid,))["status"] == 1)
        page.locator("#usersBox button", has_text="禁用").click(); close_x(page); check("用户禁用关闭保持正常", row("SELECT * FROM users WHERE id=?", (uid,))["status"] == 1)
        page.locator("#usersBox button", has_text="禁用").click(); ok(page); no_modal(page)
        db_check("用户禁用确定立即写库", "SELECT * FROM users WHERE id=?", (uid,), lambda r: r["status"] == 0)
        page.reload(); visible(page, "#page-users.active"); search_user(page, phone); check("用户禁用刷新后读回状态", "禁用" in page.locator("#usersBox").inner_text())
        page.locator("#usersBox button", has_text="启用").click(); ok(page); no_modal(page)
        db_check("用户恢复确定立即写库", "SELECT * FROM users WHERE id=?", (uid,), lambda r: r["status"] == 1)
        before_subs = row("SELECT COUNT(*) AS n FROM subscriptions WHERE user_id=?", (uid,))["n"]
        page.locator("#usersBox button", has_text="开通套餐").click(); cancel(page); check("开通套餐取消不新增订阅", row("SELECT COUNT(*) AS n FROM subscriptions WHERE user_id=?", (uid,))["n"] == before_subs)
        page.locator("#usersBox button", has_text="开通套餐").click(); close_x(page); check("开通套餐关闭不新增订阅", row("SELECT COUNT(*) AS n FROM subscriptions WHERE user_id=?", (uid,))["n"] == before_subs)
        page.locator("#usersBox button", has_text="开通套餐").click(); page.select_option("#sub_plan", str(plans[1]["id"])); ok(page); no_modal(page)
        db_check("手动开通套餐确定立即生成订阅", "SELECT * FROM subscriptions WHERE user_id=? AND plan_id=? AND order_id IS NULL", (uid, plans[1]["id"]), lambda r: r["status"] == 1 and r["speed_limit_up"] == plans[1]["speed_limit_up"])
        page.reload(); visible(page, "#page-users.active"); search_user(page, phone); check("多套餐并存时读回当前有效套餐", plans[1]["name"] in page.locator("#usersBox").inner_text())

        # Order refund modal and subscription state.
        order = row("SELECT * FROM orders WHERE order_no=?", (paid["order_no"],)); oid = order["id"]
        page.click('[data-page="orders"]'); page.select_option("#ostatus", "1"); page.click("#orderRefresh")
        page.locator("#ordersBox tr", has_text=paid["order_no"]).locator("button", has_text="退款登记").click(); cancel(page)
        check("退款取消保持已支付", row("SELECT * FROM orders WHERE id=?", (oid,))["status"] == 1)
        page.locator("#ordersBox tr", has_text=paid["order_no"]).locator("button", has_text="退款登记").click(); close_x(page)
        check("退款关闭保持已支付", row("SELECT * FROM orders WHERE id=?", (oid,))["status"] == 1)
        page.locator("#ordersBox tr", has_text=paid["order_no"]).locator("button", has_text="退款登记").click(); page.fill("#r_reason", "弹窗数据库专项退款"); page.locator("#r_channel").check(); ok(page); no_modal(page)
        db_check("退款确定立即更新订单原因和时间", "SELECT * FROM orders WHERE id=?", (oid,), lambda r: r["status"] == 3 and r["refund_reason"] == "弹窗数据库专项退款" and r["refund_at"] is not None)
        db_check("退款同时停用对应订阅", "SELECT * FROM subscriptions WHERE order_id=?", (oid,), lambda r: r["status"] == 0)
        page.reload(); visible(page, "#page-orders.active"); page.select_option("#ostatus", "3"); page.click("#orderRefresh"); check("退款刷新后读回状态和原因", "弹窗数据库专项退款" in page.locator("#ordersBox").inner_text())
        page.click('[data-page="users"]'); search_user(page, phone)
        check("原订单退款后读回手动套餐", plans[1]["name"] in page.locator("#usersBox").inner_text())

        # Real online sessions: cancel/X/OK for kick, then kick+blacklist.
        auto = json_request(request, "POST", "/portal/api/device/auto", {"mac": mac, "ip": "198.51.100.80", "gw_id": gw}).json()
        request.get(BASE + "/wifidog/auth", params={"stage": "login", "token": auto["token"], "mac": mac, "ip": "198.51.100.80", "gw_id": gw})
        page.click('[data-page="sessions"]'); page.click("#sessionRefresh")
        session = row("SELECT * FROM sessions WHERE token=?", (auto["token"],)); sid = session["id"]
        page.locator("#sessionsBox tr", has_text=mac).locator("button", has_text="踢下线").click(); cancel(page); check("踢线取消保持在线", row("SELECT * FROM sessions WHERE id=?", (sid,))["status"] == 1)
        page.locator("#sessionsBox tr", has_text=mac).locator("button", has_text="踢下线").click(); close_x(page); check("踢线关闭保持在线", row("SELECT * FROM sessions WHERE id=?", (sid,))["status"] == 1)
        page.locator("#sessionsBox tr", has_text=mac).locator("button", has_text="踢下线").click(); ok(page); no_modal(page)
        db_check("仅踢线确定立即离线且不拉黑", "SELECT * FROM sessions WHERE id=?", (sid,), lambda r: r["status"] == 0 and r["stop_at"] is not None)
        check("仅踢线没有新增黑名单", row("SELECT * FROM mac_blacklist WHERE mac=?", (mac,)) is None)
        auto2 = json_request(request, "POST", "/portal/api/device/auto", {"mac": mac, "ip": "198.51.100.80", "gw_id": gw}).json()
        request.get(BASE + "/wifidog/auth", params={"stage": "login", "token": auto2["token"], "mac": mac, "ip": "198.51.100.80", "gw_id": gw})
        page.click("#sessionRefresh"); session2 = row("SELECT * FROM sessions WHERE token=?", (auto2["token"],)); sid2 = session2["id"]
        page.locator("#sessionsBox tr", has_text=mac).locator("button", has_text="踢线并拉黑").click(); cancel(page)
        check("踢线拉黑取消不写黑名单", row("SELECT * FROM mac_blacklist WHERE mac=?", (mac,)) is None and row("SELECT * FROM sessions WHERE id=?", (sid2,))["status"] == 1)
        page.locator("#sessionsBox tr", has_text=mac).locator("button", has_text="踢线并拉黑").click(); ok(page); no_modal(page)
        db_check("踢线拉黑确定立即写会话", "SELECT * FROM sessions WHERE id=?", (sid2,), lambda r: r["status"] == 0)
        db_check("踢线拉黑确定立即写黑名单", "SELECT * FROM mac_blacklist WHERE mac=?", (mac,), lambda r: "后台踢线拉黑" in r["note"])
        page.reload(); visible(page, "#page-sessions.active"); check("踢线后刷新读回在线列表", mac not in page.locator("#sessionsBox").inner_text())

        # Access-control form modals and direct table persistence.
        page.click('[data-page="whitelist"]')
        for kind, button, table, field, value, box in [
            ("MAC白名单", "#macAdd", "mac_whitelist", "mac", "02:44:55:66:77:88", "#wlBox"),
            ("MAC黑名单", "#blackAdd", "mac_blacklist", "mac", "02:44:55:66:77:89", "#blBox"),
        ]:
            page.click(button); page.fill("#m_mac", value); cancel(page); db_gone(kind + "取消不写库", f"SELECT * FROM {table} WHERE {field}=?", (value,))
            page.click(button); page.fill("#m_mac", value); close_x(page); db_gone(kind + "关闭不写库", f"SELECT * FROM {table} WHERE {field}=?", (value,))
            page.click(button); page.fill("#m_mac", value); page.fill("#m_note", kind + "落盘"); double_ok(page); no_modal(page)
            db_check(kind + "确定立即写库", f"SELECT * FROM {table} WHERE {field}=?", (value,), lambda r: "落盘" in r["note"])
            check(kind + "防重复点击仅一条", row(f"SELECT COUNT(*) AS n FROM {table} WHERE {field}=?", (value,))["n"] == 1)
            page.reload(); visible(page, "#page-whitelist.active"); check(kind + "刷新后读回", value in page.locator(box).inner_text())
            page.locator(box).locator("button").first.click(); cancel(page); check(kind + "删除取消保留", row(f"SELECT * FROM {table} WHERE {field}=?", (value,)) is not None)
            page.locator(box).locator("button").first.click(); close_x(page); check(kind + "删除关闭保留", row(f"SELECT * FROM {table} WHERE {field}=?", (value,)) is not None)
            page.locator(box).locator("button").first.click(); ok(page); no_modal(page); db_gone(kind + "删除确定立即写库", f"SELECT * FROM {table} WHERE {field}=?", (value,))

        ip_value = "203.0.113.0/24"
        page.click("#ipAdd"); page.fill("#i_cidr", ip_value); cancel(page); db_gone("IP白名单取消不写库", "SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,))
        page.click("#ipAdd"); page.fill("#i_cidr", ip_value); close_x(page); db_gone("IP白名单关闭不写库", "SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,))
        page.click("#ipAdd"); page.fill("#i_cidr", ip_value); page.fill("#i_note", "IP落盘"); double_ok(page); no_modal(page)
        db_check("IP白名单确定立即写库", "SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,), lambda r: r["note"] == "IP落盘")
        page.reload(); visible(page, "#page-whitelist.active"); check("IP白名单刷新后读回", ip_value in page.locator("#ipwlBox").inner_text())
        page.locator("#ipwlBox button").first.click(); cancel(page); check("IP删除取消保留", row("SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,)) is not None)
        page.locator("#ipwlBox button").first.click(); close_x(page); check("IP删除关闭保留", row("SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,)) is not None)
        page.locator("#ipwlBox button").first.click(); ok(page); no_modal(page); db_gone("IP删除确定立即写库", "SELECT * FROM ip_whitelist WHERE cidr=?", (ip_value,))

        # Settings and password: DB values, reload, hash and actual old/new login.
        page.click('[data-page="settings"]'); page.fill("#set_site_name", "落盘站点名称"); page.fill("#set_notice", "落盘公告内容"); page.fill("#set_free", "17"); page.fill("#set_limit", "6")
        page.evaluate("() => { const b=document.querySelector('#settingsSave'); b.click(); b.click(); }")
        db_check("系统设置站点名称立即写库", "SELECT * FROM settings WHERE key='site_name'", (), lambda r: r["value"] == "落盘站点名称")
        db_check("系统设置公告立即写库", "SELECT * FROM settings WHERE key='site_notice'", (), lambda r: r["value"] == "落盘公告内容")
        db_check("系统设置免费分钟立即写库", "SELECT * FROM settings WHERE key='free_trial_minutes'", (), lambda r: r["value"] == "17")
        db_check("系统设置设备数立即写库", "SELECT * FROM settings WHERE key='default_device_limit'", (), lambda r: r["value"] == "6")
        page.reload(); visible(page, "#page-settings.active")
        check("设置刷新后四项全部读回", page.locator("#set_site_name").input_value() == "落盘站点名称" and page.locator("#set_notice").input_value() == "落盘公告内容" and page.locator("#set_free").input_value() == "17" and page.locator("#set_limit").input_value() == "6")
        hash_before = row("SELECT password_hash FROM admins WHERE username='admin'")["password_hash"]
        page.fill("#pwd_old", "wrong-old"); page.fill("#pwd_new", "Database#2026x"); page.fill("#pwd_confirm", "Database#2026x"); page.click("#passwordSave"); page.wait_for_timeout(400)
        check("错误原密码不改变数据库哈希", row("SELECT password_hash FROM admins WHERE username='admin'")["password_hash"] == hash_before)
        page.fill("#pwd_old", "admin123"); page.fill("#pwd_new", "Database#2026x"); page.fill("#pwd_confirm", "Database#2026x"); page.click("#passwordSave")
        db_check("正确改密立即更新数据库哈希", "SELECT * FROM admins WHERE username='admin'", (), lambda r: r["password_hash"] != hash_before)
        fresh = p.request.new_context(base_url=BASE)
        check("新密码立即可登录", json_request(fresh, "POST", "/admin/api/login", {"username": "admin", "password": "Database#2026x"}).json().get("ok") is True)
        json_request(request, "POST", "/admin/api/admin/password", {"old_password": "Database#2026x", "new_password": "admin123"}); fresh.dispose()
        page.click("#logoutBtn"); cancel(page); check("退出取消保持控制台", page.locator("#page-settings").is_visible())
        page.click("#logoutBtn"); close_x(page); check("退出关闭保持控制台", page.locator("#page-settings").is_visible())

        # Portal payment confirmation: Cancel/X do not pay; OK persists order/subscription/session.
        json_request(request, "POST", "/admin/api/settings", {"run_mode": "test", "free_trial_minutes": 0, "default_device_limit": 6, "site_notice": "落盘公告内容", "site_name": "落盘站点名称"})
        paid2 = seed_paid_user(request, phone2, mac2, gw, plans[0]["id"])
        portal = context.new_page(); portal.on("pageerror", lambda exc: ERRORS.append("portal pageerror: " + str(exc)))
        portal.goto(BASE + "/wifidog/login?gw_id=" + gw + "&mac=" + mac2 + "&ip=198.51.100.81")
        portal.evaluate("([phone,mac,gw]) => { localStorage.setItem('wg_phone',phone);localStorage.setItem('wg_mac',mac);localStorage.setItem('wg_gw_id',gw);localStorage.setItem('wg_ip','198.51.100.81'); }", [phone2, mac2, gw])
        portal.goto(BASE + paid2["pay_url"])
        portal.click("#payBtn"); cancel(portal); check("支付确认取消保持待支付", row("SELECT * FROM orders WHERE order_no=?", (paid2["order_no"],))["status"] == 0)
        portal.click("#payBtn"); close_x(portal); check("支付确认关闭保持待支付", row("SELECT * FROM orders WHERE order_no=?", (paid2["order_no"],))["status"] == 0)
        portal.click("#payBtn"); ok(portal); no_modal(portal)
        order2 = db_check("支付确定立即更新订单", "SELECT * FROM orders WHERE order_no=?", (paid2["order_no"],), lambda r: r["status"] == 1 and r["paid_at"] is not None and r["pay_type"] == "mock")
        db_check("支付确定立即生成有效订阅", "SELECT * FROM subscriptions WHERE order_id=?", (order2["id"],), lambda r: r["status"] == 1)
        db_check("支付后自动建立设备会话", "SELECT * FROM sessions WHERE mac=? ORDER BY id DESC LIMIT 1", (mac2,), lambda r: bool(r["token"]))
        portal.wait_for_url("**/portal/success**"); portal.wait_for_function("!document.querySelector('#planName').textContent.includes('正在读取')")
        check("支付成功页读取真实套餐", plans[0]["name"] in portal.locator("#planName").inner_text())

        # Rebind sub-window: Back does not change MAC; confirm updates DB and session.
        rebind_mac = "02:00:44:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
        portal.goto(BASE + "/wifidog/login?gw_id=" + gw + "&mac=" + rebind_mac + "&ip=198.51.100.82")
        portal.fill("#phone", phone2); portal.click("#sendBtn"); portal.wait_for_function("document.querySelector('#code').value.length >= 4")
        sms_id = row("SELECT * FROM sms_codes WHERE phone=? ORDER BY id DESC LIMIT 1", (phone2,))["id"]
        portal.click("#smsLoginBtn"); visible(portal, "#rebindArea")
        check("换绑弹窗出现时数据库仍绑定原 MAC", row("SELECT * FROM users WHERE phone=?", (phone2,))["mac"] == mac2)
        portal.click("#rebindBack"); check("换绑返回不改变数据库 MAC", row("SELECT * FROM users WHERE phone=?", (phone2,))["mac"] == mac2)
        with portal.expect_response("**/portal/api/send_code") as second_code:
            portal.click("#sendBtn")
        expected_code = second_code.value.json().get("dev_code", "")
        portal.wait_for_function("code => document.querySelector('#code').value === code", arg=expected_code)
        portal.click("#smsLoginBtn"); visible(portal, "#rebindArea"); portal.click("#rebindBtn")
        db_check("确认换绑立即更新用户 MAC", "SELECT * FROM users WHERE phone=?", (phone2,), lambda r: r["mac"] == rebind_mac and r["mac_change_count"] >= 1)
        db_check("确认换绑立即创建新 MAC 会话", "SELECT * FROM sessions WHERE mac=? ORDER BY id DESC LIMIT 1", (rebind_mac,), lambda r: bool(r["token"]))
        check("短信登录后验证码已使用落库", row("SELECT * FROM sms_codes WHERE id=?", (sms_id,))["used"] == 1)

        check("专项流程无 JavaScript pageerror", not any("pageerror" in x for x in ERRORS), ERRORS)
        unexpected = [x for x in ERRORS if "console" in x and "ERR_FAILED" not in x]
        check("专项流程除主动断网外无控制台错误", not unexpected, unexpected)
        page.screenshot(path=str(OUT / "modal-db-settings.png"), full_page=True)
        browser.close(); request.dispose()

    report = {"total": len(RESULTS), "passed": sum(x["ok"] for x in RESULTS), "failed": sum(not x["ok"] for x in RESULTS), "database": DB, "console_messages": ERRORS, "results": RESULTS}
    (OUT / "modal-db-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "passed", "failed")}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        ERRORS.append(traceback.format_exc())
        report = {"total": len(RESULTS), "passed": sum(x["ok"] for x in RESULTS), "failed": sum(not x["ok"] for x in RESULTS) + 1, "error": str(exc), "console_messages": ERRORS, "results": RESULTS}
        (OUT / "modal-db-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        traceback.print_exc()
        raise SystemExit(1)
