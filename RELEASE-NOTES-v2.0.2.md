# WiFiDog Auth Server v2.0.2

发布日期：2026-11-22

本版本把实时数据一致性、多 worker 支持和可部署的数据库结构整理进源码包。

## 主要更新

- 在线判定统一为 `status=1` 且网关最近 120 秒有上报；后台统计、设备在线和门户实时状态使用同一口径。
- 会话增加 `revoked` 标记：踢线、退款、禁用后旧 token 不会在下次 `gwstats` 时复活。
- `/wifidog/gwstats` 逐终端校验黑名单、套餐、token、用户与设备数。
- 新增 Redis Pub/Sub WebSocket 总线：跨 worker 广播命令、回执和网关连接计数。
- Gunicorn 默认 2 worker；绑定地址、worker 数、日志目录改为环境变量。
- 后台 9 个页面与门户套餐页、成功页、模拟支付页按 3–5 秒同步；动态页面和 API 返回 `no-store`。
- 网关统计密钥改为只从 `WIFIDOG_GWSTATS_KEY` 读取，仓库不保存真实密钥。
- 新增 `docs/schema.sql`：15 张表的 SQLite 纯结构 DDL，不含任何业务数据。
- 新增 `docs/DATABASE.md`：自动建表、空库手工初始化、MySQL 与备份说明。
- 新增 `scripts/build_release.py`：可从当前 Git 提交重建 server/full 下载包并生成 SHA256。

## 下载资产

| 资产 | 用途 |
|---|---|
| `wifidog-portal-v2.0.2-full.tar.gz` | 服务端源码 + 文档 + 数据库结构 + 验收脚本 + 路由器包 |
| `wifidog-portal-v2.0.2-server.tar.gz` | 仅服务端源码、文档、数据库结构与验收脚本 |
| `RELEASE-NOTES-v2.0.2.md` | 本说明 |
| `apfree-wifidog-*.apk` / `luci-app-*` / `luci-i18n-*` | 路由器端组件，沿用已验证版本 |
| `apfree-wifidog-libs-aarch64.tar.gz` | 路由器依赖库，沿用已验证版本 |
| `apfree-wifidog-luci-full-aarch64.tar.gz` | 路由器端全量包，沿用已验证版本 |
| `SHA256SUMS.txt` | 全部下载资产的 SHA256 校验和 |

## 部署

新服务器不需要生产数据库文件：

1. 解压 `full` 或 `server` 包到 `/opt/wifidog`。
2. 复制 `.env.example` 为 `.env`，填写域名、数据库、短信/支付等配置。
3. 启动应用；首次启动自动建 15 张表、补字段迁移、创建默认管理员和 4 个示例套餐。
4. 首次登录后立即修改 `admin/admin123` 密码。

空库手工初始化 SQLite：

```bash
sqlite3 data/wifidog.db < docs/schema.sql
```

MySQL/MariaDB 改 `WIFIDOG_DB_URL` 后由应用自动建表，`docs/schema.sql` 仅适用于 SQLite。

## 验收边界

- GitHub Actions 的 Python 3.10 / 3.11 / 3.12 import-check 通过。
- 本版本没有重新声明 v2.0.1 的 232 项浏览器全量验收结果；历史结果只适用于对应历史版本。
- 真实短信、支付宝/微信支付与网关联动仍需在目标环境配置后做小额实测。
