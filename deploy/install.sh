#!/usr/bin/env bash
# =============================================================
# WiFiDog 认证门户 - 一键安装脚本
# 适用：Debian 11/12、Ubuntu 20.04+、OpenCloudOS 8/9、CentOS Stream 8/9、Rocky/Alma 8/9
# 用法（在仓库根目录执行，root 权限）：
#   bash deploy/install.sh
# 可选环境变量：
#   APP_DIR=/opt/wifidog       安装目录
#   APP_USER=wifidog           运行用户
#   WIFIDOG_PORT=8000          应用监听端口（仅本机）
#   DOMAIN=portal.example.com  站点域名（留空则不做 Nginx 配置）
#   INSTALL_NGINX=1            是否安装并配置 Nginx（0=跳过）
#   PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
# =============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wifidog}"
APP_USER="${APP_USER:-wifidog}"
PORT="${WIFIDOG_PORT:-8000}"
DOMAIN="${DOMAIN:-}"
INSTALL_NGINX="${INSTALL_NGINX:-1}"
PYPI_MIRROR="${PYPI_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"

log() { printf '\033[32m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[33m[警告]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 执行：sudo bash deploy/install.sh"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$SRC_DIR/app/main.py" ] || die "未找到 app/main.py，请在仓库根目录执行本脚本"

# ---------- 1. 系统依赖 ----------
log "检测包管理器"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-dev python3-pip git curl tar nginx >/dev/null
    PM=apt
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3 python3-devel python3-pip git curl tar nginx >/dev/null
    PM=dnf
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q python3 python3-devel python3-pip git curl tar nginx >/dev/null
    PM=yum
else
    die "不支持的发行版：找不到 apt/dnf/yum"
fi
log "系统依赖安装完成（$PM）"

PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' \
    || die "Python 版本为 $PY_VER，需要 3.10 及以上"

# ---------- 2. 运行用户与目录 ----------
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd -r -M -s /usr/sbin/nologin "$APP_USER"
    log "已创建系统用户 $APP_USER"
fi

mkdir -p "$APP_DIR" "$APP_DIR/data" "$APP_DIR/logs"
log "同步代码到 $APP_DIR"
tar -C "$SRC_DIR" \
    --exclude='.git' --exclude='venv' --exclude='__pycache__' \
    --exclude='data/*.db*' --exclude='data/.secret_key' \
    --exclude='logs/*' --exclude='*.pyc' \
    -cf - . | tar -C "$APP_DIR" -xf -

# ---------- 3. 虚拟环境 ----------
log "创建虚拟环境并安装依赖"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -U pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" -i "$PYPI_MIRROR"

# ---------- 4. 配置文件 ----------
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    SECRET="$("$APP_DIR/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))')"
    printf '\n# 安装脚本自动生成\nWIFIDOG_SECRET=%s\n' "$SECRET" >> "$APP_DIR/.env"
    log "已生成 .env（运行模式默认 test）"
else
    log ".env 已存在，保持不变"
fi
sed -i "s#^WIFIDOG_BIND=.*#WIFIDOG_BIND=127.0.0.1:${PORT}#" "$APP_DIR/.env" 2>/dev/null || true
grep -q '^WIFIDOG_BIND=' "$APP_DIR/.env" || echo "WIFIDOG_BIND=127.0.0.1:${PORT}" >> "$APP_DIR/.env"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod 750 "$APP_DIR/data" "$APP_DIR/logs"

# ---------- 5. systemd ----------
log "安装 systemd 服务"
sed -e "s#/opt/wifidog#${APP_DIR}#g" -e "s#^User=.*#User=${APP_USER}#" -e "s#^Group=.*#Group=${APP_USER}#" \
    "$APP_DIR/deploy/wifidog.service" > /etc/systemd/system/wifidog.service
systemctl daemon-reload
systemctl enable wifidog >/dev/null 2>&1
systemctl restart wifidog
sleep 2
systemctl is-active --quiet wifidog || {
    journalctl -u wifidog -n 30 --no-pager
    die "服务启动失败，请查看上面的日志"
}
log "服务已启动：systemctl status wifidog"

# ---------- 6. Nginx ----------
if [ "$INSTALL_NGINX" = "1" ] && [ -n "$DOMAIN" ]; then
    log "配置 Nginx（$DOMAIN）"
    sed -e "s#portal.example.com#${DOMAIN}#g" -e "s#/opt/wifidog#${APP_DIR}#g" \
        "$APP_DIR/deploy/nginx_wifidog.conf" > /etc/nginx/conf.d/wifidog.conf
    if nginx -t 2>/dev/null; then
        systemctl reload nginx
        log "Nginx 已加载，接下来申请证书：certbot --nginx -d $DOMAIN"
    else
        warn "nginx -t 未通过，请检查 /etc/nginx/conf.d/wifidog.conf 后手动 reload"
    fi
else
    warn "未配置 Nginx（DOMAIN 为空或 INSTALL_NGINX=0）。应用仅监听 127.0.0.1:${PORT}"
fi

# ---------- 7. 完成 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

============================================================
 安装完成
------------------------------------------------------------
 安装目录 : $APP_DIR
 运行用户 : $APP_USER
 应用端口 : 127.0.0.1:$PORT
 管理后台 : http://${DOMAIN:-$IP}:${PORT}/admin   (默认 admin / admin123)
 健康检查 : curl http://127.0.0.1:$PORT/healthz
 服务管理 : systemctl {status|restart|stop} wifidog
 日志位置 : $APP_DIR/logs/
------------------------------------------------------------
 下一步：
 1) 登录后台，立即修改 admin 密码
 2) 「设备管理」登记路由器网关 ID
 3) 「套餐管理」调整价格与时长
 4) 路由器按 docs/ROUTER_SETUP.md 配置
 5) 全流程验收后，在「系统设置」切到 production 并配置短信/支付
============================================================
EOF
