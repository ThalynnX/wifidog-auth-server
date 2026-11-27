#!/usr/bin/env bash
# 收紧生产数据/日志权限：数据库 600、目录 700、日志 640。
# 仅 root 或项目属主执行；幂等。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"
LOGS="$ROOT/logs"
BACKUPS="$ROOT/backups"

mkdir -p "$DATA" "$LOGS" "$BACKUPS"
chmod 700 "$DATA" "$LOGS" "$BACKUPS" 2>/dev/null || true
find "$DATA" -maxdepth 1 -type f \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) \
  -exec chmod 600 {} + 2>/dev/null || true
find "$BACKUPS" -mindepth 1 -maxdepth 2 -type f -name '*.db' -exec chmod 600 {} + 2>/dev/null || true
find "$LOGS" -maxdepth 1 -type f -name '*.log' -exec chmod 640 {} + 2>/dev/null || true
echo "已收紧 $ROOT 数据/日志权限"
