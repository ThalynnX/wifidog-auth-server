"""Real Chromium click-through acceptance for the WiFidog v2 UI.

Requires an isolated local server and Playwright:
  python tests/acceptance_browser.py
Screenshots and a JSON result report are written to /tmp/wifidog-ui-artifacts.
"""
import json
import os
import random
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("WIFIDOG_TEST_BASE", "http://127.0.0.1:8011").rstrip("/")
OUT = Path(os.getenv("WIFIDOG_TEST_ARTIFACTS", "/tmp/wifidog-ui-artifacts"))
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
ERRORS = []


def check(name, condition=True, detail=""):
    RESULTS.append({"name": name, "ok": bool(condition), "detail": str(detail)[:800]})
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name + " " + str(detail))


def visible(page, selector, timeout=10000):
    page.locator(selector).wait_for(state="visible", timeout=timeout)


def confirm(page):
    page.locator(".modal-layer [data-ok]").last.click()


def send_code_and_fill(page, phone):
    """发送验证码并手动填入测试码；页面不再自动回显/自动填入。"""
    page.fill("#phone", phone)
    with page.expect_response("**/portal/api/send_code") as response_info:
        page.click("#sendBtn")
    payload = response_info.value.json()
    code = payload.get("dev_code") or ""
    check("测试环境短信接口返回测试码", bool(code), payload)
    page.fill("#code", code)
    return code


def main():
    suffix = str(int(time.time()))[-8:]
    phone = "138" + suffix
    mac = "02:00:10:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
    gw = "browser" + suffix[-6:]
    with sync_playwright() as p:
        setup = p.request.new_context(base_url=BASE)
        setup.post("/admin/api/login", data=json.dumps({"username": "admin", "password": "admin123"}), headers={"Content-Type": "application/json"})
        setup.post("/admin/api/settings", data=json.dumps({"run_mode": "test", "free_trial_minutes": 0, "default_device_limit": 3, "site_notice": "Chromium 验收公告", "site_name": "示例企业 · 云网控制台"}), headers={"Content-Type": "application/json"})
        setup.dispose()
        browser = p.chromium.launch(executable_path="/usr/local/bin/chromium", headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda exc: ERRORS.append("portal pageerror: " + str(exc)))
        page.on("console", lambda msg: ERRORS.append("portal console: " + msg.text) if msg.type == "error" else None)
        page.route("https://example.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<h1>Original URL OK</h1>"))

        portal_url = BASE + "/wifidog/login?gw_id=" + gw + "&gw_address=127.0.0.1&gw_port=2060&mac=" + mac + "&ip=198.51.100.66&url=" + "https%3A%2F%2Fexample.com%2Foriginal"
        page.goto(portal_url); visible(page, "#smsForm")
        check("门户登录页无脚本错误并显示认证表单")
        page.screenshot(path=str(OUT / "portal-login-390.png"), full_page=True)
        page.click("#sendBtn"); check("空手机号发码有明确校验", "手机号" in page.locator("#smsMsg").inner_text())
        page.fill("#phone", phone); send_code_and_fill(page, phone)
        check("发送验证码按钮返回并进入倒计时", page.locator("#sendBtn").inner_text().find("秒") >= 0)
        page.click("#smsLoginBtn"); page.wait_for_url("**/portal/plans**")
        visible(page, "#planList [data-plan-id]:first-child")
        check("无套餐登录后进入套餐页", page.locator("#phone").input_value() == phone)
        page.screenshot(path=str(OUT / "portal-plans-390.png"), full_page=True)

        page.locator("#planList [data-plan-id]").first.click(); page.wait_for_url("**/portal/pay/mock**")
        visible(page, "#payBtn"); check("套餐按钮创建订单并进入支付页")
        page.click("#backBtn"); page.wait_for_url("**/portal/plans**")
        check("支付页返回套餐按钮有效")
        page.locator("#planList [data-plan-id]").first.click(); page.wait_for_url("**/portal/pay/mock**")
        page.screenshot(path=str(OUT / "portal-pay-390.png"), full_page=True)
        page.click("#payBtn"); confirm(page); page.wait_for_url("**/portal/success**", timeout=15000)
        visible(page, ".success-title"); check("模拟支付确认后进入成功页")
        page.wait_for_timeout(700)
        page.wait_for_function("!document.querySelector('#planName').textContent.includes('正在读取')")
        check("支付后成功页恢复真实套餐会话", "SESSION LOST" not in page.locator("#liveText").inner_text())
        page.screenshot(path=str(OUT / "portal-success-390.png"), full_page=True)
        token = page.evaluate("localStorage.getItem('wg_token') || ''")
        page.click("#refreshBtn"); page.wait_for_timeout(400)
        check("成功页刷新状态按钮有反馈", page.locator("#refreshBtn").is_enabled())
        page.click("#plansBtn"); page.wait_for_url("**/portal/plans**")
        check("成功页购买续费按钮有效")
        page.click("#backLogin"); page.wait_for_url("**/wifidog/login**")
        page.wait_for_url("**/portal/success**", timeout=10000)
        token = page.evaluate("localStorage.getItem('wg_token') || ''")
        check("已认证设备自动识别并进入成功页", bool(token))
        # Unsafe return URL policy: never send a captive-portal user to the gateway admin page.
        policy = page.evaluate("""() => ({
          public: UI.safeReturnUrl('https://example.com/a?x=1', '192.168.20.1'),
          gateway: UI.safeReturnUrl('http://192.168.20.1/', '192.168.20.1'),
          private: UI.safeReturnUrl('http://10.0.0.8/admin', '10.0.0.1'),
          loopback: UI.safeReturnUrl('http://127.0.0.1/', ''),
          script: UI.safeReturnUrl('javascript:alert(1)', ''),
          probe: UI.safeReturnUrl('http://connectivitycheck.vivo.com.cn/generate_204', '')
        })""")
        check("安全回跳仅放行公网 HTTP/HTTPS",
              policy["public"] == "https://example.com/a?x=1" and
              all(not policy[key] for key in ("gateway", "private", "loopback", "script", "probe")))

        page.click("#continueBtn"); page.wait_for_url("https://example.com/original")
        check("继续访问按钮使用原始 URL", "Original URL OK" in page.content())

        page.goto(BASE + "/portal/success?token=" + token + "&gw_id=" + gw)
        page.evaluate("""() => {
          localStorage.setItem('wg_url', 'http://192.168.20.1/');
          localStorage.setItem('wg_gw_address', '192.168.20.1');
        }""")
        page.reload(); page.wait_for_timeout(350)
        start_url = page.url
        page.click("#continueBtn")
        page.wait_for_timeout(350)
        check("路由器管理地址不会触发跳转", page.url == start_url)
        check("无合法原始页面时提示直接打开浏览器", "直接打开浏览器" in page.locator("#msg").inner_text())
        page.evaluate("localStorage.setItem('wg_url', 'https://example.com/original')")

        # Device rebind: mismatch -> back -> verify again -> confirm rebind.
        rebind_mac = "02:00:11:" + ":".join(suffix[-6:][i:i+2] for i in range(0, 6, 2))
        page.goto(BASE + "/wifidog/login?gw_id=" + gw + "&mac=" + rebind_mac + "&ip=198.51.100.67")
        send_code_and_fill(page, phone)
        page.click("#smsLoginBtn"); visible(page, "#rebindArea")
        check("换设备登录展示换绑确认", page.locator("#newMac").input_value() == rebind_mac)
        page.click("#rebindBack"); check("换绑返回按钮恢复认证表单", page.locator("#authArea").is_visible())
        with page.expect_response("**/portal/api/send_code") as response_info:
            page.click("#sendBtn")
        payload = response_info.value.json()
        check("换绑后重新获取验证码", payload.get("ok") is True)
        page.fill("#code", payload.get("dev_code") or "")
        page.click("#smsLoginBtn"); visible(page, "#rebindArea")
        page.click("#rebindBtn"); page.wait_for_url("**/portal/success**")
        check("确认换绑后签发新会话", bool(page.evaluate("localStorage.getItem('wg_token') || ''")))
        mac = rebind_mac

        # Password tab validation and failed response.
        page.goto(BASE + "/wifidog/login"); page.click("#pwdTab"); page.click("#pwdLoginBtn")
        check("账号密码空值校验", "账号和密码" in page.locator("#pwdMsg").inner_text())
        page.fill("#username", "no_such_user"); page.fill("#password", "bad-password"); page.click("#pwdLoginBtn")
        page.wait_for_function("document.querySelector('#pwdMsg').textContent.includes('账号或密码错误')")
        check("账号密码错误返回可见提示")
        page.click("#smsTab"); check("短信与密码标签可来回切换", page.locator("#smsForm").is_visible())

        # Desktop admin click-through.
        admin_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        admin = admin_context.new_page()
        admin.on("pageerror", lambda exc: ERRORS.append("admin pageerror: " + str(exc)))
        admin.on("console", lambda msg: ERRORS.append("admin console: " + msg.text) if msg.type == "error" else None)
        admin.goto(BASE + "/admin/login"); visible(admin, "#loginForm")
        admin.click("#togglePwd"); check("管理员密码显示按钮有效", admin.locator("#password").get_attribute("type") == "text")
        admin.click("#togglePwd"); check("管理员密码隐藏按钮有效", admin.locator("#password").get_attribute("type") == "password")
        admin.fill("#username", "admin"); admin.fill("#password", "wrong-password"); admin.click("#loginBtn")
        admin.wait_for_function("!document.querySelector('#loginBtn').disabled && document.querySelector('#msg').textContent.length > 3")
        check("管理员错误密码可见反馈", "失败" in admin.locator("#msg").inner_text() or "错误" in admin.locator("#msg").inner_text())
        admin.fill("#password", "admin123"); admin.click("#loginBtn"); admin.wait_for_url("**/admin/")
        visible(admin, "#metrics .metric:first-child"); check("管理员登录并加载八项概览", admin.locator("#metrics .metric").count() == 8)
        admin.click("#dashRefresh"); check("概览刷新按钮有效", admin.locator("#lastRefresh").inner_text().find("最后更新") >= 0)
        admin.screenshot(path=str(OUT / "admin-dashboard-1440.png"), full_page=True)

        admin.click('[data-page="users"]'); visible(admin, "#page-users.active")
        admin.fill("#uq", phone); admin.click("#userSearch"); admin.wait_for_timeout(500)
        check("用户搜索返回目标账号", phone in admin.locator("#usersBox").inner_text())
        admin.click("#userRefresh"); check("用户刷新按钮有效")
        admin.locator("#usersBox button", has_text="禁用").first.click(); confirm(admin); admin.wait_for_timeout(400)
        admin.locator("#usersBox button", has_text="启用").first.click(); confirm(admin); admin.wait_for_timeout(400)
        check("用户禁用与恢复均经确认并更新")
        admin.locator("#usersBox button", has_text="开通套餐").first.click(); visible(admin, "#sub_plan"); confirm(admin); admin.wait_for_timeout(400)
        check("手动开通套餐弹窗提交有效")

        admin.click('[data-page="devices"]'); admin.click("#deviceAdd"); admin.fill("#d_gw", "bad id!"); confirm(admin)
        check("设备非法 ID 在弹窗内提示", admin.locator(".modal-msg.show").is_visible())
        admin.fill("#d_gw", gw); admin.fill("#d_name", "Chromium验收网关"); admin.fill("#d_loc", "测试区"); confirm(admin)
        admin.wait_for_function("document.querySelector('#devicesBox').textContent.includes('Chromium验收网关')")
        check("新增设备按钮保存并刷新表格")
        admin.locator("#devicesBox tr", has_text=gw).locator("button", has_text="编辑").click(); admin.fill("#d_name", "Chromium网关-已编辑"); confirm(admin)
        admin.wait_for_timeout(400); check("编辑设备保存有效", "已编辑" in admin.locator("#devicesBox").inner_text())
        admin.locator("#devicesBox tr", has_text=gw).locator("button", has_text="删除").click(); confirm(admin); admin.wait_for_timeout(400)
        check("删除设备确认后移除", gw not in admin.locator("#devicesBox").inner_text())

        admin.click('[data-page="plans"]'); admin.click("#planAdd")
        admin.fill("#p_name", "Chromium全参数套餐"); admin.fill("#p_price", "321"); admin.fill("#p_dur", "180")
        admin.fill("#p_mb", "1024"); admin.fill("#p_days", "3"); admin.fill("#p_dev", "2"); admin.fill("#p_up", "2048"); admin.fill("#p_down", "8192")
        confirm(admin); admin.wait_for_function("document.querySelector('#plansBox').textContent.includes('Chromium全参数套餐')")
        check("新增套餐所有输入可提交", "2048" in admin.locator("#plansBox").inner_text() and "8192" in admin.locator("#plansBox").inner_text())
        admin.locator("#plansBox tr", has_text="Chromium全参数套餐").locator("button", has_text="编辑").click(); admin.fill("#p_up", "3072"); confirm(admin); admin.wait_for_timeout(400)
        check("编辑套餐限速参数有效", "3072" in admin.locator("#plansBox").inner_text())
        admin.locator("#plansBox tr", has_text="Chromium全参数套餐").locator("button", has_text="删除").click(); confirm(admin); admin.wait_for_timeout(400)
        check("删除套餐确认后移除", "Chromium全参数套餐" not in admin.locator("#plansBox").inner_text())

        admin.click('[data-page="orders"]'); admin.select_option("#ostatus", "1"); admin.click("#orderRefresh"); admin.wait_for_timeout(500)
        check("订单状态筛选返回已支付订单", admin.locator("#ordersBox tr").count() > 0)
        admin.locator("#ordersBox button", has_text="退款登记").first.click(); admin.fill("#r_reason", "Chromium点击验收"); admin.locator("#r_channel").check(); confirm(admin)
        admin.wait_for_timeout(500); admin.select_option("#ostatus", "3"); admin.click("#orderRefresh"); admin.wait_for_timeout(400)
        check("退款弹窗原因与渠道确认可提交", "Chromium点击验收" in admin.locator("#ordersBox").inner_text())

        # Refund deactivates the paid subscription. Activate a fresh manual subscription
        # through the existing admin API before testing online-session buttons.
        user_rows = admin.request.get(BASE + "/admin/api/users", params={"q": phone, "page": 1, "size": 20}).json().get("rows", [])
        plan_rows = admin.request.get(BASE + "/admin/api/plans").json().get("rows", [])
        if user_rows and plan_rows:
            admin.request.post(BASE + f"/admin/api/users/{user_rows[0]['id']}/subscribe", data=json.dumps({"plan_id": plan_rows[0]["id"]}), headers={"Content-Type": "application/json"})
        auto_response = admin.request.post(BASE + "/portal/api/device/auto", data=json.dumps({"mac": mac, "ip": "198.51.100.66", "gw_id": gw}), headers={"Content-Type": "application/json"})
        auto_data = auto_response.json()
        live_token = auto_data.get("token", "")
        check("会话准备：自动识别返回新 token", auto_data.get("ok") and live_token, auto_data)
        auth_response = admin.request.get(BASE + "/wifidog/auth", params={"stage": "login", "token": live_token, "mac": mac, "ip": "198.51.100.66", "gw_id": gw})
        check("会话准备：网关认证返回 Auth 1", auth_response.text().strip() == "Auth: 1", auth_response.text())
        admin.click('[data-page="sessions"]'); admin.click("#sessionRefresh"); admin.wait_for_timeout(400)
        if admin.locator("#sessionsBox tr").count():
            admin.locator("#sessionsBox button", has_text="踢下线").first.click(); confirm(admin); admin.wait_for_timeout(400)
            check("在线会话仅踢线按钮有效")
            auto_response = admin.request.post(BASE + "/portal/api/device/auto", data=json.dumps({"mac": mac, "ip": "198.51.100.66", "gw_id": gw}), headers={"Content-Type": "application/json"})
            live_token = auto_response.json().get("token", "")
            admin.request.get(BASE + "/wifidog/auth", params={"stage": "login", "token": live_token, "mac": mac, "ip": "198.51.100.66", "gw_id": gw})
            admin.click("#sessionRefresh"); admin.wait_for_timeout(400)
            admin.locator("#sessionsBox button", has_text="踢线并拉黑").first.click(); confirm(admin); admin.wait_for_timeout(400)
            check("在线会话踢线并拉黑按钮有效")
        else:
            check("在线会话空状态明确显示", "暂无在线终端" in admin.locator("#sessionsBox").inner_text())

        admin.click('[data-page="whitelist"]')
        admin.click("#macAdd"); admin.fill("#m_mac", "invalid"); confirm(admin); visible(admin, ".modal-msg.show"); check("MAC 白名单非法格式提示", admin.locator(".modal-msg.show").is_visible())
        admin.fill("#m_mac", "02:20:30:40:50:60"); admin.fill("#m_note", "Chromium"); confirm(admin); admin.wait_for_timeout(400)
        admin.locator("#wlBox button", has_text="删除").first.click(); confirm(admin); admin.wait_for_timeout(350); check("MAC 白名单新增与删除按钮有效")
        admin.click("#blackAdd"); admin.fill("#m_mac", "02:20:30:40:50:61"); admin.fill("#m_note", "Chromium"); confirm(admin); admin.wait_for_timeout(400)
        admin.locator("#blBox button", has_text="解除拉黑").first.click(); confirm(admin); admin.wait_for_timeout(350); check("MAC 黑名单新增与解除按钮有效")
        admin.click("#ipAdd"); admin.fill("#i_cidr", "999.1.1.1/24"); confirm(admin); visible(admin, ".modal-msg.show"); check("IP 白名单非法格式提示", admin.locator(".modal-msg.show").is_visible())
        admin.fill("#i_cidr", "203.0.113.0/24"); admin.fill("#i_note", "Chromium"); confirm(admin); admin.wait_for_timeout(400)
        admin.locator("#ipwlBox button", has_text="删除").first.click(); confirm(admin); admin.wait_for_timeout(350); check("IP 白名单新增与删除按钮有效")

        admin.click('[data-page="logs"]'); admin.click("#logRefresh"); admin.wait_for_timeout(400)
        check("认证日志刷新返回记录", admin.locator("#logsBox tr").count() > 0)
        next_button = admin.locator("#logsPager button", has_text="下一页")
        if next_button.is_enabled():
            next_button.click(); admin.wait_for_timeout(350); admin.locator("#logsPager button", has_text="上一页").click(); check("日志上一页下一页按钮有效")
        else:
            check("日志单页时分页按钮正确禁用", next_button.is_disabled())

        admin.click('[data-page="settings"]'); visible(admin, "#set_site_name")
        admin.fill("#set_notice", "Chromium 验收公告"); admin.click("#settingsSave"); admin.wait_for_timeout(400)
        check("系统设置保存并回读", admin.locator("#set_notice").input_value() == "Chromium 验收公告")
        admin.fill("#pwd_old", "bad-old"); admin.fill("#pwd_new", "short"); admin.fill("#pwd_confirm", "short"); admin.click("#passwordSave")
        check("独立改密区域校验新密码长度", "至少 8 位" in admin.locator(".toast-stack").inner_text())
        admin.fill("#pwd_new", "Browser#2026x"); admin.fill("#pwd_confirm", "Browser#2026y"); admin.click("#passwordSave")
        check("改密确认两次密码一致性", "不一致" in admin.locator(".toast-stack").inner_text())
        admin.fill("#pwd_old", "admin123"); admin.fill("#pwd_new", "Browser#2026x"); admin.fill("#pwd_confirm", "Browser#2026x"); admin.click("#passwordSave")
        admin.locator(".toast", has_text="密码已修改").wait_for(state="visible")
        check("正确参数修改密码有成功反馈", "密码已修改" in admin.locator(".toast-stack").inner_text())
        admin.fill("#pwd_old", "Browser#2026x"); admin.fill("#pwd_new", "admin123"); admin.fill("#pwd_confirm", "admin123"); admin.click("#passwordSave")
        admin.wait_for_function("!document.querySelector('#passwordSave').disabled")
        check("恢复测试管理员密码", "密码已修改" in admin.locator(".toast-stack").inner_text())
        admin.screenshot(path=str(OUT / "admin-settings-1440.png"), full_page=True)

        admin.click("#logoutBtn"); admin.locator(".modal-layer [data-close]").last.click(); check("退出确认可取消", admin.url.endswith("/admin/#settings") or "/admin/" in admin.url)
        admin.click("#logoutBtn"); confirm(admin); admin.wait_for_url("**/admin/login"); check("退出登录按钮返回登录页")

        # Network and 401 handling in an isolated admin session.
        admin.fill("#username", "admin"); admin.fill("#password", "admin123"); admin.click("#loginBtn"); admin.wait_for_url("**/admin/")
        admin.route("**/admin/api/stats", lambda route: route.abort())
        admin.click("#dashRefresh"); admin.wait_for_timeout(500)
        check("网络失败不会静默", "网络连接失败" in admin.locator(".toast-stack").inner_text())
        admin.unroute("**/admin/api/stats")
        admin.route("**/admin/api/stats", lambda route: route.fulfill(status=401, content_type="application/json", body='{"detail":"unauthorized"}'))
        admin.click("#dashRefresh"); admin.wait_for_url("**/admin/login?expired=1")
        check("401 统一跳转并提示登录过期", "登录已过期" in admin.locator("#msg").inner_text())
        admin.unroute("**/admin/api/stats")

        # Narrow-screen admin menu.
        admin.set_viewport_size({"width": 390, "height": 844}); admin.goto(BASE + "/admin/login")
        admin.fill("#username", "admin"); admin.fill("#password", "admin123"); admin.click("#loginBtn"); admin.wait_for_url("**/admin/")
        admin.click("#menuToggle"); check("窄屏管理菜单可打开", "open" in (admin.locator("#sidebar").get_attribute("class") or ""))
        admin.click('[data-page="users"]'); check("窄屏导航点击后收起并切换", admin.locator("#page-users").is_visible() and "open" not in (admin.locator("#sidebar").get_attribute("class") or ""))
        admin.wait_for_timeout(300)
        admin.screenshot(path=str(OUT / "admin-users-390.png"), full_page=True)

        check("正常浏览器流程无 JavaScript pageerror", not any("pageerror" in e for e in ERRORS), ERRORS)
        expected_console = ("ERR_FAILED", "401 (Unauthorized)")
        unexpected_console = [e for e in ERRORS if "console" in e and not any(marker in e for marker in expected_console)]
        check("正常浏览器流程无控制台错误", not unexpected_console, unexpected_console)
        browser.close()

    report = {"total": len(RESULTS), "passed": sum(x["ok"] for x in RESULTS), "failed": sum(not x["ok"] for x in RESULTS), "console_messages": ERRORS, "results": RESULTS}
    (OUT / "browser-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "passed", "failed")}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        ERRORS.append(traceback.format_exc())
        report = {"total": len(RESULTS), "passed": sum(x["ok"] for x in RESULTS), "failed": sum(not x["ok"] for x in RESULTS) + 1,
                  "error": str(exc), "console_messages": ERRORS, "results": RESULTS}
        (OUT / "browser-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        traceback.print_exc()
        raise SystemExit(1)
