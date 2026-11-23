<div align="center">

# WiFiDog 认证计费系统（FastAPI 生产版）

**强制门户认证 · 短信/账号登录 · 套餐计费 · 在线支付 · 多网关管理 · 实时在线状态**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v2.0.3-orange)](CHANGELOG.md)

</div>

---

## 目录

- [1. 项目简介](#1-项目简介)
- [2. 功能一览](#2-功能一览)
- [3. 系统架构](#3-系统架构)
- [4. 支持的路由器与工作模式](#4-支持的路由器与工作模式)
- [5. 服务器环境要求](#5-服务器环境要求)
- [6. 快速开始（测试模式，10 分钟跑通）](#6-快速开始测试模式10-分钟跑通)
- [7. 从 0 部署到生产](#7-从-0-部署到生产)
- [8. 路由器对接（摘要）](#8-路由器对接摘要)
- [9. 配置项说明](#9-配置项说明)
- [10. 协议与接口](#10-协议与接口)
- [11. 管理后台](#11-管理后台)
- [12. 目录结构](#12-目录结构)
- [13. 升级 / 备份 / 排障](#13-升级--备份--排障)
- [14. 安全基线](#14-安全基线)
- [15. 下载与版本](#15-下载与版本)

---

## 1. 项目简介

本项目是一套 **可直接商用部署的 WiFi 强制门户（Captive Portal）认证与计费系统**，服务端使用
**FastAPI + SQLAlchemy + SQLite/MySQL + Gunicorn + Nginx**，路由器端对接
**apfree-wifidog / 标准 wifidog 协议**。

用户连接 WiFi 后会被网关重定向到本系统的登录门户，通过**手机号验证码**或**账号密码**完成认证，
按**时长 / 流量 / 包周期**套餐计费，支持**支付宝、微信支付**购买套餐，认证通过后由网关放行上网；
套餐到期、流量耗尽、被管理员拉黑或踢下线时，网关会收到 `Auth: 0` 并立即断网。

> **重要说明：仓库主线已切换**
>
> 本仓库早期版本（tag [`node-v3.0.0`](https://github.com/ThalynnX/wifidog-auth-server/releases/tag/node-v3.0.0)）是一套 Node.js + Express + FreeRADIUS
> 的实验性实现，**并未在实际生产环境运行**。当前 `main` 分支为生产环境实际运行的 Python/FastAPI 版本，
> 功能更完整（套餐计费、支付回调、设备绑定、MAC/IP 黑白名单、管理后台、实时统计）。
> 如需旧实现，请检出 `node-v3.0.0` 标签。

### 适用场景

| 场景 | 说明 |
|---|---|
| 商铺 / 餐饮 / 咖啡厅 WiFi | 顾客扫码或短信认证上网，可售卖时长/包月套餐 |
| 公寓 / 宿舍 / 出租屋 | 按房间或按人售卖宽带套餐，绑定设备数 |
| 酒店 / 民宿 | 客房 WiFi 认证，前台可发券或后台手动开通 |
| 工厂 / 园区宿舍 | 员工实名（手机号）认证，黑白名单管理 |
| 校园 / 培训机构的访客网络 | 访客限时免费体验 + 付费升级 |

---

## 2. 功能一览

### 2.1 认证与门户

- 标准 **WiFiDog 协议**：`ping / login / portal / message / auth(login, counters, logout) / gwstats`
- 兼容 **apfree-wifidog 的 `counters_v2` 批量上报协议**（v1.1.2 起）
- 登录方式：**手机号 + 短信验证码**、**用户名 + 密码**
- 登录成功后签发 **24 小时上网 token**，浏览器跳转网关 `auth` 接口完成放行
- 支持 **免认证 IP 白名单**（如收银机、打印机、有线办公网段）与 **免认证 MAC 白名单**
- 支持 **强制断网 MAC 黑名单**，优先级最高，可覆盖有效套餐并立即踢线
- 门户页面自适应手机端，支持站点名称、公告自定义
- v2.0.0 统一深海军蓝 + 青色科技风，登录、套餐、支付、成功页使用一致交互语言
- 支付后自动恢复设备会话，成功页实时读取套餐、MAC、IP、速率、在线时长和流量

### 2.2 套餐与计费

| 套餐类型 | `type` | 计费方式 |
|---|---|---|
| 免费体验 | `free` | 固定时长/流量，每设备一次 |
| 时长套餐 | `time` | 购买后可用 N 分钟，可配流量上限 |
| 流量套餐 | `flow` | 可用 N MB，可配有效天数 |
| 包周期套餐 | `period` | 自购买起 N 天内不限时长（可配流量/限速） |

- 支持原价/现价、上下行限速（kbps）、同时在线设备数限制、上架/下架、排序
- 默认初始化 4 个示例套餐（1 小时体验、24 小时畅享、包月套餐、10GB 流量包）与 1 个隐藏的免费体验套餐，均可在后台修改
- 新设备默认赠送 **10 分钟 / 100MB** 免费体验（后台可调，0 表示关闭）
- 流量统计来自网关 `counters` 上报，按会话累计，白名单设备不扣套餐流量

### 2.3 支付

- **支付宝当面付/手机网站支付**（RSA2 签名、异步通知验签、金额核对、幂等开通）
- **微信支付 APIv3**（AES-GCM 回调解密、金额核对、幂等开通）
- **Mock 支付**（测试模式专用，一键模拟支付成功）
- 订单状态：待支付 / 已支付 / 已关闭 / 已退款；后台支持手动退款标记

### 2.4 设备与用户管理

- 多网关管理：网关 ID、名称、安装位置、共享密钥、在线状态、系统负载、最后上报 IP
- 用户管理：禁用/启用、删除账号、手动开通套餐、查看绑定设备与订单
- 设备绑定：一个账号可绑定 MAC，支持换绑（默认每 30 天 3 次，可按用户增减额度、重置周期）
- 设备控制：后台可解绑/改绑用户设备、踢下线；管理员操作不计入自助换绑次数
- 在线会话：查看当前在线用户、IP、MAC、套餐、已用流量，支持一键踢下线
- 认证日志：完整记录登录、认证、计费、踢线、失败原因

### 2.5 运维与安全

- 单文件 SQLite（WAL 模式）开箱即用，也可切换 MySQL/MariaDB
- 启动时**自动建表与轻量迁移**，升级不需手工改表
- 仓库提供 [`docs/schema.sql`](docs/schema.sql) 纯结构 DDL（15 张表，不含任何业务数据），生产库 `data/*.db` 不入库
- 管理端 JWT + HttpOnly Cookie，支持 `x-admin-token` 头调用
- 防爆破：验证码 5 次错误作废；登录按 IP/手机号限流；管理员失败 5 次锁 IP 10 分钟
- 短信与支付密钥只从 `.env` 读取，不落数据库
- 系统运行模式：`test`（短信/支付 mock）与 `production`（真实通道），后台可一键切换
- Nginx + Gunicorn 双层部署，支持 WebSocket 长连接（apfree-wifidogx 实时通道）

---

## 3. 系统架构

```
                         ┌──────────────────────────────┐
  手机/电脑 ──连接WiFi──▶ │  路由器（apfree-wifidogx）    │
      ▲                  │  br-lan 发放 DHCP / 拦截流量  │
      │                  └───────────┬──────────────────┘
      │  ① 302 到门户                │ ② ping / auth / counters
      │                              ▼
┌─────┴────────────────────────────────────────────────────────┐
│ Nginx（80/443，HTTPS 终止、静态资源、WebSocket 升级）          │
└─────┬────────────────────────────────────────────────────────┘
      │ 127.0.0.1:8000
┌─────▼────────────────────────────────────────────────────────┐
│ Gunicorn + UvicornWorker                                     │
│  ├─ /wifidog/*   网关协议（ping/login/auth/gwstats/ws）       │
│  ├─ /portal/*    用户门户（登录、套餐、下单、支付、状态）      │
│  ├─ /admin/*     管理后台（页面 + JSON API）                  │
│  └─ 定时任务      会话巡检、离线判定、到期断网                 │
└─────┬────────────────────────────────────────────────────────┘
      │ SQLAlchemy
┌─────▼────────────────────────────────────────────────────────┐
│ SQLite（WAL，默认） 或 MySQL / MariaDB                        │
│ users devices plans orders subscriptions sessions sms_codes   │
│ auth_logs admins settings mac_whitelist mac_blacklist ip_whitelist │
└──────────────────────────────────────────────────────────────┘
      │
      ├─▶ 短信通道（阿里云 / 通用 HTTP / mock）
      └─▶ 支付通道（支付宝 / 微信 APIv3 / mock）
```

**认证时序（简化版）**

```
终端           网关                服务端
 │  访问 http://x │                   │
 │──────────────▶│ 302 到 /wifidog/login?gw_id&gw_address&gw_port&mac&ip&url
 │◀──────────────│                   │
 │  打开门户，输入手机号+验证码        │
 │──────────────────────────────────▶ POST /portal/api/login
 │◀──────────────────────────────────│ {redirect: http://gw:port/wifidog/auth?token=...}
 │  浏览器访问 redirect               │
 │──────────────▶│ stage=login       │
 │               │──────────────────▶│ 校验 token → Auth: 1
 │               │◀──────────────────│
 │  可以上网      │ 周期 stage=counters → 累计流量、套餐校验 → Auth: 1/0
```

更多细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 4. 支持的路由器与工作模式

### 4.1 路由器适配清单

| 平台 / 芯片 | 架构 | 典型机型 | 固件 | 本项目提供 | 支持程度 |
|---|---|---|---|---|---|
| Qualcomm `ipq60xx`（qualcommax） | `aarch64_cortex-a53` | 京东云亚瑟 AX1800、部分 AX5/AX6 改机 | ImmortalWrt 23.05 / 24.10 | `router-packages/` 内 apk + libs + LuCI 全量包 | ✅ 已验证（推荐） |
| Qualcomm `ipq807x` | `aarch64_cortex-a53` | 小米 AX9000、红米 AX6000 等 | ImmortalWrt | 需自行编译 | ⚠️ 理论支持，需编译 |
| MediaTek `filogic 830/820` | `aarch64_cortex-a53` | MT7986 系列（红米 AX6000、GL-MT6000） | ImmortalWrt | 需自行编译 | ⚠️ 理论支持，需编译 |
| MediaTek `mt7621` | `mipsel_24kc` | 新路由 3、K2P、小米 4A 千兆 | OpenWrt 23.05 | 需自行编译 | ⚠️ 理论支持，需编译 |
| Atheros `ar71xx/ath79` | `mips_24kc` | 老款 TP-Link / 网件 | OpenWrt 19.07+ | 需自行编译 | ⚠️ 老固件，建议升级 |
| x86_64 软路由 | `x86_64` | N100/J4125 多网口软路由 | OpenWrt / ImmortalWrt | 需自行编译 | ✅ 建议（性能最好） |
| 原版 `wifidog`（OpenWrt 官方包） | 任意 | opkg 可安装的机型 | OpenWrt 21.02+ | 通用 | ✅ 兼容（走标准协议，无 counters_v2） |

> 本仓库 `router-packages/` 提供的 aarch64 包**仅适用于 `qualcommax/ipq60xx` + `aarch64_cortex-a53`**。
> 其它架构请参考 [docs/ROUTER_SETUP.md](docs/ROUTER_SETUP.md) 第 8 节自行用 OpenWrt SDK 编译，
> 或直接安装 OpenWrt 官方 `wifidog` 包（功能较少但协议兼容）。

### 4.2 三种工作模式（务必选对）

| 模式 | 拓扑 | 是否推荐 | 说明 |
|---|---|---|---|
| **A. 主路由 / NAT 模式** | 光猫 → 本路由器（PPPoE/DHCP 拨号）→ 交换机/AP | ✅ **强烈推荐** | 路由器同时是网关和 DHCP 服务器，能完整拦截所有流量，认证、限速、踢线全部生效 |
| **B. 二级路由 / 旁路 DHCP 模式** | 上游路由 → 本路由器 LAN 口 → 终端 | ✅ 推荐（需改 DHCP） | 本路由器关闭 WAN，LAN 口接上游；**必须由本机下发 DHCP（或上游 DHCP 把默认网关指向本机）**，否则流量不经过本机，无法认证 |
| **C. 纯 AP / 桥接模式** | 上游路由负责 DHCP，本机只做无线 AP | ❌ 不推荐 | 终端默认网关是上游路由，wifidog 无法劫持流量，**认证不会生效**；除非上游支持把网关指向本机 |
| **D. 多 SSID / 多网关** | 一台路由器多个 gateway 段 | ✅ 支持 | 不同 SSID 对应不同 `gw_id`，可分别统计和售卖套餐 |

**选型建议**

- 单店 / 单层：一台刷好 ImmortalWrt 的路由器做**主路由**（模式 A）最稳。
- 已有主路由不想动：用**模式 B**，但一定要把本机设为终端 DHCP 服务器，或在上游把 DHCP 网关指到本机 LAN IP。
- 大覆盖：主路由（模式 A）+ 多台 AP（AP 模式只做无线扩展，认证仍在主路由完成）。

### 4.3 路由器设置总览（详细步骤见 docs/ROUTER_SETUP.md）

1. 刷入 **ImmortalWrt / OpenWrt**（23.05 或 24.10），确认能正常上网（LAN `192.168.6.1`，WAN 拨号成功）。
2. 安装 `apfree-wifidogx` + 依赖库 + LuCI 插件（apk 或 opkg，见第 8 节）。
3. LuCI → **服务 → WiFiDogX**，填写认证服务器地址、端口、路径、网关接口、网关 ID。
4. 启用服务并保存应用，`/etc/init.d/wifidogx restart`。
5. 服务端后台（正式模式）先登记该 `gw_id`，再让路由器上报。
6. 用手机连 WiFi 验证：应弹出登录页 → 登录 → 可上网 → 后台显示在线。

---

## 5. 服务器环境要求

### 5.1 硬件

| 项目 | 最低 | 推荐 | 说明 |
|---|---|---|---|
| CPU | 1 核 | 2 核 | Gunicorn 默认 1~2 worker |
| 内存 | 1 GB | 2 GB | SQLite + Python 约占用 200~400MB |
| 磁盘 | 10 GB | 20 GB SSD | 日志与数据库增长很慢 |
| 带宽 | 1 Mbps | 按门店规模 | 认证流量很小，用户上网流量走路由器出口 |

> 单台 2C2G 云服务器可轻松支撑 **数十台网关、数千终端**（瓶颈通常在出口带宽而非认证服务）。

### 5.2 软件

| 组件 | 版本要求 | 说明 |
|---|---|---|
| 操作系统 | Debian 12 / Ubuntu 22.04+ / OpenCloudOS 9 / CentOS Stream 9 | 其它 Linux 亦可 |
| Python | **3.10+**（推荐 3.11/3.12） | 需要 `python3-venv`、`python3-dev` |
| 数据库 | SQLite 3.35+（默认）或 MySQL 8 / MariaDB 10.6+ | SQLite 需 WAL 支持 |
| Web 服务器 | Nginx 1.18+ | 反向代理、HTTPS、静态资源、WebSocket |
| 进程管理 | systemd | 开机自启、崩溃重启 |
| 可选 | Redis 6+ | 多实例共享限流计数（单机可不装） |

### 5.3 网络与端口

| 端口 | 用途 | 暴露范围 |
|---|---|---|
| `80` | HTTP（跳转 HTTPS / ACME 验证） | 公网 |
| `443` | HTTPS 门户与后台 | 公网 |
| `8000` | Gunicorn 应用端口 | **仅 127.0.0.1** |
| `60080/60081` | 旧部署沿用的对外/内部端口 | 可选，按需 |

- **生产环境必须使用域名 + HTTPS**：微信支付、支付宝回调、短信签名审核都要求可公网访问的 HTTPS 地址。
- 域名示例：`portal.example.com`，A 记录指向服务器公网 IP。
- 云服务器安全组 / 防火墙只需放行 `22`、`80`、`443`。
- 服务器时区建议 `Asia/Shanghai`，并开启 NTP（订单与套餐到期依赖系统时间）。

### 5.4 依赖清单

`requirements.txt` 固定了运行依赖，均为纯 Python 包，无需编译系统库：

```
fastapi>=0.110  uvicorn[standard]  gunicorn>=21.2  SQLAlchemy>=2.0
PyMySQL  redis  Jinja2  python-multipart
passlib  bcrypt  PyJWT  requests  cryptography>=42
```

---

## 6. 快速开始（测试模式，10 分钟跑通）

> 测试模式下：短信验证码直接在接口返回 `dev_code`，支付走本站模拟页，**不需要任何第三方密钥**。

```bash
# 1) 拉取源码
git clone https://github.com/ThalynnX/wifidog-auth-server.git
cd wifidog-auth-server

# 2) 创建虚拟环境并安装依赖
python3 -m venv venv
./venv/bin/pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
./venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3) 准备配置
cp .env.example .env
mkdir -p data logs

# 4) 前台启动（首次启动会自动建表 + 初始化管理员与示例套餐）
./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开浏览器：

- 用户门户：`http://服务器IP:8000/wifidog/login?gw_id=test&gw_address=192.168.6.1&gw_port=2060&mac=AA:BB:CC:DD:EE:01&ip=192.168.6.100`
- 管理后台：`http://服务器IP:8000/admin`，默认账号 **`admin` / `admin123`**（登录后立刻改密码）
- 健康检查：`http://服务器IP:8000/healthz`

**测试模式验收流程**

1. 后台 → 套餐管理，确认已有 4 个示例套餐（可改价、改时长）。
2. 门户页输入手机号 → 点“发送验证码” → 接口/页面会显示 `dev_code` → 输入登录。
3. 登录成功跳到套餐页 → 选套餐 → 购买 → 自动进入模拟支付页 → 点“确认支付”。
4. 后台“订单管理”出现已支付订单，“用户管理”显示套餐与到期时间。
5. 用 curl 模拟网关认证（把 `<TOKEN>` 换成登录返回的 token）：

```bash
curl "http://127.0.0.1:8000/wifidog/auth?stage=login&ip=192.168.6.100&mac=aa:bb:cc:dd:ee:01&token=<TOKEN>&incoming=0&outgoing=0&gw_id=test"
# 期望输出：Auth: 1
```

---

## 7. 从 0 部署到生产

完整命令级教程见 **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**，下面是主线步骤：

```bash
# ① 系统依赖（Debian/Ubuntu）
apt update && apt install -y python3 python3-venv python3-dev nginx sqlite3 git curl

# ② 目录与用户
useradd -r -s /usr/sbin/nologin wifidog || true
mkdir -p /opt/wifidog && chown -R wifidog:wifidog /opt/wifidog

# ③ 部署代码（二选一）
git clone https://github.com/ThalynnX/wifidog-auth-server.git /opt/wifidog
# 或解压 Release 里的 wifidog-portal-v1.1.2-full.tar.gz 到 /opt/wifidog

# ④ 虚拟环境
cd /opt/wifidog
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt

# ⑤ 配置
cp .env.example .env
sed -i "s/^WIFIDOG_RUN_MODE=.*/WIFIDOG_RUN_MODE=test/" .env
mkdir -p data logs && chown -R wifidog:wifidog data logs
./venv/bin/python -c "import secrets;print('WIFIDOG_SECRET='+secrets.token_urlsafe(48))" >> .env

# ⑥ systemd
cp deploy/wifidog.service /etc/systemd/system/wifidog.service
# 按实际路径修改 WorkingDirectory / ExecStart / User
systemctl daemon-reload && systemctl enable --now wifidog
systemctl status wifidog --no-pager

# ⑦ Nginx
cp deploy/nginx_wifidog.conf /etc/nginx/conf.d/wifidog.conf
# 修改 server_name 与证书路径
nginx -t && systemctl reload nginx

# ⑧ HTTPS
apt install -y certbot python3-certbot-nginx
certbot --nginx -d portal.example.com
```

部署完成后：

1. 访问 `https://portal.example.com/admin`，用 `admin/admin123` 登录并**立即修改密码**。
2. “设备管理”新增网关（填写路由器上配置的 `gw_id`）。
3. “套餐管理”调整价格与时长。
4. 路由器按第 8 节配置并联网。
5. 手机连接 WiFi，走完“门户 → 登录 → 购买 → 上网”全流程。
6. 一切正常后，在后台“系统设置”把运行模式切到 **production**，并配置真实短信与支付参数。

---

## 8. 路由器对接（摘要）

### 8.1 服务端需要提供的信息

| 参数 | 示例 | 说明 |
|---|---|---|
| 认证服务器域名 | `portal.example.com` | 必须能被路由器解析 |
| 端口 | `443`（HTTPS）或 `80` | 建议 HTTPS |
| 协议路径 | `/wifidog/` | **结尾必须带斜杠** |
| 网关 ID | `shop01` | `^[A-Za-z0-9_.-]{1,64}$`，全局唯一 |
| 网关接口 | `br-lan` | 给终端发 WiFi 的桥接口 |
| 出口接口 | `wan` / `pppoe-wan` | 拨号后的实际出口名 |

### 8.2 apfree-wifidogx 配置示例（`/etc/config/wifidogx`）

```uci
config wifidogx 'common'
	option enabled '1'
	option auth_server_mode 'cloud'
	option external_interface 'wan'
	option device_id 'shop01'
	option auth_server_hostname 'portal.example.com'
	option auth_server_port '443'
	option auth_server_path '/wifidog/'
	option check_interval '60'
	option client_timeout '5'
	option apple_cna '1'
	option js_filter '1'
	option enable_dns_forward '1'
	option long_conn_mode 'wss'
	option log_level '6'

config gateway 'main'
	option gateway_auth_enabled '1'
	option gateway_name 'br-lan'
	option gateway_channel 'wlan0'
	option gateway_id 'shop01'
	option gateway_subnetv4 '192.168.6.0/24'
```

```bash
/etc/init.d/wifidogx restart
logread -f | grep -i wifidog      # 观察 ping/auth 日志
```

### 8.3 标准 wifidog 配置（`/etc/wifidog.conf`）

```conf
GatewayID            shop01
GatewayInterface     br-lan
GatewayAddress       192.168.6.1
ExternalInterface    pppoe-wan

AuthServer {
    Hostname                   portal.example.com
    HTTPPort                   443
    Path                       /wifidog/
    LoginScriptPathFragment    login/?
    PortalScriptPathFragment   portal/?
    MsgScriptPathFragment      message/?
    PingScriptPathFragment     ping/?
    AuthScriptPathFragment     auth/?
}
CheckInterval 60
ClientTimeout 10
```

> **关于 HTTPS**：原版 wifidog 不支持 TLS，请让 Nginx 同时监听 80（仅 `/wifidog/` 路径），
> 或在路由器上做端口转发/透明代理；apfree-wifidogx 支持 HTTPS 直连。
>
> 路由器端的完整安装、LuCI 截图级步骤、防火墙、DNS 劫持、多 SSID、有线免认证、
> 故障排查，全部写在 **[docs/ROUTER_SETUP.md](docs/ROUTER_SETUP.md)**。

---

## 9. 配置项说明

配置优先级：**后台“系统设置” > 环境变量 > 代码默认值**。短信、支付密钥只从环境变量读取。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WIFIDOG_RUN_MODE` | `test` | `test` / `production` |
| `WIFIDOG_SECRET` | 空 | JWT 签名密钥，留空自动生成到 `data/.secret_key` |
| `WIFIDOG_DB_URL` | `sqlite:///data/wifidog.db` | 可改为 `mysql+pymysql://user:pass@host/db?charset=utf8mb4` |
| `WIFIDOG_DB_ECHO` | `0` | 打印 SQL 调试 |
| `WIFIDOG_DEBUG` | `0` | 调试模式（生产必须 0） |
| `WIFIDOG_SITE_NAME` | `WiFi 认证服务` | 门户与后台标题 |
| `WIFIDOG_PUBLIC_BASE` | 空 | 对外基地址，如 `https://portal.example.com` |
| `WIFIDOG_GATEWAY_PROTO` | `http` | 跳转网关时使用的协议 |
| `WIFIDOG_FREE_TRIAL_MINUTES` | `10` | 新设备免费时长（0 关闭） |
| `WIFIDOG_FREE_TRAFFIC_MB` | `100` | 免费流量（0 不限） |
| `WIFIDOG_DEVICE_LIMIT` | `3` | 默认同时在线设备数（0 不限） |
| `WIFIDOG_SESSION_IDLE_TIMEOUT` | `600` | 会话无上报判离线（秒） |
| `WIFIDOG_DEVICE_OFFLINE_TIMEOUT` | `180` | 网关离线判定（秒） |
| `WIFIDOG_ADMIN_TOKEN_EXPIRE` | `43200` | 后台 token 有效期（秒） |
| `WIFIDOG_PORTAL_TOKEN_EXPIRE` | `86400` | 上网 token 有效期（秒） |
| `WIFIDOG_SMS_PROVIDER` | `mock` | `aliyun` / `http` / `mock` |
| `WIFIDOG_SMS_ACCESS_KEY` | — | 短信 AccessKey |
| `WIFIDOG_SMS_SECRET_KEY` | — | 短信 SecretKey |
| `WIFIDOG_SMS_SIGN_NAME` | — | 短信签名 |
| `WIFIDOG_SMS_TEMPLATE_CODE` | — | 模板 CODE |
| `WIFIDOG_SMS_CODE_EXPIRE` | `300` | 验证码有效期（秒） |
| `WIFIDOG_SMS_DAILY_LIMIT` | `20` | 单号每日上限 |
| `WIFIDOG_SMS_IP_HOURLY_LIMIT` | `10` | 单 IP 每小时上限 |
| `WIFIDOG_PAY_PROVIDER` | `mock` | `alipay` / `wechat` / `mock` |
| `WIFIDOG_ALIPAY_APPID` | — | 支付宝应用 ID |
| `WIFIDOG_ALIPAY_PRIVATE_KEY` | — | 应用私钥 PEM（`\n` 转义为一行） |
| `WIFIDOG_ALIPAY_PUBLIC_KEY` | — | 支付宝公钥 PEM |
| `WIFIDOG_ALIPAY_NOTIFY_URL` | — | `https://域名/portal/api/pay/notify/alipay` |
| `WIFIDOG_WECHAT_APPID` | — | 公众号/小程序 AppID |
| `WIFIDOG_WECHAT_MCHID` | — | 商户号 |
| `WIFIDOG_WECHAT_API_V3` | — | APIv3 密钥 |
| `WIFIDOG_WECHAT_SERIAL_NO` | — | 商户证书序列号 |
| `WIFIDOG_WECHAT_PRIVATE_KEY` | — | 商户私钥 PEM |
| `WIFIDOG_WECHAT_NOTIFY_URL` | — | `https://域名/portal/api/pay/notify/wechat` |

完整模板见 [`.env.example`](.env.example)。

---

## 10. 协议与接口

### 10.1 网关协议（`/wifidog/`）

| 方法 | 路径 | 参数 | 返回 |
|---|---|---|---|
| GET | `/wifidog/ping` | `gw_id, sys_uptime, sys_memfree, sys_load, wifidog_uptime` | `Pong`（纯文本） |
| GET | `/wifidog/login` | `gw_id, gw_address, gw_port, mac, ip, url` | 门户 HTML，白名单直接 302 回网关 |
| GET | `/wifidog/portal` | 同上 | 兼容旧网关的跳转 |
| GET | `/wifidog/message` | `gw_id, message` | 纯文本提示 |
| GET/POST | `/wifidog/auth` | `stage=login/counters/logout/counters_v2` | **必须纯文本 `Auth: 1` / `Auth: 0`** |
| POST | `/wifidog/gwstats` | 请求头 `x-gw-key` | JSON 统计入库 |
| WS | `/ws/wifidogx` | apfree 实时通道 | `Pong` / 指令下发 |

### 10.2 用户端接口（`/portal/`）

`send_code` · `login` · `password_login` · `device/rebind` · `device/auto` · `plans` · `buy` ·
`pay/mock` · `pay/confirm` · `pay/notify/alipay` · `pay/notify/wechat` · `me` · `live` · `success`

### 10.3 管理端接口（`/admin/`）

`login` · `logout` · `me` · `stats` · `users` · `devices` · `plans` · `orders` · `sessions` ·
`blacklist` · `whitelist` · `ipwhitelist` · `logs` · `settings` · `admin/password`

> 完整参数、返回示例、错误码、签名规则见 **[docs/API.md](docs/API.md)**（含网关侧与自研客户端对接）。

---

## 11. 管理后台

访问 `/admin`，默认 `admin / admin123`（**首次登录必须修改**）。

| 模块 | 能力 |
|---|---|
| 数据概览 | 用户数、在线数、今日订单/收入、网关在线状态 |
| 用户管理 | 搜索、禁用/启用、删除账号、手动开通套餐、查看/解绑/改绑设备、调整换绑额度 |
| 设备管理 | 网关增删改、在线状态、最后上报时间/IP、系统负载 |
| 套餐管理 | 新增/编辑/上下架、时长/流量/周期、限速、设备数 |
| 订单管理 | 按状态筛选、查看支付流水、标记退款 |
| 在线会话 | 实时在线列表、已用流量、强制踢下线 |
| MAC 白名单 | 免认证设备（如收银机、打印机） |
| IP 白名单 | 免认证网段/单 IP（如有线办公网） |
| MAC 黑名单 | 强制断网，优先级最高，立即踢线 |
| 认证日志 | 登录、认证、计费、踢线、失败原因 |
| 系统设置 | 运行模式、站点名称、公告、免费时长、设备数、修改密码 |

### v2.0.3 用户与设备控制（当前）

- 每个用户拥有独立的 30 天换绑额度，默认 3 次，后台可按用户 `+1` / `-1`、直接设置上限或重置已用次数。
- 后台新增「用户控制」面板：查看绑定 MAC、上次绑定时间、在线会话，支持解绑/改绑设备、踢下线、禁用/恢复、删除用户。
- 管理员直接解绑/改绑不计入用户自助换绑次数；额度为 0 时禁止用户自助换绑，管理员仍可直接处理。
- 删除用户需输入手机号/用户名/ID 确认；有生效套餐时需强制确认。删除会清理订阅、会话、验证码和账号，保留订单用于财务审计。
- 新增 `device_change_limit`、`device_change_window_start` 两列，旧库启动时自动迁移，存量用户默认 3 次。
- 本次发布不包含尚未部署的授权模块。

### v2.0.2 实时数据一致性与多 worker

- 在线判定统一为“最近 120 秒有网关上报”，踢线/退款/禁用后的旧 token 不会复活。
- 新增 Redis Pub/Sub WS 总线，后台统计、设备在线和门户状态在多 worker 下保持一致。
- 后台 9 个页面、门户套餐/成功页/模拟支付页按 3–5 秒同步，动态页面与 API 禁止缓存。
- 网关统计密钥改为 `WIFIDOG_GWSTATS_KEY` 环境变量，仓库不保存真实密钥。
- 新增 `docs/schema.sql` 与 `docs/DATABASE.md`：15 张表结构可直接审计，空库可手工初始化；生产库本身不入库。

### v2.0.1 安全回跳修复

- 认证成功页“继续访问”只允许回跳公网 `http/https` 原页面；网关地址、私网、回环、`.local` 主机名和系统联网检测页全部拒绝。
- 网关只提供路由器根地址或没有可恢复的合法原页面时，不再跳转，按钮改为“认证已完成，可直接上网”并给出提示。
- 登录页保存 `wg_url` 前先做安全判定，并清理 `localStorage` 中已有的危险回跳值；静态资源版本号同步为 `2.0.1`。
- 认证、计费、支付、设备绑定、黑白名单、WebSocket 和数据库结构均未改动。

> v2.0.1 验收结果：接口与协议 **71/71**、Chromium **64/64**、弹窗与数据库专项 **97/97**，合计 **232/232**。其中 Chromium 新增公网正常回跳、危险地址拦截、成功页停留提示 3 项专项检查。

### v2.0.0 科技风控制台

- 管理端和门户端共 6 个页面整体重做，统一深海军蓝、青色与蓝绿色视觉；后台优先桌面并兼顾窄屏，门户优先手机。
- 所有表单、弹窗和确认框均有明确的确定、取消、关闭行为，以及请求中、成功、失败、超时和 401 反馈；提交期间防止重复点击。
- 套餐编辑补齐上下行限速，退款补齐真实渠道退款确认，用户/订单/日志补齐分页，管理员改密独立成区。
- 自动识别、账号密码登录、支付和设备换绑均保存并恢复会话；门户验证码兼容实际短信通道的 4–6 位数字。
- 提供三套可复现验收：接口与协议、Chromium 全按钮、弹窗与子窗口数据库落盘。使用方法和覆盖范围见 [tests/README.md](tests/README.md)。

> v2.0.0 验收结果：接口与协议 **71/71**、Chromium **61/61**、弹窗与数据库专项 **97/97**，合计 **229/229**。验收使用隔离 SQLite 数据库；真实短信和支付通道仍需在生产配置后进行小额实测。

---

## 12. 目录结构

```
wifidog-auth-server/
├── app/
│   ├── main.py              # FastAPI 入口、启动建表与种子数据、定时巡检
│   ├── config.py            # 环境变量与默认值
│   ├── database.py          # SQLAlchemy 引擎/会话（SQLite WAL / MySQL）
│   ├── models.py            # 15 张数据表
│   ├── security.py          # 密码哈希、JWT 签发校验
│   ├── services_auth.py     # 认证核心：套餐校验、放行/拒绝判定
│   ├── device_auth.py       # 设备绑定、换绑、设备数限制
│   ├── ratelimit.py         # 内存/IP/手机号限流
│   ├── sms.py               # 阿里云 / HTTP / mock 短信
│   ├── payment.py           # 支付宝 / 微信 / mock 支付
│   ├── realtime.py          # 实时统计与 WebSocket 广播
│   ├── settings_store.py    # 后台设置读写（settings 表）
│   ├── templates_env.py     # Jinja2 环境与过滤器
│   ├── routers/
│   │   ├── wifidog.py       # 网关协议
│   │   ├── portal.py        # 用户门户与支付回调
│   │   ├── admin.py         # 管理后台页面与 API
│   │   └── ws.py            # apfree-wifidogx WebSocket
│   ├── templates/           # portal / admin 页面
│   └── static/              # 静态资源
├── deploy/
│   ├── install.sh           # 一键安装脚本（Debian/Ubuntu/OpenCloudOS）
│   ├── wifidog.service      # systemd 模板
│   └── nginx_wifidog.conf   # Nginx 站点模板
├── docs/
│   ├── DEPLOYMENT.md        # 从 0 部署（命令级）
│   ├── DATABASE.md          # 15 张表结构与初始化说明
│   ├── schema.sql           # SQLite 纯结构 DDL（无数据）
│   ├── ROUTER_SETUP.md      # 路由器型号/模式/设置/排障
│   ├── ARCHITECTURE.md      # 架构与数据模型
│   ├── API.md               # 网关协议 + 客户端 + 管理 API
│   └── FAQ.md               # 常见问题
├── scripts/
│   └── build_release.py     # 从当前提交重建 server/full 下载包与 SHA256
├── tests/                   # 接口、Chromium、弹窗与数据库落盘验收
├── router-packages/         # 路由器端安装包（apk / libs / LuCI）
├── data/                    # SQLite 数据库与密钥（运行时生成，已忽略）
├── logs/                    # 日志（运行时生成，已忽略）
├── requirements.txt
├── gunicorn_conf.py
├── .env.example
├── CHANGELOG.md
└── LICENSE
```

---

## 13. 升级 / 备份 / 排障

### 13.1 升级

```bash
cd /opt/wifidog
git pull --ff-only
./venv/bin/pip install -r requirements.txt
systemctl restart wifidog
systemctl status wifidog --no-pager
```

启动时会自动建表并做轻量字段迁移，**升级前仍建议备份数据库**。

### 13.2 备份

```bash
# SQLite 安全备份（WAL 模式推荐用 sqlite3 .backup）
sqlite3 /opt/wifidog/data/wifidog.db ".backup '/backup/wifidog-$(date +%F).db'"
tar czf /backup/wifidog-conf-$(date +%F).tar.gz /opt/wifidog/.env
```

建议加 cron 每日备份并保留 14 天；`.env` 内含密钥，备份文件权限设为 `600`。

### 13.3 排障速查

| 现象 | 排查方向 |
|---|---|
| 门户打不开 | Nginx 是否监听 80/443；`curl -I http://127.0.0.1:8000/healthz`；防火墙/安全组 |
| 登录后仍不能上网 | 网关 `auth` 是否返回 `Auth: 1`；token 是否过期；网关时间是否准确 |
| 后台设备一直离线 | 路由器 ping 是否可达；`gw_id` 是否与后台登记一致；`check_interval` 是否过大 |
| 验证码收不到 | 短信通道密钥/签名/模板是否审核通过；测试模式看 `dev_code` |
| 支付成功未开通 | 回调 URL 是否公网 HTTPS 可达；验签是否通过；订单金额是否一致 |
| 502 Bad Gateway | Gunicorn 未启动/端口不符；`journalctl -u wifidog -n 50` |
| 认证页不自动弹出 | 网关 DNS 劫持/重定向规则；手机 captive portal 检测缓存 |

更多见 [docs/FAQ.md](docs/FAQ.md)。

---

## 14. 安全基线

- [ ] 首次登录立即修改 `admin` 默认密码，并改用强密码
- [ ] `.env` 权限 `600`，`data/` 目录仅服务账号可读写
- [ ] 生产环境 `WIFIDOG_DEBUG=0`，Gunicorn 只监听 `127.0.0.1`
- [ ] 全站 HTTPS，HTTP 301 跳转；关闭不必要端口
- [ ] 数据库不直接暴露公网；MySQL 使用独立低权限账号
- [ ] 定期备份并验证可恢复
- [ ] 关注 `auth_logs` 异常登录与高频失败 IP
- [ ] 路由器管理后台改默认密码，关闭 WAN 侧管理入口

---

## 15. 下载与版本

### 当前版本：v2.0.3（用户与设备控制）

本次更新：新增用户级 30 天换绑额度（默认 3 次，可按用户增减/设置/重置）；后台用户控制面板支持查看绑定设备与在线会话、解绑/改绑、踢下线、禁用/恢复和删除用户；删除用户保留订单用于审计；旧库自动迁移两个新字段。

最新 Release：[v2.0.3 下载与更新说明](https://github.com/ThalynnX/wifidog-auth-server/releases/tag/v2.0.3)

| 资产 | 内容 |
|---|---|
| `wifidog-portal-v2.0.3-full.tar.gz` | **完整源码包**：服务端代码 + 部署脚本 + 全部文档 + 数据库结构 + 验收脚本 + 路由器包 |
| `wifidog-portal-v2.0.3-server.tar.gz` | 仅服务端源码、部署文档、数据库结构与验收脚本（不含路由器二进制包） |
| `RELEASE-NOTES-v2.0.3.md` | 本版本更新内容、部署顺序与验收边界 |
| `wifidog-server-2.0.3-linux-x86_64.tar.gz` | Nuitka 编译交付包：不含 `.py` 源码，含运行所需资源 |
| `wifidog-portal-v2.0.3-offline-x86_64.tar.gz` | 离线部署包：内置 Python 运行时、wheels、Redis 与安装脚本 |
| `apfree-wifidog-*.apk` | 路由器 wifidogx 主程序（沿用已验证版本） |
| `luci-app-apfree-wifidog-*.apk` | LuCI 配置界面（沿用已验证版本） |
| `luci-i18n-apfree-wifidog-zh-cn-*.apk` | 中文语言包（沿用已验证版本） |
| `apfree-wifidog-libs-aarch64.tar.gz` | 依赖库（沿用已验证版本） |
| `apfree-wifidog-luci-full-aarch64.tar.gz` | 含 wifidogx/wdctlx/init.d/配置模板的全量包（沿用已验证版本） |
| `SHA256SUMS.txt` | 本 Release 全部下载资产的 SHA256 校验和 |

> 本次未单独发布 `acceptance` 打包资产；接口/测试结果与已知验收边界见 `CHANGELOG.md` 和 `RELEASE-NOTES-v2.0.3.md`。

---

## License

[MIT](LICENSE) © 2026 WiFiDog Auth Server contributors
