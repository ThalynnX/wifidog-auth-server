# 从 0 部署指南（命令级）

面向：一台全新云服务器 + 一个域名，从空系统到「手机连 WiFi 能认证能付费」的完整过程。
预计耗时 30~60 分钟（不含域名备案/证书等待）。

---

## 0. 准备清单

下单/开工前先备齐：

| 项目 | 要求 | 示例 |
|---|---|---|
| 云服务器 | 1C1G 起，推荐 2C2G，公网 IP | 2 核 2G / 40G SSD |
| 操作系统 | Debian 12 / Ubuntu 22.04+ / OpenCloudOS 9 / CentOS Stream 9 | Debian 12 |
| 域名 | 已解析到服务器公网 IP（A 记录） | `portal.example.com` |
| 端口 | 安全组放行 `22`、`80`、`443` | — |
| 短信 | 阿里云短信签名+模板（可后补，先用测试模式） | 签名“XX科技” |
| 支付 | 支付宝应用 / 微信商户号（可后补） | — |
| 路由器 | 见 [ROUTER_SETUP.md](ROUTER_SETUP.md) | 京东云亚瑟 AX1800 |

> **测试模式不需要短信和支付资质**，可以先把整套流程跑通，最后再补资质切生产。

---

## 1. 系统初始化

SSH 登录后先做基础加固与时区：

```bash
# 更新系统
apt update && apt upgrade -y          # Debian/Ubuntu
# dnf update -y                        # OpenCloudOS/CentOS

# 时区与时间同步（订单、套餐到期强依赖系统时间）
timedatectl set-timezone Asia/Shanghai
timedatectl set-ntp true
date

# 常用工具
apt install -y curl wget git vim unzip sqlite3 python3 python3-venv python3-dev nginx
# dnf install -y curl wget git vim unzip sqlite python3 python3-devel nginx
```

**OpenCloudOS / CentOS 注意**：默认可能没有 `python3-venv`，用
`dnf install -y python3-pip` 后 `python3 -m venv` 通常可用；若报 `ensurepip is not available`，
再装 `dnf install -y python3-devel` 或 `python3 -m venv --without-pip venv` 后手动装 pip。

检查版本：

```bash
python3 -V        # 必须 >= 3.10
nginx -v
```

---

## 2. 创建运行用户与目录

不要用 root 跑应用。

```bash
useradd -r -M -s /usr/sbin/nologin wifidog
mkdir -p /opt/wifidog/data /opt/wifidog/logs
```

---

## 3. 获取代码（两种方式）

### 方式 A：Git 克隆（推荐，便于升级）

```bash
git clone https://github.com/ThalynnX/wifidog-auth-server.git /opt/wifidog
cd /opt/wifidog
git log -1 --oneline
```

### 方式 B：Release 源码包（无 git 环境/客户现场）

```bash
cd /tmp
wget https://github.com/ThalynnX/wifidog-auth-server/releases/download/v2.0.3/wifidog-portal-v2.0.3-full.tar.gz
tar -xzf wifidog-portal-v2.0.3-full.tar.gz -C /opt/wifidog --strip-components=1
```

确认目录结构：

```bash
ls /opt/wifidog
# app  deploy  docs  router-packages  requirements.txt  gunicorn_conf.py  .env.example
```

---

## 4. 安装 Python 依赖

```bash
cd /opt/wifidog
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证关键包：

```bash
./venv/bin/python -c "import fastapi, sqlalchemy, gunicorn, jwt, bcrypt; print('ok')"
```

常见问题：

- `bcrypt` 编译失败 → `apt install -y build-essential python3-dev` 后重装；
- 网络慢/超时 → 换源 `-i https://mirrors.aliyun.com/pypi/simple`；
- 公司代理 → `export https_proxy=...`。

---

## 5. 配置 `.env`

```bash
cd /opt/wifidog
cp .env.example .env
```

生成强密钥（不要用示例值）：

```bash
./venv/bin/python -c "import secrets;print('WIFIDOG_SECRET='+secrets.token_urlsafe(48))"
# 把输出追加进 .env（或用编辑器写入）
```

最小可运行配置（测试模式）：

```ini
WIFIDOG_RUN_MODE=test
WIFIDOG_SECRET=<上一步生成的值>
WIFIDOG_BIND=127.0.0.1:8000
WIFIDOG_WORKERS=2
WIFIDOG_SITE_NAME=示例企业 WiFi
WIFIDOG_PUBLIC_BASE=https://portal.example.com
WIFIDOG_SMS_PROVIDER=mock
WIFIDOG_PAY_PROVIDER=mock
```

权限：

```bash
chown -R wifidog:wifidog /opt/wifidog
chmod 600 /opt/wifidog/.env
chmod 750 /opt/wifidog/data /opt/wifidog/logs
```

> `.env` 里有密钥，**不要提交到 Git、不要发给客户**。

---

## 6. 首次启动（前台验证）

先以前台方式跑一次，确认建表、种子数据、页面都正常：

```bash
cd /opt/wifidog
sudo -u wifidog ./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到类似日志即成功：

```
INFO  WiFi 认证服务 started (run_mode 初始值见后台设置)
INFO  Uvicorn running on http://127.0.0.1:8000
```

另开一个 SSH 窗口验证：

```bash
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok",...}

curl -s "http://127.0.0.1:8000/wifidog/ping?gw_id=test&sys_uptime=1&sys_memfree=1&sys_load=0&wifidog_uptime=1"
# Pong
```

按 `Ctrl+C` 停止前台进程，继续配置守护。

> 首次启动会自动：建 15 张表 → 补字段迁移 → 创建管理员 `admin/admin123` → 写入 4 个示例套餐。
>
> 仓库不附带生产库 `data/wifidog.db`。数据库结构见 [`DATABASE.md`](DATABASE.md)，纯 SQLite DDL 见 [`schema.sql`](schema.sql)；空库可手工执行 `sqlite3 data/wifidog.db < docs/schema.sql`，正常部署直接启动应用即可。

---

## 7. systemd 守护

```bash
cp /opt/wifidog/deploy/wifidog.service /etc/systemd/system/wifidog.service
```

如果安装目录不是 `/opt/wifidog`，改这三处：

```bash
sed -i 's#/opt/wifidog#/你的目录#g' /etc/systemd/system/wifidog.service
```

启动与开机自启：

```bash
systemctl daemon-reload
systemctl enable --now wifidog
systemctl status wifidog --no-pager
journalctl -u wifidog -n 30 --no-pager
```

常用命令：

```bash
systemctl restart wifidog     # 改 .env 后必须重启
systemctl stop wifidog
systemctl is-active wifidog
```

> 服务单元已启用 `Restart=always`，崩溃会自动拉起；日志同时写入 `logs/gunicorn_*.log`。

---

## 8. Nginx 反向代理

```bash
cp /opt/wifidog/deploy/nginx_wifidog.conf /etc/nginx/conf.d/wifidog.conf
```

修改两处：

```bash
sed -i 's#portal.example.com#你的域名#g' /etc/nginx/conf.d/wifidog.conf
sed -i 's#/opt/wifidog#/实际目录#g' /etc/nginx/conf.d/wifidog.conf
```

检查并重载：

```bash
nginx -t
systemctl reload nginx
curl -I http://你的域名/healthz
```

要点（模板已内置，改配置时别删）：

| 配置 | 原因 |
|---|---|
| `location /ws/wifidogx` + `Upgrade/Connection` 头 + `proxy_read_timeout 3600s` | 路由器长连接，超时会被反复重连 |
| `proxy_set_header X-Real-IP / X-Forwarded-For / X-Forwarded-Proto` | 后台看到真实 IP，回调生成正确协议 |
| `location /static/` alias | 静态资源不走应用，减轻负载 |
| `client_max_body_size 8m` | 预留上传 |

> 若用 **宝塔面板**：网站 → 添加站点（域名）→ 设置 → 反向代理 → 目标 `http://127.0.0.1:8000`，
> 再在“配置文件”里补 `/ws/wifidogx` 的 Upgrade 段；伪静态不需要。

---

## 9. HTTPS

### 9.1 certbot（推荐）

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d portal.example.com
# 按提示选 2（强制跳转 HTTPS）
certbot renew --dry-run
```

证书自动续期由 `certbot.timer` 负责，确认：

```bash
systemctl list-timers | grep certbot
```

### 9.2 已有证书

把证书放到 `/etc/nginx/ssl/portal.example.com.pem` 与 `.key`，在 server 段加：

```nginx
listen 443 ssl http2;
ssl_certificate     /etc/nginx/ssl/portal.example.com.pem;
ssl_certificate_key /etc/nginx/ssl/portal.example.com.key;
ssl_protocols TLSv1.2 TLSv1.3;
```

然后在 80 的 server 段加 `return 301 https://$host$request_uri;`（保留 `/.well-known/` 路径）。

### 9.3 验证

```bash
curl -I https://portal.example.com/healthz
curl -s https://portal.example.com/wifidog/ping?gw_id=test | head -1
```

> 生产环境必须 HTTPS：微信/支付宝回调、短信审核、以及部分手机 captive portal 检测都要求。

---

## 10. 防火墙与安全组

```bash
# ufw（Debian/Ubuntu）
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable

# firewalld（OpenCloudOS/CentOS）
firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
```

云厂商安全组同样只放行 22/80/443；**不要放行 8000**。

---

## 11. 初始化后台

1. 打开 `https://portal.example.com/admin`
2. 用 `admin / admin123` 登录
3. 立即改密码：右上角/系统设置 → 修改密码
4. 系统设置：
   - 站点名称（显示在门户顶部）
   - 站点公告
   - 免费体验时长（默认 10 分钟，0 关闭）
   - 默认设备数（默认 3）
5. 套餐管理：改价/改时长/上下架，建议先保留 1 个“1 小时体验”用于测试
6. 设备管理：新增网关（`gw_id` 与路由器一致）

---

## 12. 测试模式全流程验收

| 步骤 | 操作 | 期望 |
|---|---|---|
| 1 | 手机连测试 WiFi | 自动弹出门户（或手动打开任意 http 站点） |
| 2 | 输入手机号，点发送验证码 | 页面/接口返回 `dev_code`（测试模式） |
| 3 | 输入验证码登录 | 跳转套餐页 |
| 4 | 选套餐 → 购买 | 跳转模拟支付页 |
| 5 | 点“确认支付” | 提示支付成功，套餐生效 |
| 6 | 点“立即上网” | 能打开网页 |
| 7 | 后台看设备/会话/订单 | 网关在线、会话在列、订单已支付 |
| 8 | 后台把该用户加入黑名单 | 30 秒内被踢下线、无法再上网 |

命令行验收（不依赖路由器）：

```bash
BASE=https://portal.example.com

# 发码
curl -s -X POST $BASE/portal/api/send_code -H 'Content-Type: application/json' \
  -d '{"phone":"<your-phone>"}'

# 登录（code 用返回的 dev_code）
curl -s -X POST $BASE/portal/api/login -H 'Content-Type: application/json' \
  -d '{"phone":"<your-phone>","code":"123456","mac":"aa:bb:cc:dd:ee:01","gw_id":"test","ip":"192.168.6.100"}'

# 网关认证（token 用上一步返回值）
curl -s "$BASE/wifidog/auth?stage=login&gw_id=test&ip=192.168.6.100&mac=aa:bb:cc:dd:ee:01&token=<TOKEN>&incoming=0&outgoing=0"
# Auth: 1
```

---

## 13. 切换生产模式

### 13.1 短信

1. 阿里云短信控制台：签名审核通过、模板审核通过，拿到 `AccessKey/Secret/SignName/TemplateCode`。
2. 写入 `.env`：

```ini
WIFIDOG_SMS_PROVIDER=aliyun
WIFIDOG_SMS_ACCESS_KEY=xxx
WIFIDOG_SMS_SECRET_KEY=xxx
WIFIDOG_SMS_SIGN_NAME=XX科技
WIFIDOG_SMS_TEMPLATE_CODE=SMS_xxxx
```

3. 重启并实测一条真实短信（注意每日限流 20 条/号）。

### 13.2 支付

**支付宝**

```ini
WIFIDOG_PAY_PROVIDER=alipay
WIFIDOG_ALIPAY_APPID=2021xxxx
WIFIDOG_ALIPAY_PRIVATE_KEY=<应用私钥，单行 \n 或 PEM>
WIFIDOG_ALIPAY_PUBLIC_KEY=<支付宝公钥>
WIFIDOG_ALIPAY_NOTIFY_URL=https://portal.example.com/portal/api/pay/notify/alipay
```

**微信 APIv3**

```ini
WIFIDOG_PAY_PROVIDER=wechat
WIFIDOG_WECHAT_APPID=wx...
WIFIDOG_WECHAT_MCHID=16...
WIFIDOG_WECHAT_API_V3=<APIv3 密钥>
WIFIDOG_WECHAT_SERIAL_NO=<商户证书序列号>
WIFIDOG_WECHAT_PRIVATE_KEY=<apiclient_key.pem 内容>
WIFIDOG_WECHAT_NOTIFY_URL=https://portal.example.com/portal/api/pay/notify/wechat
```

回调地址必须在公网 HTTPS 可达；微信商户平台需配置授权域名。

### 13.3 切换

后台 → 系统设置 → 运行模式 `production` → 保存。切换后：

- mock 支付确认接口返回 403；
- 短信必须走真实通道；
- `ping` 不再自动登记新网关（先登记再上线）；
- 建议先在测试网关验证，再切主网关。

---

## 14. 可选：切换 MySQL

SQLite 足够单机使用；多实例/高并发再换 MySQL。

```bash
apt install -y mysql-server    # 或 mariadb-server
mysql -uroot -p <<'SQL'
CREATE DATABASE wifidog DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wifidog'@'127.0.0.1' IDENTIFIED BY '强密码';
GRANT ALL PRIVILEGES ON wifidog.* TO 'wifidog'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
```

`.env`：

```ini
WIFIDOG_DB_URL=mysql+pymysql://wifidog:强密码@127.0.0.1:3306/wifidog?charset=utf8mb4
```

迁移旧数据（停机窗口内执行）：

```bash
systemctl stop wifidog
# 用脚本把 SQLite 各表写入 MySQL（或直接用支持异构的工具）
systemctl start wifidog
```

> 已有 SQLite 数据不建议手工拼 SQL；数据量大时用 `sqlite3 .dump` + 改写语法，或写一次性 Python 脚本按表复制。

---

## 15. 备份与恢复

### 15.1 每日备份脚本

```bash
mkdir -p /backup/wifidog
cat > /usr/local/bin/wifidog-backup.sh <<'SH'
#!/bin/bash
set -e
D=/backup/wifidog
sqlite3 /opt/wifidog/data/wifidog.db ".backup '$D/db-$(date +%F).db'"
tar czf "$D/conf-$(date +%F).tar.gz" -C /opt/wifidog .env
find "$D" -type f -mtime +14 -delete
SH
chmod 700 /usr/local/bin/wifidog-backup.sh
```

cron：

```bash
( crontab -l 2>/dev/null; echo "30 3 * * * /usr/local/bin/wifidog-backup.sh" ) | crontab -
```

### 15.2 恢复

```bash
systemctl stop wifidog
cp /backup/wifidog/db-2026-11-20.db /opt/wifidog/data/wifidog.db
chown wifidog:wifidog /opt/wifidog/data/wifidog.db
systemctl start wifidog
curl -s http://127.0.0.1:8000/healthz
```

---

## 16. 升级与回滚

### 16.1 Git 方式

```bash
cd /opt/wifidog
systemctl stop wifidog
cp -a data data.bak.$(date +%F)
git fetch --tags
git checkout v1.1.2          # 或 git pull --ff-only
./venv/bin/pip install -r requirements.txt
chown -R wifidog:wifidog .
systemctl start wifidog
curl -s http://127.0.0.1:8000/healthz
```

启动时自动建表/补字段，**不需要手工执行 SQL**；仍建议先备份。

### 16.2 包方式

```bash
systemctl stop wifidog
tar -xzf wifidog-portal-vX.Y.Z-full.tar.gz -C /opt/wifidog --strip-components=1
chown -R wifidog:wifidog /opt/wifidog
systemctl start wifidog
```

> 升级只覆盖代码与文档，`data/`、`logs/`、`.env` 不要覆盖。

### 16.3 回滚

```bash
cd /opt/wifidog
systemctl stop wifidog
git checkout <上一个 tag>
systemctl start wifidog
```

数据库若已加字段，旧版本通常可继续读（多余字段被忽略）；如需彻底回滚，恢复备份数据库。

---

## 17. 监控与日志

| 项目 | 命令/位置 |
|---|---|
| 服务状态 | `systemctl status wifidog` |
| 应用日志 | `journalctl -u wifidog -f` |
| Gunicorn 访问日志 | `/opt/wifidog/logs/gunicorn_access.log` |
| Gunicorn 错误日志 | `/opt/wifidog/logs/gunicorn_error.log` |
| Nginx 日志 | `/var/log/nginx/wifidog_*.log` |
| 健康检查 | `curl -fsS http://127.0.0.1:8000/healthz` |
| 进程与资源 | `ps -o pid,rss,pcpu,cmd -C python3`、`free -m` |

简单的 5 分钟巡检脚本：

```bash
*/5 * * * * curl -fsS http://127.0.0.1:8000/healthz >/dev/null || systemctl restart wifidog
```

> 注意：重启会断开在线会话的实时统计（网关会在下个周期重新上报），非必要不要自动重启；
> 更稳妥是用外部拨测告警。

---

## 18. 部署常见错误

| 报错 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 工作目录不对 | `WorkingDirectory` 必须是项目根，`ExecStart` 用 `-m gunicorn ... app.main:app` |
| `502 Bad Gateway` | 应用没起 / 端口不一致 | `systemctl status wifidog`；`curl 127.0.0.1:8000/healthz`；核对 upstream 端口 |
| `Permission denied: data/wifidog.db` | 目录属主是 root | `chown -R wifidog:wifidog data logs` |
| `sqlite3.OperationalError: attempt to write a readonly database` | SELinux/权限/ProtectSystem | 临时 `setenforce 0` 验证；或给 `ReadWritePaths` 加目录 |
| `413 Request Entity Too Large` | Nginx 限制 | `client_max_body_size` 调大 |
| 登录后台一直 401 | Cookie 被 HttpOnly/SameSite 拦 / 反向代理未传 Host | 检查 `X-Forwarded-Proto`、域名与访问地址一致 |
| 支付回调 404/403 | 回调 URL 错 / 生产模式未配公钥 | 核对 `.env` 的 NOTIFY_URL 与公网可达性 |
| 时间不对导致 token 失效 | 服务器时区/时间漂移 | `timedatectl`、`chronyc tracking` |
| `pip install` 卡住 | 网络问题 | 换国内源/配代理 |
| 服务频繁重启 | 端口占用/配置错误 | `journalctl -u wifidog -n 50`；`ss -lntp | grep 8000` |

---

## 19. 上线检查清单

- [ ] 域名解析生效，HTTPS 证书有效且自动续期
- [ ] 安全组仅放行 22/80/443，8000 未暴露
- [ ] `admin` 密码已修改，`.env` 权限 600
- [ ] `WIFIDOG_RUN_MODE=production`，`WIFIDOG_DEBUG=0`
- [ ] 短信真实可达，支付回调验签通过（用 1 分钱或最小套餐实测）
- [ ] 网关已登记，后台显示在线
- [ ] 手机端完整走通“连 WiFi → 门户 → 登录 → 购买 → 上网”
- [ ] 黑名单/踢线/套餐到期断网验证
- [ ] 每日备份任务已生效，并做过一次恢复演练
- [ ] 监控/告警（Uptime、云监控）已配置
