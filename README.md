# WiFiDog 认证服务器

<div align="center">

**基于 FreeRADIUS 的企业级 WiFi captive portal 认证解决方案**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D18.0-green.svg)](https://nodejs.org/)
[![FreeRADIUS](https://img.shields.io/badge/FreeRADIUS-3.x-blue.svg)](https://freeradius.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

[English](#english) | [中文文档](#中文文档)

</div>

---

# 中文文档

## 目录

- [项目简介](#项目简介)
- [二次开发说明](#二次开发说明)
- [系统架构](#系统架构)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [详细部署指南](#详细部署指南)
- [配置参考](#配置参考)
- [API 接口文档](#api-接口文档)
- [WiFiDog 协议说明](#wifidog-协议说明)
- [用户管理](#用户管理)
- [生产环境部署](#生产环境部署)
- [故障排查](#故障排查)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 项目简介

WiFiDog 认证服务器是一个完整的 WiFi captive portal 解决方案，用于在无线网络中实现用户认证和访问控制。当用户连接到 WiFi 网络时，会被自动重定向到认证页面，输入正确的用户名和密码后才能访问互联网。

本项目使用 **Node.js + Express** 构建 HTTP 服务层，实现 WiFiDog 协议的所有端点；使用 **FreeRADIUS** 作为 RADIUS 认证后端，处理实际的用户身份验证；使用 **SQLite** 存储会话令牌和网关状态。

### 适用场景

- 酒店/民宿 WiFi 认证
- 咖啡厅/餐厅免费 WiFi 管理
- 企业办公网络访客认证
- 校园/图书馆无线接入控制
- 社区公共 WiFi 管理
- IoT 设备网络接入认证

---

## 二次开发说明

### 基于项目

本项目是以下开源项目的二次开发作品：

| 项目 | 地址 | 用途 |
|------|------|------|
| **FreeRADIUS** | https://github.com/FreeRADIUS/freeradius-server | RADIUS 协议服务器，处理用户认证 |
| **WiFiDog 协议** | https://wifidog.github.io/ | Captive portal 协议规范 |

### 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| HTTP 服务 | Node.js + Express | WiFiDog 协议实现 |
| 认证后端 | FreeRADIUS | RADIUS 协议认证 |
| 数据库 | SQLite (sql.js) | 会话和令牌存储 |
| 模板引擎 | EJS | 登录页面渲染 |
| 容器化 | Docker + Docker Compose | 一键部署 |

### 创新点

1. **轻量级部署**：相比传统 Java/PHP 方案，Node.js 启动更快，资源占用更低
2. **纯 JS SQLite**：使用 sql.js 替代 better-sqlite3，无需原生编译，跨平台兼容
3. **Docker 一体化**：认证服务器 + FreeRADIUS 一键编排部署
4. **现代化 UI**：响应式登录页面，支持移动设备自适应

---

## 系统架构

```
┌─────────────┐     HTTP      ┌─────────────────┐    RADIUS     ┌──────────────┐
│  WiFi 客户端  │ ──────────→  │  WiFiDog 网关     │ ──────────→  │  认证服务器    │
│ (手机/电脑)   │              │  (OpenWrt 路由)   │              │  (Node.js)    │
└─────────────┘              └─────────────────┘              └──────┬───────┘
                                                                     │
                                                               UDP 1812
                                                                     │
                                                              ┌──────▼───────┐
                                                              │  FreeRADIUS   │
                                                              │  (认证后端)    │
                                                              └──────┬───────┘
                                                                     │
                                                              ┌──────▼───────┐
                                                              │  SQLite DB    │
                                                              │  (会话存储)    │
                                                              └──────────────┘
```

### 认证流程

```
1. 用户连接 WiFi
2. 用户打开浏览器，尝试访问任意网页
3. WiFiDog 网关拦截请求，302 重定向到认证服务器 /login
4. 认证服务器重定向到 /portal 登录页面
5. 用户输入用户名和密码
6. 认证服务器通过 RADIUS 协议向 FreeRADIUS 验证凭证
7. 验证成功 → 生成令牌 → 通知网关放行 → 重定向用户到原始网页
8. 网关定期向 /ping 发送心跳，向 /auth 检查令牌有效性
```

---

## 功能特性

### 核心功能

- **完整 WiFiDog 协议**：支持 login、portal、auth、ping、msg 全部端点
- **FreeRADIUS 集成**：通过标准 RADIUS 协议认证，支持 PAP 方式
- **会话管理**：令牌自动生成、过期控制、主动注销
- **网关管理**：自动记录网关心跳、系统状态、在线用户数
- **用户记录**：自动记录认证成功的用户信息

### 安全特性

- 令牌随机生成（32 位 hex）
- 可配置令牌过期时间
- RADIUS 共享密钥保护
- 参数校验防注入

### 运维特性

- 服务器状态 API（/status）
- 网关状态跟踪
- 认证日志记录
- Docker 容器化部署

---

## 快速开始

### 前置条件

- Docker 和 Docker Compose（推荐）
- 或 Node.js >= 18.0 + FreeRADIUS

### 方式一：Docker 部署（推荐，3 分钟完成）

```bash
# 1. 克隆项目
git clone https://github.com/leoNEW915/wifidog-auth-server.git
cd wifidog-auth-server

# 2. 启动所有服务
docker-compose up -d

# 3. 查看运行状态
docker-compose ps

# 4. 查看日志
docker-compose logs -f
```

服务启动后：
- 认证服务器：`http://your-server-ip:2060`
- FreeRADIUS：`UDP 1812`（容器内部通信）

### 方式二：本地开发部署

```bash
# 1. 克隆项目
git clone https://github.com/leoNEW915/wifidog-auth-server.git
cd wifidog-auth-server

# 2. 安装依赖
npm install

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，配置 RADIUS 服务器地址

# 4. 安装并启动 FreeRADIUS
# Ubuntu/Debian:
sudo apt-get install freeradius
sudo cp freeradius/clients.conf /etc/freeradius/3.0/clients.conf
sudo cp freeradius/users /etc/freeradius/3.0/users
sudo freeradius -X

# macOS:
brew install freeradius
freeradius -X

# 5. 启动认证服务器
npm start

# 开发模式（自动重启）
npm run dev
```

---

## 详细部署指南

### 一、服务器准备

#### 最低配置

| 资源 | 要求 |
|------|------|
| CPU | 1 核 |
| 内存 | 512 MB |
| 磁盘 | 1 GB |
| 系统 | Ubuntu 20.04+ / CentOS 7+ / Debian 10+ |
| 网络 | 公网 IP（网关需能访问） |

#### 推荐配置

| 资源 | 要求 |
|------|------|
| CPU | 2 核 |
| 内存 | 2 GB |
| 磁盘 | 10 GB SSD |
| 系统 | Ubuntu 22.04 LTS |
| 网络 | 100 Mbps+ |

### 二、Docker 部署详解

#### 1. 安装 Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker

# 安装 Docker Compose
sudo apt-get install docker-compose-plugin
```

#### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```ini
# 服务端口
PORT=2060

# RADIUS 配置（Docker 部署时使用容器名）
RADIUS_SERVER_HOST=freeradius
RADIUS_SERVER_PORT=1812
RADIUS_SECRET=your-strong-secret-here

# 令牌有效期（秒）
TOKEN_EXPIRY=3600
```

#### 3. 启动服务

```bash
# 启动
docker-compose up -d

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f wifidog-auth
docker-compose logs -f freeradius

# 停止
docker-compose down

# 重启
docker-compose restart
```

#### 4. 验证服务

```bash
# 检查认证服务器
curl http://localhost:2060/status

# 预期输出:
# {"status":"ok","activeTokens":0,"registeredGateways":0,"registeredUsers":0}
```

### 三、WiFiDog 网关配置

在运行 OpenWrt 的路由器上安装 WiFiDog：

```bash
# OpenWrt 安装 WiFiDog
opkg update
opkg install wifidog

# 编辑配置
vi /etc/wifidog.conf
```

配置内容：

```
# 网关 ID（唯一标识）
GatewayID my-gateway-001

# 认证服务器地址
AuthServerHostname your-server-ip
AuthServerPort 2060
AuthServerPath /

# 检查间隔（秒）
GatewayInterface br-lan
GatewayPort 2060

# 超时设置
ClientTimeout 720
WdOrphanTimeout 600
```

重启 WiFiDog：

```bash
/etc/init.d/wifidog restart
```

### 四、防火墙配置

```bash
# 开放认证服务器端口
sudo ufw allow 2060/tcp

# 如果 FreeRADIUS 需要外部访问（一般不需要）
sudo ufw allow 1812/udp
sudo ufw allow 1813/udp

# 启用防火墙
sudo ufw enable
```

---

## 配置参考

### 环境变量完整说明

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PORT` | `2060` | 认证服务器 HTTP 端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `RADIUS_SERVER_HOST` | `127.0.0.1` | FreeRADIUS 服务器地址 |
| `RADIUS_SERVER_PORT` | `1812` | FreeRADIUS 认证端口 |
| `RADIUS_SECRET` | `testing123` | RADIUS 共享密钥（**必须修改**） |
| `DB_PATH` | `./data/wifidog.db` | SQLite 数据库文件路径 |
| `TOKEN_EXPIRY` | `3600` | 令牌有效期（秒），默认 1 小时 |
| `GW_CHECK_INTERVAL` | `600` | 网关检查间隔（秒） |

### FreeRADIUS 配置说明

#### clients.conf - RADIUS 客户端定义

```
client {
    ipaddr = 127.0.0.1      # 认证服务器 IP
    secret = testing123      # 共享密钥（需与 .env 一致）
    nas_type = other
    shortname = localhost
}
```

#### users - 用户凭证文件

```
# 格式：用户名  Cleartext-Password := "密码"
testuser  Cleartext-Password := "testpass"
    Reply-Message = "Welcome to WiFi!"

admin  Cleartext-Password := "admin123"
    Reply-Message = "Admin access granted"
```

---

## API 接口文档

### GET /login

网关重定向入口。当用户首次访问网络时，WiFiDog 网关将用户重定向到此端点。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ip` | string | 是 | 客户端 IP 地址 |
| `mac` | string | 是 | 客户端 MAC 地址 |
| `token` | string | 是 | 会话令牌 |
| `gateway_id` | string | 是 | 网关唯一标识 |
| `gateway_address` | string | 否 | 网关地址 |
| `ssid` | string | 否 | WiFi 名称 |

**行为：**
- 如果令牌有效 → 直接重定向到网关
- 如果令牌无效 → 重定向到 `/portal` 登录页面

### GET /portal

显示用户登录页面。

**请求参数：** 同 `/login`

**响应：** HTML 登录页面

### POST /portal

处理用户登录请求。

**请求体（form-urlencoded）：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 用户名 |
| `password` | string | 是 | 密码 |
| `ip` | string | 是 | 客户端 IP |
| `mac` | string | 是 | 客户端 MAC |
| `token` | string | 是 | 会话令牌 |
| `gateway_id` | string | 是 | 网关 ID |
| `gateway_address` | string | 否 | 网关地址 |

**行为：**
1. 通过 RADIUS 协议向 FreeRADIUS 验证凭证
2. 验证成功 → 生成令牌 → 重定向到网关
3. 验证失败 → 返回登录页面并显示错误信息

### GET /auth

网关轮询端点，检查令牌是否仍然有效。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | 是 | 会话令牌 |

**响应：**
- `Auth: 1` — 令牌有效，允许用户继续访问
- `Auth: 0` — 令牌无效或已过期，网关将重新拦截用户

### GET /ping

网关心跳端点。网关定期发送心跳以报告自身状态。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `gw_id` | string | 是 | 网关 ID |
| `sys_uptime` | integer | 否 | 系统运行时间（秒） |
| `sys_memfree` | integer | 否 | 可用内存（KB） |
| `sys_load` | float | 否 | 系统负载 |
| `num_users` | integer | 否 | 当前在线用户数 |

**响应：** `Pong`

### GET /msg

向用户显示消息页面。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `msg` | string | 否 | 消息内容 |

### GET /status

服务器状态监控端点（JSON 格式）。

**响应示例：**

```json
{
  "status": "ok",
  "activeTokens": 5,
  "registeredGateways": 2,
  "registeredUsers": 10
}
```

---

## WiFiDog 协议说明

WiFiDog 是一个开源的 captive portal 协议，定义了网关与认证服务器之间的通信规范。

### 协议版本

本项目实现 WiFiDog Protocol v1。

### 通信流程

```
客户端          网关                认证服务器           FreeRADIUS
  │              │                    │                    │
  │──HTTP请求──→│                    │                    │
  │              │──拦截，302重定向──→│                    │
  │              │  /login?ip=&mac=  │                    │
  │              │  &token=&gw_id=   │                    │
  │              │                    │──重定向到portal──→│
  │←─────────────┴──────登录页面─────│                    │
  │──提交凭证──────────────────────→│                    │
  │              │                    │──RADIUS认证─────→│
  │              │                    │←──Access-Accept──│
  │              │                    │                    │
  │              │←──通知放行─────────│                    │
  │←──允许访问──│                    │                    │
  │              │                    │                    │
  │              │──定期ping────────→│                    │
  │              │←──────Pong────────│                    │
  │              │                    │                    │
  │              │──定期auth检查────→│                    │
  │              │←──Auth: 1/0──────│                    │
```

---

## 用户管理

### 添加用户

#### 方法一：编辑 users 文件

编辑 `freeradius/users` 文件：

```
# 新用户
zhangsan  Cleartext-Password := "password123"
    Reply-Message = "Welcome, Zhang San!"

lisi  Cleartext-Password := "secure456"
    Reply-Message = "Welcome, Li Si!"
```

重启 FreeRADIUS 使配置生效：

```bash
# Docker 部署
docker-compose restart freeradius

# 本地部署
sudo systemctl restart freeradius
```

#### 方法二：使用数据库（生产推荐）

配置 FreeRADIUS SQL 模块，将用户存储在 MySQL/PostgreSQL 中，支持动态管理。

### 批量导入用户

创建 CSV 文件 `users.csv`：

```csv
username,password,email
user001,pass001,user001@example.com
user002,pass002,user002@example.com
```

使用脚本批量添加到 FreeRADIUS。

### 删除用户

从 `freeradius/users` 文件中移除对应用户行，重启 FreeRADIUS。

---

## 生产环境部署

### 一、使用 Nginx 反向代理（HTTPS）

```nginx
server {
    listen 443 ssl http2;
    server_name auth.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/auth.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/auth.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:2060;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name auth.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

### 二、使用 Systemd 管理（非 Docker）

创建 `/etc/systemd/system/wifidog-auth.service`：

```ini
[Unit]
Description=WiFiDog Auth Server
After=network.target freeradius.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/wifidog-auth-server
ExecStart=/usr/bin/node src/server.js
Restart=always
RestartSec=10
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable wifidog-auth
sudo systemctl start wifidog-auth
sudo systemctl status wifidog-auth
```

### 三、安全加固清单

- [ ] 修改默认 RADIUS 密钥（`testing123` → 强密码）
- [ ] 配置 HTTPS（Let's Encrypt 免费证书）
- [ ] 配置防火墙，仅开放必要端口
- [ ] 定期备份 SQLite 数据库
- [ ] 启用 FreeRADIUS 日志监控
- [ ] 设置令牌合理过期时间
- [ ] 限制 RADIUS 客户端 IP
- [ ] 更新 Node.js 到最新 LTS 版本

### 四、监控与日志

```bash
# 查看认证服务器日志
docker-compose logs -f wifidog-auth

# 查看 FreeRADIUS 日志
docker-compose logs -f freeradius
docker exec freeradius cat /var/log/freeradius/radius.log

# 查看服务器状态
curl http://localhost:2060/status
```

### 五、备份与恢复

```bash
# 备份数据库
cp data/wifidog.db data/wifidog.db.backup.$(date +%Y%m%d)

# 备份 FreeRADIUS 配置
tar czf freeradius-backup.tar.gz freeradius/

# 恢复数据库
cp data/wifidog.db.backup.20261019 data/wifidog.db
docker-compose restart wifidog-auth
```

---

## 故障排查

### 问题：认证失败，提示"认证服务暂时不可用"

**原因：** 认证服务器无法连接到 FreeRADIUS

**排查步骤：**

```bash
# 1. 检查 FreeRADIUS 是否运行
docker-compose ps freeradius
# 或
netstat -an | grep 1812

# 2. 测试 RADIUS 连接
docker exec freeradius radtest testuser testpass localhost 0 testing123

# 预期输出:
# Received Access-Accept Id 123 from 127.0.0.1:1812 to 127.0.0.1:xxxxx

# 3. 检查 RADIUS 密钥是否一致
# .env 中的 RADIUS_SECRET 必须与 freeradius/clients.conf 中的 secret 一致

# 4. 查看 FreeRADIUS 日志
docker-compose logs freeradius
```

### 问题：网关无法连接认证服务器

**排查步骤：**

```bash
# 1. 检查认证服务器是否运行
curl http://your-server:2060/status

# 2. 检查防火墙
sudo ufw status
sudo iptables -L -n | grep 2060

# 3. 检查 WiFiDog 网关配置
cat /etc/wifidog.conf | grep AuthServer

# 4. 检查网络连通性
# 在网关上执行:
ping your-server-ip
curl http://your-server:2060/ping?gw_id=test
```

### 问题：用户认证后无法上网

**排查步骤：**

```bash
# 1. 检查令牌是否生成
curl "http://localhost:2060/auth?token=USER_TOKEN"
# 应返回 "Auth: 1"

# 2. 检查网关是否收到 auth 响应
# 在网关上查看日志:
logread | grep wifidog

# 3. 检查网关防火墙规则
iptables -t nat -L -n | grep WiFiDog
```

### 问题：令牌过期太快

**解决：** 修改 `.env` 中的 `TOKEN_EXPIRY` 值：

```ini
# 设置为 8 小时
TOKEN_EXPIRY=28800

# 设置为 24 小时
TOKEN_EXPIRY=86400
```

重启服务生效。

---

## 常见问题

### Q: 支持多少个并发用户？

A: 取决于服务器配置。单核 1GB 内存的 VPS 可支持约 500 个并发用户。SQLite 在低并发场景下表现良好，高并发建议切换到 MySQL/PostgreSQL。

### Q: 能否使用 MySQL 代替 SQLite？

A: 可以。配置 FreeRADIUS 的 SQL 模块连接 MySQL，然后修改 `src/db.js` 使用 MySQL 客户端库。

### Q: 支持 HTTPS 吗？

A: 认证服务器本身使用 HTTP（WiFiDog 协议要求）。建议在前端使用 Nginx 反向代理处理 HTTPS。

### Q: 如何自定义登录页面？

A: 编辑 `src/views/portal.ejs` 文件，使用 EJS 模板语法修改页面内容和样式。

### Q: 网关支持哪些设备？

A: 任何支持 WiFiDog 协议的路由器，包括运行 OpenWrt、DD-WRT、pfSense 等固件的设备。

### Q: 能否限制用户带宽或流量？

A: WiFiDog 协议本身不支持带宽限制。需要在网关层面使用 QoS 规则（如 OpenWrt 的 SQM 插件）。

---

## 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发环境搭建

```bash
git clone https://github.com/leoNEW915/wifidog-auth-server.git
cd wifidog-auth-server
npm install
cp .env.example .env
npm run dev
```

### 代码规范

- 使用 2 空格缩进
- 遵循 ESLint 规则
- 提交信息使用中文或英文均可
- 新功能需附带使用说明

### 提交 PR

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。

---

## 致谢

- [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server) — 业界领先的 RADIUS 服务器实现
- [WiFiDog](https://wifidog.github.io/) — 开源 captive portal 协议
- [Express](https://expressjs.com/) — Node.js Web 框架
- [sql.js](https://github.com/sql-js/sql.js/) — 纯 JavaScript SQLite 实现

---

# English

## Overview

WiFiDog Auth Server is a complete WiFi captive portal solution for user authentication and network access control. When users connect to WiFi, they are redirected to a login page and must authenticate before accessing the internet.

Built with **Node.js + Express** for the HTTP service layer, **FreeRADIUS** for RADIUS authentication, and **SQLite** for session storage.

## Quick Start

```bash
git clone https://github.com/leoNEW915/wifidog-auth-server.git
cd wifidog-auth-server
docker-compose up -d
```

Server runs at `http://localhost:2060`.

## Test Users

| Username | Password |
|----------|----------|
| testuser | testpass |
| admin    | admin123 |

## Based On

- [FreeRADIUS](https://github.com/FreeRADIUS/freeradius-server) — RADIUS server
- [WiFiDog](https://wifidog.github.io/) — Captive portal protocol

## License

MIT
