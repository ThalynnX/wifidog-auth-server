#!/usr/bin/env bash
# 全量编译交付构建（Nuitka onedir）。
#
# 产物目录默认: dist/encrypted/wifidog-server-v2.0.3-linux-x86_64/
# 产物内是编译后的二进制 + 运行数据，不包含 app/ 下的 .py 源码。
#
# 构建机必须与目标机同架构、同 glibc 或更老；推荐使用目标机的 Python 3.11。
# 例:
#   PYTHON_BIN=/opt/wifidog/runtime/bin/python3 ./scripts/build_encrypted.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="${WIFIDOG_VERSION:-2.0.3}"
NAME="wifidog-server-${VERSION}-linux-x86_64"
OUT_ROOT="${OUT_ROOT:-$ROOT/dist/encrypted}"
BUILD_ROOT="$OUT_ROOT/$NAME"
DIST_DIR="$BUILD_ROOT/wifidog-server"

if ! "$PYTHON_BIN" -c 'import nuitka' >/dev/null 2>&1; then
  echo "错误: $PYTHON_BIN 没有安装 Nuitka，请先 pip install nuitka" >&2
  exit 1
fi

echo "[1/4] 清理旧产物: $BUILD_ROOT"
rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"

echo "[2/4] Nuitka 编译（onedir，包含模板/静态资源）"
"$PYTHON_BIN" -m nuitka \
  --standalone \
  --output-dir="$BUILD_ROOT" \
  --output-filename=wifidog-server \
  --company-name="WiFiDog" \
  --product-name="WiFiDog Auth Server" \
  --file-version="$VERSION" \
  --product-version="$VERSION" \
  --include-package=app \
  --include-package=uvicorn \
  --include-package=fastapi \
  --include-package=sqlalchemy \
  --include-package=pydantic \
  --include-package=jinja2 \
  --include-package=cryptography \
  --include-package=redis \
  --include-package=bcrypt \
  --include-package=jwt \
  --include-package=multipart \
  --include-package=pymysql \
  --include-data-dir=app/templates=app/templates \
  --include-data-dir=app/static=app/static \
  --nofollow-import-to=tests \
  --nofollow-import-to=tkinter \
  --nofollow-import-to=unittest \
  --assume-yes-for-downloads \
  run_encrypted.py

# Nuitka 的输出目录名通常为 run_encrypted.dist；兼容不同版本。
if [ -d "$BUILD_ROOT/run_encrypted.dist" ]; then
  DIST_DIR="$BUILD_ROOT/run_encrypted.dist"
elif [ -d "$BUILD_ROOT/wifidog-server.dist" ]; then
  DIST_DIR="$BUILD_ROOT/wifidog-server.dist"
fi

if [ ! -x "$DIST_DIR/wifidog-server" ] && [ ! -f "$DIST_DIR/wifidog-server" ]; then
  echo "错误: 未找到编译后的 wifidog-server（$DIST_DIR）" >&2
  exit 1
fi

echo "[3/4] 整理交付目录"
FINAL_DIR="$BUILD_ROOT/wifidog-server"
if [ "$DIST_DIR" != "$FINAL_DIR" ]; then
  rm -rf "$FINAL_DIR"
  mv "$DIST_DIR" "$FINAL_DIR"
  DIST_DIR="$FINAL_DIR"
fi
mkdir -p "$DIST_DIR/data" "$DIST_DIR/logs"
cp -f "$ROOT/.env.example" "$DIST_DIR/.env.example"
cp -f "$ROOT/README.md" "$DIST_DIR/README.md"
if [ -f "$ROOT/LICENSE" ]; then cp -f "$ROOT/LICENSE" "$DIST_DIR/LICENSE" 2>/dev/null || true; fi
cat > "$DIST_DIR/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a
[ -f .env ] && . ./.env
set +a
exec ./wifidog-server
SH
chmod 755 "$DIST_DIR/run.sh"

# 源码泄露检查：编译目录里不应出现 app/*.py 或 run_encrypted.py
if find "$DIST_DIR" -path "$DIST_DIR/data" -prune -o -name '*.py' -print | grep -q .; then
  echo "错误: 交付目录中发现 .py 源码，停止打包：" >&2
  find "$DIST_DIR" -path "$DIST_DIR/data" -prune -o -name '*.py' -print >&2
  exit 1
fi

echo "[4/4] 打包"
TAR="$OUT_ROOT/$NAME.tar.gz"
tar -C "$BUILD_ROOT" -czf "$TAR" "$(basename "$DIST_DIR")"
sha256sum "$TAR" | tee "$TAR.sha256"
echo "OK: $TAR"
