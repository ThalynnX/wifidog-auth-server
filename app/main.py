"""wifidog 认证服务端入口。"""
import logging
import random
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models  # noqa: F401
from .config import (
    DEBUG, DEVICE_OFFLINE_TIMEOUT, LOG_DIR, SESSION_IDLE_TIMEOUT, SITE_NAME,
)
from .database import SessionLocal, init_db
from .models import Admin, Plan
from .routers import admin, alipay_admin, alipay_portal, portal, wifidog, ws
from .security import hash_password
from . import services_auth as svc
from . import redis_bus as ws_bus

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "wifidog.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("wifidog")

BASE_DIR = Path(__file__).resolve().parent


def _seed_defaults():
    """首次启动写入默认管理员与示例套餐。"""
    db: Session = SessionLocal()
    try:
        if db.query(Admin).count() == 0:
            db.add(Admin(username="admin", password_hash=hash_password("admin123"),
                         role="admin", status=1))
            logger.info("已创建默认管理员 admin / admin123（请登录后立即修改密码）")
        if db.query(Plan).count() == 0:
            demo = [
                Plan(name="1小时体验", type="time", price=100, duration_minutes=60,
                     traffic_mb=0, device_limit=1, sort=10, description="适合临时上网"),
                Plan(name="24小时畅享", type="time", price=500, duration_minutes=1440,
                     traffic_mb=0, device_limit=2, sort=9, description="单日不限流量"),
                Plan(name="包月套餐", type="period", price=2980, valid_days=30,
                     traffic_mb=0, device_limit=3, sort=8, description="30天不限时长"),
                Plan(name="10GB流量包", type="flow", price=1980, valid_days=90,
                     traffic_mb=10240, device_limit=2, sort=7, description="90天内有效"),
            ]
            db.add_all(demo)
            logger.info("已写入 4 个示例套餐")
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("初始化默认数据失败: %s", e)
        db.rollback()
    finally:
        db.close()


def _cleanup_worker():
    """后台线程：定期回收超时会话、清理过期记录。
    （多 worker 下会重复执行，但操作均幂等，无害；加随机延迟错开峰值）"""
    time.sleep(random.uniform(0, 10))
    while True:
        time.sleep(30)
        try:
            db = SessionLocal()
            n = svc.cleanup_idle_sessions(db, SESSION_IDLE_TIMEOUT)
            r = svc.cleanup_expired_records(db)
            r["subs"] = svc.expire_subscriptions(db)
            off = svc.mark_offline_devices(db, DEVICE_OFFLINE_TIMEOUT)
            if n or r.get("sessions") or r.get("orders") or off:
                logger.info("清理完成: 超时会话=%s 过期记录=%s 设备离线=%s", n, r, off)
            db.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("cleanup error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_defaults()
    # 多 worker 下 WebSocket 跨进程总线；Redis 不可用时自动退回单进程
    await ws_bus.start(wifidog.ws_bus_deliver, wifidog.ws_bus_ack)
    t = threading.Thread(target=_cleanup_worker, daemon=True)
    t.start()
    logger.info("%s started (run_mode 初始值见后台设置)", SITE_NAME)
    yield
    await ws_bus.stop()
    logger.info("%s stopped", SITE_NAME)


app = FastAPI(
    title=SITE_NAME,
    description="wifidog 认证计费服务端",
    version="2.0.3",
    lifespan=lifespan,
    # API 文档仅在 DEBUG 模式暴露，生产关闭（防接口结构泄露）
    docs_url="/docs" if DEBUG else None,
    redoc_url=None,
)


@app.middleware("http")
async def _strip_wifidog_trailing_slash(request: Request, call_next):
    """apfree-wifidog / wifidog 网关发的请求路径带尾部斜杠（如 /wifidog/ping/?...），
    FastAPI 默认会 307 重定向，但网关的 HTTP 客户端不跟随，导致 'Auth server offline'。
    此中间件把 /wifidog/xxx/ 内部改写为 /wifidog/xxx（不返回 307）。"""
    path = request.url.path
    if (path.startswith("/wifidog/") and path != "/wifidog/"
            and path.endswith("/") and not path.startswith("/wifidog/static")):
        new_path = path.rstrip("/")
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode("utf-8")
    return await call_next(request)


@app.middleware("http")
async def _no_store_dynamic(request: Request, call_next):
    """管理后台/门户/网关接口一律禁用浏览器缓存，避免前端显示旧业务数据。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/admin", "/portal", "/wifidog/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(wifidog.router)
app.include_router(portal.router)
app.include_router(alipay_portal.router)
app.include_router(alipay_admin.router)
app.include_router(admin.router)
app.include_router(ws.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/admin")


@app.get("/healthz")
def healthz():
    """健康检查：同时验证数据库可读，避免“进程活着但数据库不可用”的假健康。"""
    db: Session = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    finally:
        db.close()
    return {"ok": db_ok, "service": "wifidog-auth", "db": db_ok, "redis": ws_bus.redis_ok()}
