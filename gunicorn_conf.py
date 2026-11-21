"""Gunicorn 配置（环境变量可覆盖，默认适配单机 SQLite 部署）。

关键环境变量：
  WIFIDOG_BIND        监听地址，默认 127.0.0.1:8000（对外由 Nginx 反代）
  WIFIDOG_WORKERS      worker 数，默认 1（SQLite + 内存限流建议 1~2）
  WIFIDOG_LOG_DIR      日志目录，默认 <项目>/logs
  WIFIDOG_LOG_LEVEL    debug/info/warning/error
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.getenv("WIFIDOG_LOG_DIR", os.path.join(BASE_DIR, "logs"))
os.makedirs(LOG_DIR, exist_ok=True)

bind = os.getenv("WIFIDOG_BIND", "127.0.0.1:8000")
workers = int(os.getenv("WIFIDOG_WORKERS", "1"))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = int(os.getenv("WIFIDOG_WORKER_CONNECTIONS", "200"))

max_requests = int(os.getenv("WIFIDOG_MAX_REQUESTS", "5000"))
max_requests_jitter = 500
timeout = int(os.getenv("WIFIDOG_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

accesslog = os.path.join(LOG_DIR, "gunicorn_access.log")
errorlog = os.path.join(LOG_DIR, "gunicorn_error.log")
loglevel = os.getenv("WIFIDOG_LOG_LEVEL", "info")
capture_output = True

# 反向代理场景下取真实客户端 IP
forwarded_allow_ips = os.getenv("WIFIDOG_FORWARDED_ALLOW_IPS", "127.0.0.1")
