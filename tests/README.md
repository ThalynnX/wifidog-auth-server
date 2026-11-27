# WiFidog UI 验收脚本

这三个脚本用于**隔离测试环境**，不要直接对生产服务器运行。脚本会创建、修改和删除测试数据，并临时修改测试管理员密码（结束前恢复为 `admin123`）。

## 1. 启动隔离服务

```bash
cd /path/to/wifidog-auth-server
rm -f /tmp/wifidog-ui-acceptance.db
PYTHONPATH="$PWD" \
WIFIDOG_DB_URL=sqlite:////tmp/wifidog-ui-acceptance.db \
WIFIDOG_SECRET=ui-acceptance-secret-2026 \
WIFIDOG_SMS_DEV_ECHO=1 \
uvicorn app.main:app --host 127.0.0.1 --port 8011
```

## 2. 接口与协议验收

依赖：`httpx`。

```bash
WIFIDOG_TEST_BASE=http://127.0.0.1:8011 python tests/acceptance_api.py
```

覆盖管理员鉴权、统计、用户、设备、套餐全参数、订单退款、在线会话、MAC/IP 黑白名单、日志分页、系统设置、修改密码，以及门户短信、登录、下单、模拟支付、自动识别、实时状态和网关 `Auth: 1/0`、counters 协议。

报告：`/tmp/wifidog-ui-artifacts/api-results.json`。

## 3. Chromium 逐按钮验收

依赖：`playwright`，Chromium 默认路径为 `/usr/local/bin/chromium`。

```bash
WIFIDOG_TEST_BASE=http://127.0.0.1:8011 python tests/acceptance_browser.py
```

覆盖桌面后台和手机门户的真实点击、弹窗确认、表格刷新、换绑、支付后会话恢复、网络失败、401 跳转、窄屏菜单以及 JavaScript / 控制台错误检查。网络失败和 401 是脚本主动模拟的负向测试，对应浏览器资源错误属于预期现象。

报告：`/tmp/wifidog-ui-artifacts/browser-results.json`；截图也保存在同一目录。

## 4. 弹窗、子窗口与数据库落盘专项验收

```bash
WIFIDOG_TEST_BASE=http://127.0.0.1:8011 \
WIFIDOG_TEST_DB=/tmp/wifidog-ui-acceptance.db \
python tests/acceptance_modals_db.py
```

逐一操作主要表单和确认框的“取消、关闭（X）、确定”，覆盖非法参数、重复点击和主动断网。每次保存后直接读取 SQLite，确认数据已提交；随后刷新页面或调用读取接口，核对页面、接口和数据库一致。覆盖设备、套餐、用户、订阅、退款、在线会话、MAC/IP 黑白名单、系统设置、管理员密码、支付和设备换绑。

报告：`/tmp/wifidog-ui-artifacts/modal-db-results.json`。

如需重新完整回归，先停止服务并删除隔离数据库，再启动服务，避免短信和下单限流影响重复测试。
