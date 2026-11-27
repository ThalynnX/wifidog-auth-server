#!/usr/bin/env bash
# 收紧生产数据/日志权限：数据库 600、目录 700、日志 640。
# 仅 root 或项目属主执行；幂等。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"
LOGS="$ROOT/logs"
BACKUPS="$ROOT/backups"

mkdir -p "$DATA" "$LOGS" "$BACKUPS"
chmod 700 "$DATA" "$BACKUPS" 2>/dev/null || true
# 日志目录同时供 Nginx(www) 打开文件，不能 700；收紧为 750 root:www。
chmod 750 "$LOGS" 2>/dev/null || true
chown root:www "$LOGS" 2>/dev/null || true
find "$DATA" -maxdepth 1 -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) \
  -exec chmod 600 {} + 2>/dev/null || true
find "$BACKUPS" -mindepth 1 -maxdepth 2 -type f -name '*.db' -exec chmod 600 {} + 2>/dev/null || true
find "$LOGS" -maxdepth 1 -type f -name '*.log' -exec chmod 640 {} + 2>/dev/null || true
echo "已收紧 $ROOT 数据/日志权限"
