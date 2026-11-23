"""全量编译交付的入口。

Nuitka 编译后，这个文件不会再以 .py 形式交付；运行目录使用 onedir，
这样 data/ 和 app/templates 可以持久化，不会像 onefile 一样每次解压到临时目录。
"""
from __future__ import annotations

import os
import sys

from app.main import app
import uvicorn


def _bind() -> tuple[str, int]:
    raw = os.getenv("WIFIDOG_BIND", "127.0.0.1:8000").strip()
    host, _, port_text = raw.rpartition(":")
    if not host:
        host = "127.0.0.1"
    try:
        port = int(port_text)
    except ValueError:
        port = 8000
    return host, port


def main() -> int:
    host, port = _bind()
    # 用 app 对象而不是 "app.main:app" 字符串，避免编译后动态导入失败。
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.getenv("WIFIDOG_LOG_LEVEL", "info").lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
