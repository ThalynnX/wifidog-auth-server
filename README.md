# WiFiDog Auth Server v3

Production WiFiDog captive portal authentication server.

**架构**: Node.js + Express + FreeRADIUS + SQLite (sql.js)

## 功能特性

- WiFiDog 协议完整实现 (`/login`, `/auth`, `/ping`, `/msg`)
- RADIUS 认证后端 (FreeRADIUS)
- Token 生命周期管理 (创建 / 验证 / 过期清理)
- 网关心跳记录与在线状态
- 暴力破解限流保护
- 防开放重定向安全校验
- 移动端适配认证页面

## 项目结构

```
src/
├── config/          # 集中配置
│   ├── index.js
│   └── validate.js
├── core/            # 应用核心
│   ├── app.js       # Express 工厂
│   └── lifecycle.js # 生命周期
├── middleware/       # 中间件
│   ├── security.js
│   ├── rate-limit.js
│   ├── error-handler.js
│   └── request-logger.js
├── routes/          # 路由
│   ├── wifidog.js    # WiFiDog 协议端点
│   ├── portal.js    # 认证门户
│   └── health.js    # 健康检查
├── services/        # 业务服务
│   ├── token-service.js
│   ├── gateway-service.js
│   ├── user-service.js
│   └── radius-service.js
├── db/              # 数据层
│   ├── index.js
│   └── schema.js
├── utils/           # 工具
│   ├── logger.js
│   └── validation.js
├── views/           # EJS 模板
└── server.js        # 启动入口

deploy/
├── systemd/         # systemd 服务
├── router/          # 路由器端配置模板
└── freeradius/      # FreeRADIUS 配置
```

## 模块调用关系

```
server.js
  └─> config/index.js
  └─> db/index.js
       └─> db/schema.js
  └─> core/app.js
       ├─> middleware/security.js
       ├─> middleware/rate-limit.js
       ├─> routes/wifidog.js
       │    ├─> services/token-service.js
       │    └─> services/gateway-service.js
       ├─> routes/portal.js
       │    ├─> services/radius-service.js
       │    ├─> services/token-service.js
       │    └─> services/user-service.js
       └─> routes/health.js
  └─> core/lifecycle.js
       └─> services/token-service.js (定时清理)
```

## 快速部署

### Docker Compose (推荐)

```bash
git clone https://github.com/ThalynnX/wifidog-auth-server.git
cd wifidog-auth-server
cp .env.example .env
# 编辑 .env 修改密码和地址
docker-compose up -d
```

### 云服务器 (1.13.168.245)

```bash
# 安装 Node.js 20+
curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
dnf install -y nodejs

# 部署
cd /opt
git clone https://github.com/ThalynnX/wifidog-auth-server.git
cd wifidog-auth-server
npm ci --omit=dev

# 配置
cp .env.example .env
# 编辑 .env

# 启动
npm start

# 或使用 systemd
cp deploy/systemd/wifidog-auth.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wifidog-auth
```

### 路由器端配置 (OpenWrt)

```bash
# 安装 wifidog
opkg update
opkg install wifidog

# 复制配置
cp deploy/router/wifidog.conf.template /etc/wifidog/wifidog.conf
# 编辑配置，确保 AuthServer 指向云服务器

# 启动
/etc/init.d/wifidog enable
/etc/init.d/wifidog start
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/login` | GET | 网关重定向入口 |
| `/portal` | GET/POST | 认证门户页面 |
| `/auth` | GET | Token 验证 (返回 Auth: 1/0) |
| `/ping` | GET | 网关心跳 (返回 Pong) |
| `/msg` | GET | 消息页面 |
| `/health` | GET | 容器健康检查 |
| `/status` | GET | 运行状态统计 |

## 配置说明

见 `.env.example` 注释。

## License

MIT
