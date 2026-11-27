#!/usr/bin/env bash
# SQLite 在线备份：使用 .backup 保证 WAL 一致性，不直接 cp 正在写入的 db。
# 默认备份到 <项目>/backups/YYYYMMDDHHMMSS/wifidog.db，权限 600。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${WIFIDOG_DB_PATH:-$ROOT/data/wifidog.db}"
BACKUP_ROOT="${WIFIDOG_BACKUP_DIR:-$ROOT/backups}"
KEEP="${WIFIDOG_BACKUP_KEEP:-14}"

if [ ! -f "$DB_PATH" ]; then
  echo "数据库不存在: $DB_PATH" >&2
  exit 1
fi
if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "缺少 sqlite3 命令" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"
TMP="$DEST/wifidog.db.tmp"
sqlite3 "$DB_PATH" ".backup '$TMP'"
mv "$TMP" "$DEST/wifidog.db"
chmod 700 "$BACKUP_ROOT" "$DEST"
chmod 600 "$DEST/wifidog.db"
echo "已备份: $DEST/wifidog.db"

# 仅保留最近 KEEP 个备份目录
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | tail -n +$((KEEP + 1)) | cut -d' ' -f2- \
  | while read -r old; do rm -rf -- "$old"; done
