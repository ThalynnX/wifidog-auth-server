# 接口文档（网关协议 / 用户端 / 管理端）

> 适用版本：v1.1.2（FastAPI 生产版）
> 服务端基地址：`$BASE`，例如 `https://portal.example.com`
> 所有示例中的域名、IP、端口均为占位符，请替换为自己的部署值。

## 约定

- 用户端与管理端接口：`Content-Type: application/json`，返回 JSON，成功 `ok=true`；失败
  `{"ok": false, "msg": "原因"}`。
- **网关协议接口是例外**：`ping` 返回纯文本 `Pong`，`auth` 必须返回纯文本 `Auth: 1` / `Auth: 0`
  （末尾带换行），**不能返回 JSON**，否则网关无法识别。
- 写操作有限流，触发时返回 `429` 或 `ok=false`，按 `msg` 提示用户稍后再试。
- 时间统一为服务器本地时间（建议 `Asia/Shanghai`）。

---

## 一、网关（路由器）协议 `/wifidog/`

### 1.1 参数要求

| 参数 | 说明 |
|---|---|
| `gw_id` | 网关唯一 ID，`^[A-Za-z0-9_.-]{1,64}$` |
| `gw_address` | 网关内网 IP，如 `192.168.6.1` |
| `gw_port` | 网关 auth 端口，如 `2060`（apfree 默认） |
| `mac` | 终端 MAC，大小写不敏感，服务端统一转小写 |
| `ip` | 终端内网 IP |
| `url` | 用户原始访问地址（登录后跳回用） |
| `token` | 登录成功后签发的上网令牌（24 小时） |

### 1.2 `GET /wifidog/ping`

网关心跳，每 `CheckInterval` 秒一次。

```
GET /wifidog/ping?gw_id=shop01&sys_uptime=12345&sys_memfree=20480&sys_load=0.12&wifidog_uptime=600
→ 200 text/plain: Pong
```

- 服务端记录网关在线状态、最后 IP、系统负载；
- `test` 模式会自动登记新网关；`production` 模式只更新已登记网关。

### 1.3 `GET /wifidog/login`

用户被网关 302 到这里，返回门户 HTML。

```
GET /wifidog/login?gw_id=shop01&gw_address=192.168.6.1&gw_port=2060&mac=AA:BB:CC:DD:EE:01&ip=192.168.6.100&url=http://example.com/
→ 200 text/html
```

- 命中 **IP 白名单 / MAC 白名单**：直接 `302` 回
  `http://{gw_address}:{gw_port}/wifidog/auth?token=wl...`，用户无感放行；
- 否则渲染登录页（手机号验证码 / 账号密码）。

### 1.4 `GET /wifidog/portal`、`GET /wifidog/message`

兼容旧网关的跳转页与提示页。`message` 参数 `message` 为要展示的文本，返回纯文本。

### 1.5 `GET|POST /wifidog/auth`

核心认证与计费接口，按 `stage` 分四种：

| stage | 用途 | 关键参数 | 返回 |
|---|---|---|---|
| `login` | 首次放行判定 | `token, ip, mac, gw_id, incoming, outgoing` | `Auth: 1` / `Auth: 0` |
| `counters` | 周期流量计费 | 同上，`incoming/outgoing` 为累计字节 | 同上 |
| `counters_v2` | apfree 批量上报（JSON） | 见 1.6 | `Auth: 1` / `Auth: 0` |
| `logout` | 用户主动下线 | `token, ip, mac, gw_id` | `Auth: 1` |

放行判定顺序（简化）：

1. 黑名单 MAC → 拒绝（并踢线）；
2. IP/MAC 白名单 → 放行，不扣套餐；
3. token 无效/过期 → 拒绝；
4. 用户被禁用 → 拒绝；
5. 无有效套餐、套餐过期、流量耗尽、设备数超限 → 拒绝；
6. 以上都通过 → 放行，并累计流量、刷新会话。

示例：

```bash
curl "https://portal.example.com/wifidog/auth?stage=login&gw_id=shop01&ip=192.168.6.100&mac=aa:bb:cc:dd:ee:01&token=<TOKEN>&incoming=0&outgoing=0"
# Auth: 1
```

> 服务端返回 0 后，网关会在下一个周期把该客户端踢下线；用户需重新登录或购买套餐。

### 1.6 `counters_v2`（apfree 扩展）

apfree-wifidog 在客户端较多时会把多个客户端的计数打包成 JSON 一次上报，服务端逐条判定并返回总体
`Auth: 1/0`。字段由网关生成，服务端兼容处理；自研网关可继续用 `stage=counters` 逐个上报。

### 1.7 `POST /wifidog/gwstats`

网关系统状态上报（可选）。

```
POST /wifidog/gwstats
Header: x-gw-key: <网关共享密钥，若后台配置了 secret 则必填>
Body: {"gw_id":"shop01","uptime":...,"memfree":...,"load":...,"clients":...}
```

### 1.8 `WS /ws/wifidogx`

apfree-wifidogx 长连接通道（对应 LuCI 的 `Persistent Connection Mode = ws/wss`，
`WebSocket URI path = /ws/wifidogx`）。

- 客户端发送文本 `Pong` 或 JSON 心跳；
- 服务端可下发指令（如强制重载、踢线）；
- Nginx 必须放行 Upgrade 头并设置长 `proxy_read_timeout`（模板为 3600s）。

---

## 二、用户端 API `/portal/`

### 2.1 发送验证码

```
POST /portal/api/send_code
{"phone": "<your-phone>"}
→ {"ok": true, "msg": "验证码已发送", "dev_code": "1989"}
```

- `dev_code` **仅测试模式**返回，正式模式为空；
- 限制：同一手机号每日 20 条，同一 IP 每小时 10 条。

### 2.2 验证码登录

```
POST /portal/api/login
{
  "phone": "<your-phone>",
  "code": "1989",
  "gw_id": "shop01",
  "gw_address": "192.168.6.1",
  "gw_port": "2060",
  "mac": "AA:BB:CC:DD:EE:01",
  "url": "http://example.com/"
}
→ {"ok": true,
   "redirect": "http://192.168.6.1:2060/wifidog/auth?token=<TOKEN>",
   "token": "<TOKEN>"}
```

- 验证码 5 分钟有效，**输错 5 次作废**；
- 登录限流：每 IP 10 分钟 30 次，每手机号 10 次；
- 客户端拿到 `redirect` 后引导浏览器访问，网关即完成放行。

### 2.3 账号密码登录

```
POST /portal/api/password_login
{"username": "user1", "password": "******", "gw_id": "shop01", "gw_address": "192.168.6.1", "gw_port": "2060", "mac": "..."}
→ 同 2.2
```

### 2.4 换绑设备

```
POST /portal/api/device/rebind
{"token": "<TOKEN>", "mac": "AA:BB:CC:DD:EE:02"}
→ {"ok": true, "msg": "换绑成功"}
```

- 有次数限制（防止账号共享），超限返回 `ok=false`。

### 2.5 自动绑定

```
POST /portal/api/device/auto
{"token": "<TOKEN>", "mac": "..."}
```

首次登录自动绑定当前设备，无需用户操作。

### 2.6 查询我的状态

```
GET /portal/me?token=<TOKEN>
→ {"ok": true, "phone": "138...", "nickname": "", "plan": "包月套餐",
   "expire_at": "2026-12-21 13:17", "traffic_left_mb": 97.3, "online_devices": 1}
```

### 2.7 套餐列表

```
GET /portal/plans        → HTML 自助购买页
GET /portal/api/live?token=<TOKEN>&mac=...
→ {"ok": true, "live": true, ...}     实时状态轮询（页面自动刷新用）
```

### 2.8 购买套餐

```
POST /portal/api/buy
{"plan_id": 3, "phone": "<your-phone>"}
→ {"ok": true, "order_no": "20261121...", "pay_url": "<支付链接或二维码内容>", "qr": false}
```

- 支付渠道由服务端按运行模式决定，客户端不能指定；
- `qr=true` 表示 `pay_url` 是二维码内容（微信 Native），需前端自行渲染二维码；
- 下单限流 10 笔/小时/IP。

### 2.9 模拟支付确认（仅测试模式）

```
POST /portal/api/pay/confirm
{"order_no": "2026..."}
→ {"ok": true, "msg": "支付成功，套餐已开通"}
```

正式模式返回 `403`。

### 2.10 支付回调（渠道 → 服务端）

| 渠道 | 端点 | 格式 | 处理 |
|---|---|---|---|
| 支付宝 | `POST /portal/api/pay/notify/alipay` | form | RSA2 验签 → 金额核对 → 幂等开通 → 返回 `success`/`fail` |
| 微信 | `POST /portal/api/pay/notify/wechat` | JSON | APIv3 AES-GCM 解密 → 金额核对 → 返回 `{"code":"SUCCESS"}` |

对应 `.env`：

```ini
WIFIDOG_ALIPAY_NOTIFY_URL=https://portal.example.com/portal/api/pay/notify/alipay
WIFIDOG_WECHAT_NOTIFY_URL=https://portal.example.com/portal/api/pay/notify/wechat
```

---

## 三、管理端 API `/admin/api/`

认证方式：登录后服务端下发 HttpOnly Cookie；程序化调用可带请求头
`x-admin-token: <token>`（从登录响应或 Cookie 中获取）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/api/login` | `{"username","password"}`；失败 5 次锁 IP 10 分钟 |
| POST | `/admin/api/logout` | 退出 |
| GET | `/admin/api/me` | 当前管理员 |
| GET | `/admin/api/stats` | 概览数据 |
| GET | `/admin/api/users?q=&page=&size=` | 用户列表 |
| POST | `/admin/api/users/{uid}/toggle` | 禁用/启用 |
| POST | `/admin/api/users/{uid}/subscribe` | 手动开通套餐 |
| GET/POST | `/admin/api/devices` | 网关列表 / 新增 |
| DELETE | `/admin/api/devices/{did}` | 删除网关 |
| GET/POST | `/admin/api/plans` | 套餐列表 / 新增或修改 |
| DELETE | `/admin/api/plans/{pid}` | 删除套餐 |
| GET | `/admin/api/orders?page=&size=&status=` | 订单列表 |
| POST | `/admin/api/orders/{oid}/refund` | 标记退款 |
| GET | `/admin/api/sessions` | 在线会话 |
| POST | `/admin/api/sessions/{sid}/kick` | 踢下线 |
| GET/POST | `/admin/api/blacklist` | MAC 黑名单 |
| DELETE | `/admin/api/blacklist/{bid}` | 移除黑名单 |
| GET/POST | `/admin/api/whitelist` | MAC 白名单 |
| DELETE | `/admin/api/whitelist/{wid}` | 移除白名单 |
| GET/POST | `/admin/api/ipwhitelist` | IP 白名单 |
| DELETE | `/admin/api/ipwhitelist/{wid}` | 移除 IP 白名单 |
| GET | `/admin/api/logs?page=&size=` | 认证日志 |
| GET/POST | `/admin/api/settings` | 系统设置 |
| POST | `/admin/api/admin/password` | `{"old_password","new_password"}` |

### 设置项（`POST /admin/api/settings`）

| 键 | 值 | 说明 |
|---|---|---|
| `run_mode` | `test` / `production` | 运行模式，保存即生效 |
| `free_trial_minutes` | 整数 | 新设备免费时长，0 关闭 |
| `default_device_limit` | 整数 | 默认同时在线设备数，0 不限 |
| `site_name` | 字符串 | 站点名称 |
| `site_notice` | 字符串 | 门户公告 |

> 短信/支付密钥不在数据库中，只能通过 `.env` 配置。

---

## 四、限制与常量速查

| 项 | 值 | 可调 |
|---|---|---|
| 上网 token 有效期 | 24 小时 | `WIFIDOG_PORTAL_TOKEN_EXPIRE` |
| 后台 token 有效期 | 12 小时 | `WIFIDOG_ADMIN_TOKEN_EXPIRE` |
| 验证码有效期 | 5 分钟 | `WIFIDOG_SMS_CODE_EXPIRE` |
| 验证码错误上限 | 5 次作废 | 代码常量 |
| 发码限制 | 20 条/号/日、10 条/IP/时 | `WIFIDOG_SMS_*_LIMIT` |
| 登录限制 | 30 次/IP/10 分钟、10 次/号/10 分钟 | 代码常量 |
| 管理员登录 | 失败 5 次锁 IP 10 分钟 | 代码常量 |
| 会话判离线 | 600 秒无上报 | `WIFIDOG_SESSION_IDLE_TIMEOUT` |
| 网关判离线 | 180 秒无 ping | `WIFIDOG_DEVICE_OFFLINE_TIMEOUT` |
| `gw_id` 格式 | `^[A-Za-z0-9_.-]{1,64}$` | — |
| 金额单位 | **分**（integer） | — |

---

## 五、错误码与排查

| 返回 | 含义 | 处理 |
|---|---|---|
| `{"ok":false,"msg":"验证码错误"}` | 验证码不匹配 | 重新获取，注意 5 次上限 |
| `{"ok":false,"msg":"验证码已过期"}` | 超过 5 分钟 | 重新发送 |
| `{"ok":false,"msg":"今日发送次数已达上限"}` | 超过 20 条/号 | 次日再试或后台调整 |
| `{"ok":false,"msg":"该账号已被禁用"}` | 用户状态为 0 | 后台启用 |
| `{"ok":false,"msg":"设备数量已达上限"}` | 超过套餐设备数 | 换绑或升级套餐 |
| `{"ok":false,"msg":"套餐已下架"}` | 前台不可见/已停用 | 后台重新上架 |
| `403` | 测试接口在生产模式被禁 | 属于正常保护 |
| `429` | 触发限流 | 降低频率，或调整限流参数 |
| `Auth: 0` | 认证被拒 | 看后台认证日志的 reason 字段 |
