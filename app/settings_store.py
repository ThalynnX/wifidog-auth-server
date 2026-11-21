"""运行时设置存取（Setting 表）——后台"系统设置"页的真正后端。

提供后台可改、立即生效的运行时开关：
  run_mode             test / production（核心开关）
  free_trial_minutes   新用户免费体验分钟数
  default_device_limit 默认同时在线设备数
  site_notice          门户公告
读取优先级：Setting 表 > 环境变量初始值。
"""
from . import config
from .models import Setting

# 允许通过后台写入的键（白名单，防止任意 key 注入）
EDITABLE_KEYS = {
    "run_mode", "free_trial_minutes", "default_device_limit",
    "site_notice", "site_name",
}

_INT_KEYS = {"free_trial_minutes", "default_device_limit"}


def get_setting(db, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row and row.value != "" else default


def set_setting(db, key: str, value: str):
    if key not in EDITABLE_KEYS:
        raise ValueError(f"不允许的设置项: {key}")
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        row = Setting(key=key)
        db.add(row)
    row.value = str(value)
    row.updated_at = __import__("datetime").datetime.now()
    db.commit()


def get_run_mode(db) -> str:
    """运行模式开关：test / production。"""
    v = (get_setting(db, "run_mode", "") or "").strip().lower()
    if v in ("test", "production"):
        return v
    return config.RUN_MODE_DEFAULT


def is_test_mode(db) -> bool:
    return get_run_mode(db) == "test"


def get_free_trial_minutes(db) -> int:
    v = get_setting(db, "free_trial_minutes", str(config.FREE_TRIAL_MINUTES))
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return config.FREE_TRIAL_MINUTES


def get_device_limit(db) -> int:
    v = get_setting(db, "default_device_limit", str(config.DEFAULT_DEVICE_LIMIT))
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return config.DEFAULT_DEVICE_LIMIT


def get_site_notice(db) -> str:
    return get_setting(db, "site_notice", "")


def get_site_name(db) -> str:
    v = get_setting(db, "site_name", "")
    return v or config.SITE_NAME


def sms_provider(db) -> str:
    """短信通道由运行模式决定：test 一律 mock；production 用配置的真实通道。"""
    if is_test_mode(db):
        return "mock"
    return config.SMS_PROVIDER if config.sms_ready() else "mock"


def pay_provider(db) -> str:
    """支付渠道由运行模式决定：test 一律 mock；production 用配置的真实通道。"""
    if is_test_mode(db):
        return "mock"
    return config.PAY_PROVIDER if config.pay_ready() else "mock"
