"""数据库引擎与会话管理。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from .config import DB_URL, DB_ECHO, LOG_DIR

_is_sqlite = DB_URL.startswith("sqlite")

if _is_sqlite:
    # check_same_thread=False 允许 FastAPI 多线程复用；WAL 提升并发读写能力
    engine = create_engine(
        DB_URL, echo=DB_ECHO, future=True, connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
else:
    engine = create_engine(
        DB_URL, echo=DB_ECHO, future=True, pool_pre_ping=True, pool_recycle=280, pool_size=10, max_overflow=20
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate():
    """轻量列迁移（幂等）：为已有库补齐后加的字段。"""
    from sqlalchemy import text

    wanted = {
        "sms_codes": {"attempts": "INTEGER NOT NULL DEFAULT 0"},
        "users": {
            "alipay_user_id": "VARCHAR(64)",
            "alipay_nickname": "VARCHAR(128) DEFAULT ''",
            "alipay_avatar": "VARCHAR(255) DEFAULT ''",
            "alipay_bound_at": "DATETIME",
            "device_change_limit": "INTEGER NOT NULL DEFAULT 3",
            "device_change_window_start": "DATETIME",
        },
        "orders": {
            "refund_no": "VARCHAR(64) DEFAULT ''",
            "notify_id": "VARCHAR(128) DEFAULT ''",
            "alipay_trade_status": "VARCHAR(32) DEFAULT ''",
            "last_query_at": "DATETIME",
        },
        "sessions": {
            "down_mbps": "FLOAT DEFAULT 0",
            "up_mbps": "FLOAT DEFAULT 0",
            "last_rate_at": "DATETIME",
            "auth_logged": "INTEGER NOT NULL DEFAULT 0",
            "revoked": "INTEGER NOT NULL DEFAULT 0",
        },
    }
    with engine.connect() as conn:
        for table, cols in wanted.items():
            try:
                if _is_sqlite:
                    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                    existing = {r[1] for r in rows}
                else:
                    rows = conn.exec_driver_sql(
                        f"SELECT COLUMN_NAME FROM information_schema.columns "
                        f"WHERE TABLE_NAME=:t", {"t": table},
                    ).fetchall()
                    existing = {r[0] for r in rows}
                for col, ddl in cols.items():
                    if col not in existing:
                        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                conn.commit()
            except Exception as e:  # noqa: BLE001
                print(f"[migrate] {table} 迁移跳过: {e}")
        # 支付宝用户 ID 唯一索引：SQLite 用部分索引，MySQL 用普通唯一索引（NULL 可重复）
        try:
            if _is_sqlite:
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_alipay_user_id "
                    "ON users(alipay_user_id) WHERE alipay_user_id IS NOT NULL"
                )
            else:
                exists = conn.exec_driver_sql(
                    "SELECT COUNT(1) FROM information_schema.statistics "
                    "WHERE table_schema=DATABASE() AND table_name='users' "
                    "AND index_name='ux_users_alipay_user_id'"
                ).scalar()
                if not exists:
                    conn.exec_driver_sql(
                        "ALTER TABLE users ADD UNIQUE INDEX ux_users_alipay_user_id (alipay_user_id)"
                    )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[migrate] users.alipay_user_id 索引跳过: {e}")


def init_db():
    """建表 + 迁移（幂等）。"""
    from . import models  # noqa: F401  确保模型被注册

    Base.metadata.create_all(bind=engine)
    _migrate()
