# 数据库结构与部署说明

本项目的数据库结构由 `app/models.py` 定义，应用启动时自动建表并做幂等字段迁移，**不需要提交或分发生产数据库文件**。

## 为什么不提供 `data/wifidog.db`

生产库包含用户手机号、MAC、订单、支付记录、管理员配置等真实数据，属于敏感数据，不能进入公开仓库。仓库只提供：

- `app/models.py`：结构源头，15 张表的 SQLAlchemy 定义
- `docs/schema.sql`：从生产库导出的纯 SQLite DDL，只有建表/索引，没有任何 `INSERT` 数据
- `app/database.py`：启动建表与轻量迁移逻辑

`data/*.db`、日志和 `.env` 都已在 `.gitignore` 中忽略。

## 正常部署：自动建表

应用启动时会执行：

1. `Base.metadata.create_all()`：创建缺失的表
2. `_migrate()`：给已有库补齐后加字段与索引
3. `_seed_defaults()`：创建默认管理员 `admin/admin123`，写入 4 个示例套餐

因此新服务器只需要部署代码、配置 `.env`、启动服务，不需要手工执行 SQL。首次登录后必须立即修改默认管理员密码。

## 手工初始化 SQLite（可选）

仅适用于空库；已有库不要重复执行，否则会因表已存在而报错。

```bash
mkdir -p data
sqlite3 data/wifidog.db < docs/schema.sql
```

随后启动应用，应用会继续补字段迁移并写入默认管理员与示例套餐。

## MySQL / MariaDB

把 `WIFIDOG_DB_URL` 改为 MySQL 连接串后，应用同样会自动建表。`docs/schema.sql` 是 SQLite 方言，不能直接用于 MySQL；MySQL 以 `app/models.py` 自动建表为准。

## 15 张表

| 表 | 用途 | 关键字段 |
|---|---|---|
| `users` | 用户、手机号/账号登录、支付宝绑定、设备 MAC | `phone`、`username`、`password_hash`、`alipay_user_id`、`mac`、`balance` |
| `devices` | 网关/路由器设备 | `gw_id`、`secret`、`online`、`last_seen_at`、`last_ip` |
| `plans` | 套餐定义 | `price`（分）、`duration_minutes`、`traffic_mb`、`valid_days`、`device_limit` |
| `orders` | 支付订单 | `order_no`、`plan_id`、`amount`、`status`、`trade_no`、`refund_no` |
| `subscriptions` | 用户已购套餐/订阅 | `user_id`、`plan_id`、`start_at`、`expire_at`、`traffic_total_mb`、`status` |
| `sessions` | 在线认证会话 | `token`、`user_id`、`gw_id`、`mac`、`last_update_at`、`revoked` |
| `sms_codes` | 短信验证码 | `phone`、`code`、`purpose`、`expire_at`、`attempts` |
| `auth_logs` | 认证审计日志 | `user_id`、`gw_id`、`mac`、`action`、`result`、`reason` |
| `admins` | 管理员账号 | `username`、`password_hash`、`role`、`status` |
| `settings` | 系统配置 KV | `key`、`value`、`updated_at` |
| `mac_whitelist` | MAC 免认证白名单 | `mac`、`note`、`status` |
| `ip_whitelist` | IP/CIDR 免认证白名单 | `cidr`、`note`、`status` |
| `mac_blacklist` | MAC 强制断网黑名单 | `mac`、`note` |
| `alipay_oauth_states` | 支付宝 OAuth 一次性 state | `state`、`payload`、`used`、`expire_at` |
| `payment_events` | 支付回调/查单/退款幂等审计 | `channel`、`event_key`、`order_no`、`payload_hash`、`status` |

## 升级与备份

升级前先备份数据库：

```bash
sqlite3 data/wifidog.db ".backup 'backup-$(date +%F).db'"
```

启动时会自动补字段，但不会删除旧字段，也不会自动回滚。结构变更以 `app/models.py` 和 `app/database.py` 的 `_migrate()` 为准；`docs/schema.sql` 是便于审计和空库初始化的静态快照。
