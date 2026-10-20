<div align="center">

# WiFiDog Auth Server

**生产级 WiFiDog 强制门户认证服务器** — Node.js + Express + FreeRADIUS + SQLite

[![CI](https://github.com/ThalynnX/wifidog-auth-server/actions/workflows/ci.yml/badge.svg)](https://github.com/ThalynnX/wifidog-auth-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)

</div>

---

## Overview

基于 [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server) 二次开发的 WiFiDog 强制门户（captive portal）认证服务器。为 WiFiDog 网关提供登录门户、令牌下发与校验、网关心跳上报等完整协议支持，使用 RADIUS 协议对接 FreeRADIUS 完成账号认证。

## Features

- 🚪 完整 WiFiDog 协议：`/login` `/portal` `/auth` `/ping` `/msg` `/status`
- 🔐 安全加固：Helmet 安全头 + CSP、登录限流防爆破、开放重定向防护、输入校验
- ⚡ 高性能：SQLite 内存库 + 防抖落盘，网关心跳热点路径零同步写盘
- 🧪 工程化：内置 `node:test` 测试套件、ESLint、GitHub Actions CI、多阶段 Docker 构建
- 📦 开箱即用：`docker compose up -d` 一条命令拉起认证服务器 + FreeRADIUS

## Architecture

```
[WiFi Client] ──▶ [WiFiDog Gateway] ──▶ [Auth Server (Node.js)] ──▶ [FreeRADIUS]
                                              │
                                        [SQLite DB]
```

| 模块 | 职责 |
|------|------|
| `src/server.js` | Express 路由：WiFiDog 协议端点、安全中间件、优雅停机 |
| `src/radius-client.js` | RADIUS 客户端：PAP 认证、超时重试、NAS 属性 |
| `src/db.js` | SQLite（sql.js）会话存储：令牌、网关、用户；防抖落盘 |
| `src/config.js` | 集中式环境配置与默认值 |
| `src/validation.js` | 输入校验与开放重定向防护 |
| `src/logger.js` | 结构化 JSON 日志 |
| `public/` | 门户页静态样式（配合严格 CSP） |

## Quick Start

### Docker（推荐）

```bash
git clone https://github.com/ThalynnX/wifidog-auth-server.git
cd wifidog-auth-server

# 先修改 freeradius/clients.conf 与 docker-compose.yml 中的 RADIUS_SECRET
docker compose up -d --build
```

认证服务器运行于 `http://localhost:2060`，FreeRADIUS 运行于 UDP `1812/1813`。

### 本地开发

```bash
npm install
cp .env.example .env
npm run dev        # 开发模式（nodemon 热重载）
npm start          # 生产模式
```

### 验证

```bash
npm test           # 运行测试套件（node:test）
npm run lint       # ESLint 检查
curl http://localhost:2060/health   # {"status":"ok"}
```

## Configuration

### 环境变量（.env）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` / `HOST` | `2060` / `0.0.0.0` | 服务监听地址 |
| `RADIUS_SERVER_HOST` | `127.0.0.1` | FreeRADIUS 地址（compose 中为 `freeradius`） |
| `RADIUS_SERVER_PORT` | `1812` | RADIUS 认证端口 |
| `RADIUS_SECRET` | `testing123` | **生产环境必须修改**，且与 `freeradius/clients.conf` 一致 |
| `RADIUS_NAS_IP` / `RADIUS_NAS_PORT` | `127.0.0.1` / `0` | RADIUS 报文中的 NAS 标识 |
| `RADIUS_TIMEOUT_MS` / `RADIUS_RETRIES` | `5000` / `2` | RADIUS 请求超时与重试 |
| `DB_PATH` | `./data/wifidog.db` | SQLite 文件路径 |
| `DB_FLUSH_INTERVAL_MS` | `500` | 数据库防抖落盘间隔 |
| `TOKEN_EXPIRY` | `3600` | 会话令牌有效期（秒） |
| `RATE_LIMIT_WINDOW_MS` / `RATE_LIMIT_MAX` | `60000` / `10` | 登录接口限流 |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warn` / `error` |

### WiFiDog 网关（/etc/wifidog.conf）

```
AuthServerHostname your-server-ip
AuthServerPort 2060
AuthServerPath /
GatewayID my-gateway-001
```

## API Endpoints

| 端点 | 方法 | 说明 |
|------|------|------|
| `/login` | GET | WiFiDog 网关重定向入口（校验令牌） |
| `/portal` | GET / POST | 用户登录页 / 登录提交（限流保护） |
| `/auth` | GET | 令牌校验，返回 `Auth: 1` / `Auth: 0` |
| `/ping` | GET | 网关心跳上报 |
| `/msg` | GET | 显示消息页 |
| `/status` | GET | 运行状态 JSON（令牌/网关/用户计数、版本、运行时长） |
| `/health` | GET | 容器健康检查 |

## Test Users（freeradius/users）

- `testuser` / `testpass`
- `admin` / `admin123`

> 生产环境请替换为 SQL / LDAP 等后端，或将 `freeradius/users` 挂载为只读凭证文件。

## Project Structure

```
wifidog-auth-server/
├── src/
│   ├── server.js           # Express 路由 + 安全中间件 + 优雅停机
│   ├── config.js           # 集中式配置
│   ├── db.js               # SQLite 会话存储（防抖落盘）
│   ├── radius-client.js    # RADIUS 客户端
│   ├── validation.js       # 输入校验 / 开放重定向防护
│   ├── logger.js           # 结构化日志
│   ├── views/              # portal.ejs / message.ejs
│   └── public/style.css    # 门户样式
├── freeradius/             # radiusd.conf / clients.conf / users
├── test/                   # db / radius-client / server 测试
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Dockerfile
└── package.json
```

## Security Notes

1. **修改默认密钥**：`RADIUS_SECRET` 与 `freeradius/clients.conf` 中的 `secret` 必须改为强随机值。
2. **门户走 HTTPS**：认证页面会提交明文密码，生产环境务必用 Nginx/Caddy 反向代理终结 TLS（`/portal`、`/login` 必须 HTTPS）。
3. **开放重定向防护**：`gateway_address` 仅接受内网 IPv4 / 回环 / ULA IPv6 字面量，恶意跳转会被拦截并回退到默认网关。
4. **登录限流**：`/portal` POST 默认每 IP 每 60 秒 10 次，防爆破。
5. **Docker 网络**：`freeradius/clients.conf` 已内置 `172.16.0.0/12` 网段客户端，允许 compose 桥接网络中的认证容器访问。
6. **数据备份**：`data/wifidog.db` 为会话数据库，建议定期备份并纳入监控。

## Production Tips

1. 用 Nginx 反向代理提供 HTTPS 并开启 gzip；
2. 将认证服务器置于内网，仅暴露 2060 给网关网段；
3. 用户量大时，将 `users` 平铺文件替换为 SQL / LDAP 模块；
4. 接入 Prometheus / 日志采集，监控 `/status` 与 `/health`。

## Based On

- [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server)
- [WiFiDog](https://wifidog.github.io/)

## License

MIT
