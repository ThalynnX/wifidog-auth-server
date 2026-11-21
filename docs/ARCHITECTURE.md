# 架构说明

本文说明系统由哪些部分组成、一次认证在代码里怎么走、数据怎么存、要扩展时改哪里。

---

## 1. 总体结构

```
终端（手机/电脑）
   │  ① 连接 WiFi，被网关拦截
   ▼
路由器 apfree-wifidogx
   │  ② 302 到 /wifidog/login
   │  ③ 周期 ping / auth counters / WS 心跳
   ▼
Nginx（80/443）
   │  TLS 终止、静态资源、WebSocket 升级
   ▼
Gunicorn（UvicornWorker，127.0.0.1:8000）
   ├── app/routers/wifidog.py   网关协议
   ├── app/routers/portal.py    用户门户 + 支付回调
   ├── app/routers/admin.py     管理后台
   ├── app/routers/ws.py        长连接
   └── 后台线程 _cleanup_worker 巡检（离线判定/到期清理）
   ▼
SQLAlchemy 2.x
   ├── SQLite（默认，WAL）
   └── MySQL / MariaDB（可选）
   ▼
外部：短信通道 / 支付宝 / 微信
```

**为什么这样分层**

| 层 | 职责 | 不放在应用里的原因 |
|---|---|---|
| 路由器 | 流量拦截、DHCP、放行/踢线 | 只有它能控制终端流量 |
| Nginx | TLS、静态、连接复用、限流兜底 | 比 Python 处理 TLS/静态更高效 |
| FastAPI | 业务逻辑、协议判定、计费 | 需要数据库与外部 API |
| Gunicorn | 进程管理、优雅重启 | 生产不用 uvicorn 单进程裸跑 |
| SQLite/MySQL | 持久化 | 无状态应用，方便扩容/备份 |

---

## 2. 一次认证的完整链路

```
1. 终端访问 http://任意站点
2. 网关 nftables 拦截 → 302 http://portal/login?gw_id&gw_address&gw_port&mac&ip&url
3. 服务端 /wifidog/login
     ├─ 命中 IP/MAC 白名单 → 302 回 http://gw:port/wifidog/auth?token=wl...
     └─ 否则渲染登录页
4. 用户提交手机号+验证码 → POST /portal/api/login
     ├─ 校验验证码（5 分钟、5 次上限）
     ├─ 查/建用户，校验状态、设备绑定
     ├─ 签 JWT（含 phone/mac/gw_id/过期时间）
     └─ 返回 redirect=http://gw:port/wifidog/auth?token=<JWT>
5. 浏览器访问 redirect → 网关请求 /wifidog/auth?stage=login
     ├─ 校验 token 签名/过期/IP/MAC
     ├─ 判定套餐（有效、未过期、流量未耗尽、设备数未超）
     ├─ 写 session，返回 Auth: 1
     └─ 任意一条不满足，返回 Auth: 0（并写 auth_logs）
6. 网关放行；每 CheckInterval 上报 stage=counters
     ├─ 累计 incoming/outgoing（字节）
     ├─ 校验套餐余量/到期时间
     └─ 返回 Auth: 1 继续 / Auth: 0 踢线
7. 用户主动下线或管理员踢线 → stage=logout / kick 接口 → 会话结束
```

关键代码位置：

| 逻辑 | 文件 |
|---|---|
| 认证放行判定 | `app/services_auth.py` |
| 网关协议入口 | `app/routers/wifidog.py` |
| 设备绑定/换绑/设备数 | `app/device_auth.py` |
| 限流 | `app/ratelimit.py` |
| 会话清理/离线判定 | `app/main.py::_cleanup_worker` |
| 实时统计/WS | `app/realtime.py`、`app/routers/ws.py` |

---

## 3. 数据模型

共 14 张表（`app/models.py`）：

| 表 | 作用 | 关键字段 |
|---|---|---|
| `users` | 用户 | `phone`、`username`、`password_hash`、`status`、`balance`、`mac`、`mac_change_count` |
| `devices` | 网关 | `gw_id`（唯一）、`name`、`location`、`secret`、`online`、`last_seen_at`、`sys_load` |
| `plans` | 套餐 | `type`(free/time/flow/period)、`price`(分)、`duration_minutes`、`traffic_mb`、`valid_days`、`speed_limit_*`、`device_limit` |
| `orders` | 订单 | `order_no`、`user_id`、`plan_id`、`amount`、`status`(0待付/1已付/2关闭/3退款)、`pay_type`、`trade_no` |
| `subscriptions` | 套餐订阅 | 用户+套餐、生效/到期、剩余流量、状态 |
| `sessions` | 在线会话 | `user_id`、`mac`、`ip`、`gw_id`、`token`、`started_at`、`last_seen_at`、`incoming/outgoing` |
| `sms_codes` | 验证码 | `phone`、`code`、`expires_at`、`attempts`、`used` |
| `auth_logs` | 认证日志 | `user_id`、`mac`、`ip`、`gw_id`、`action`、`result`、`reason` |
| `admins` | 管理员 | `username`、`password_hash` |
| `settings` | 系统设置 | `key`、`value`（`run_mode`、`site_name`、`free_trial_minutes` 等） |
| `mac_whitelist` | 免认证 MAC | `mac`、`remark` |
| `mac_blacklist` | 强制断网 MAC | `mac`、`reason` |
| `ip_whitelist` | 免认证 IP | `ip` / 网段、`remark` |

设计要点：

- **金额一律用“分”存整数**，避免浮点误差；
- MAC 统一小写存储，比较时归一化；
- `sessions` 是“当前在线”的事实来源，`subscriptions` 是“买了什么”的事实来源；
- 所有表带 `created_at`，订单/会话另有业务时间字段；
- 启动时 `init_db()` 建表并做“缺列则补”的轻量迁移，升级不需要手工 SQL。

---

## 4. 放行判定规则（优先级从高到低）

```
1. MAC 在黑名单            → 拒绝 + 踢线（无视套餐与白名单）
2. IP/MAC 在白名单         → 放行，不扣套餐流量
3. token 缺失/签名错/过期   → 拒绝
4. 用户被禁用(status=0)    → 拒绝
5. 无有效订阅              → 拒绝（除非有未用完的免费体验）
6. 订阅已过期              → 拒绝
7. 流量已耗尽              → 拒绝
8. 在线设备数超限          → 拒绝（新设备）
9. 网关被禁用              → 拒绝
10. 全部通过               → 放行，累计流量、刷新会话
```

每次判定结果都会写 `auth_logs`，带 `reason`，后台可直接看到“为什么被拒”。

---

## 5. 运行模式

| 行为 | `test` | `production` |
|---|---|---|
| 短信 | mock，接口回显 `dev_code` | 真实通道（阿里云/HTTP） |
| 支付 | `/portal/pay/mock` 模拟页 | 支付宝/微信真实下单与回调 |
| `pay/confirm` | 可用 | 403 |
| `ping` 新网关 | 自动登记 | 仅更新已登记网关 |
| 适用 | 联调、演示、培训 | 线上收费 |

优先级：**后台 `settings.run_mode` > `WIFIDOG_RUN_MODE` > 默认 `test`**。

切换是**即时生效**的：`settings_store` 每次读设置表，不需要重启（改 `.env` 才需要重启）。

---

## 6. 实时与长连接

- `realtime.py` 维护内存中的在线状态与广播；
- 门户页面通过 `GET /portal/api/live` 轮询刷新（不需要长连接，兼容性最好）；
- apfree-wifidogx 走 `WS /ws/wifidogx`：心跳保活 + 服务端下发指令；
- 多 worker 时，内存中的实时状态**不跨进程共享**，后台页面以数据库 `sessions` 为准；
- 需要跨进程/跨机共享限流与实时态时，接 Redis（可选依赖）。

---

## 7. 安全设计

| 面 | 措施 |
|---|---|
| 密码 | `passlib + bcrypt` 哈希，永不存明文 |
| 令牌 | JWT（HS256），含 `exp`、`sub`、`mac`、`gw_id`；密钥 `WIFIDOG_SECRET` |
| 管理端 | HttpOnly Cookie + 可选 `x-admin-token`；失败 5 次锁 IP |
| 用户端 | 验证码一次有效 + 5 次错误作废；发码/登录/下单多维限流 |
| 支付 | 支付宝 RSA2 验签、微信 APIv3 AES-GCM 解密；金额与订单双向核对；回调幂等 |
| 密钥 | 短信/支付密钥只走 `.env`，不进数据库、不进仓库 |
| 网关 | 可选 `secret` + `x-gw-key` 校验 `gwstats`；`gw_id` 白名单式登记 |
| 部署 | Gunicorn 只听 127.0.0.1，Nginx 终止 TLS；systemd 沙箱选项 |

**已知边界**：本项目不提供 RADIUS/EAP，走的是 captive portal 应用层认证；如需 802.1X 企业级认证，
需另接 FreeRADIUS（旧版 Node 实现曾用此方案，已归档在 `node-v3.0.0`）。

---

## 8. 扩展指南

| 需求 | 改哪里 |
|---|---|
| 新增支付渠道 | `app/payment.py` 加渠道实现；`portal.py` 加回调路由；`.env` 加配置 |
| 新增短信商 | `app/sms.py` 加 provider 分支 |
| 新增套餐类型 | `models.Plan.type` 加枚举；`services_auth.py` 判定逻辑；后台表单 |
| 增加报表 | `admin.py` 加只读接口；或直接查库做 BI |
| 多机部署 | 切 MySQL + Redis；Nginx 上做 upstream 负载均衡；会话状态以 DB 为准 |
| 自定义门户样式 | `app/templates/portal/*.html` + `app/static/` |
| 对接自有 App | 按 `docs/API.md` 调 `/portal/api/*`，拿 token 后跳网关 auth |

---

## 9. 已知限制

- SQLite 单写者：高并发写入（大量 counters 同时上报）时建议 MySQL；
- 内存限流按进程隔离：`WIFIDOG_WORKERS>1` 时实际限流阈值会放大，建议 1~2 或接 Redis；
- 原版 wifidog 不支持 HTTPS/WS，需服务端额外暴露 80 端口路径；
- `counters_v2` 仅 apfree-wifidog 支持，标准 wifidog 用 `counters`；
- 本项目不做上网行为审计/内容过滤，如需合规审计请在网关侧另接。
