"""全局配置：环境变量 > .env 文件；短信/支付密钥均为占位符，正式接入时替换。

运行模式（开关）说明：
  run_mode = test       测试模式：短信走 mock（验证码回显）、支付走 mock、ping 自动登记设备
  run_mode = production 正式模式：短信/支付走配置的真实通道，mock 支付确认关闭
优先级：后台"系统设置"中的 run_mode > 环境变量 WIFIDOG_RUN_MODE > 默认 test
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SECRET = "please-change-this-secret-key"

# ---------------------------------------------------------------- 数据库
# 默认 SQLite(WAL)；大规模部署可切 MySQL：
# WIFIDOG_DB_URL=mysql+pymysql://user:pass@127.0.0.1:3306/wifidog?charset=utf8mb4
DB_URL = os.getenv("WIFIDOG_DB_URL", f"sqlite:///{DATA_DIR}/wifidog.db")
DB_ECHO = os.getenv("WIFIDOG_DB_ECHO", "0") == "1"

# ---------------------------------------------------------------- 安全
# 若未通过环境变量提供 SECRET_KEY，则自动生成并持久化到 data/.secret_key，
# 避免使用公开默认值导致管理员令牌可被伪造（P0-1）。
SECRET_KEY = os.getenv("WIFIDOG_SECRET", "")
if not SECRET_KEY or SECRET_KEY == DEFAULT_SECRET:
    _kf = DATA_DIR / ".secret_key"
    if _kf.exists():
        SECRET_KEY = _kf.read_text(encoding="utf-8").strip()
    if not SECRET_KEY:
        SECRET_KEY = secrets.token_urlsafe(48)
        _kf.write_text(SECRET_KEY, encoding="utf-8")
        try:
            os.chmod(_kf, 0o600)
        except OSError:
            pass
        print("[config] 已自动生成并持久化 SECRET_KEY ->", _kf)

ADMIN_TOKEN_EXPIRE = int(os.getenv("WIFIDOG_ADMIN_TOKEN_EXPIRE", "43200"))   # 12h
# 上网 token 有效期（秒）：过期后网关重新认证将被拒绝，需重新登录获取新 token
PORTAL_TOKEN_EXPIRE = int(os.getenv("WIFIDOG_PORTAL_TOKEN_EXPIRE", "86400"))  # 24h

# ---------------------------------------------------------------- 运行模式（可在后台修改）
RUN_MODE_DEFAULT = os.getenv("WIFIDOG_RUN_MODE", "test").strip().lower()
if RUN_MODE_DEFAULT not in ("test", "production"):
    RUN_MODE_DEFAULT = "test"

# ---------------------------------------------------------------- 服务
SITE_NAME = os.getenv("WIFIDOG_SITE_NAME", "WiFi 认证服务")
PUBLIC_BASE = os.getenv("WIFIDOG_PUBLIC_BASE", "")
GATEWAY_PROTO = os.getenv("WIFIDOG_GATEWAY_PROTO", "http")

# ---------------------------------------------------------------- 认证策略（后台可改，此处为初始值）
FREE_TRIAL_MINUTES = int(os.getenv("WIFIDOG_FREE_TRIAL_MINUTES", "10"))
FREE_TRIAL_TRAFFIC_MB = int(os.getenv("WIFIDOG_FREE_TRIAL_TRAFFIC_MB", "100"))
DEFAULT_DEVICE_LIMIT = int(os.getenv("WIFIDOG_DEVICE_LIMIT", "3"))
SESSION_IDLE_TIMEOUT = int(os.getenv("WIFIDOG_SESSION_IDLE_TIMEOUT", "600"))
# 网关心跳超时（秒）：超过该时间未 ping 的设备标记为离线（网关默认 60s 一次心跳）
DEVICE_OFFLINE_TIMEOUT = int(os.getenv("WIFIDOG_DEVICE_OFFLINE_TIMEOUT", "180"))

# ---------------------------------------------------------------- 短信（占位符，正式接入时替换）
# provider: mock / aliyun / http
SMS_PROVIDER = os.getenv("WIFIDOG_SMS_PROVIDER", "mock")
SMS_ACCESS_KEY = os.getenv("WIFIDOG_SMS_ACCESS_KEY", "<your-sms-access-key>")     # 占位符
SMS_SECRET_KEY = os.getenv("WIFIDOG_SMS_SECRET_KEY", "<your-sms-secret-key>")     # 占位符
SMS_SIGN_NAME = os.getenv("WIFIDOG_SMS_SIGN_NAME", "<your-sms-sign-name>")        # 占位符
SMS_TEMPLATE_CODE = os.getenv("WIFIDOG_SMS_TEMPLATE_CODE", "<your-template-code>")  # 占位符
SMS_CODE_EXPIRE = int(os.getenv("WIFIDOG_SMS_CODE_EXPIRE", "300"))
SMS_DAILY_LIMIT = int(os.getenv("WIFIDOG_SMS_DAILY_LIMIT", "20"))
# 同一 IP 每小时最多发送条数（防换号刷短信）
SMS_IP_HOURLY_LIMIT = int(os.getenv("WIFIDOG_SMS_IP_HOURLY_LIMIT", "10"))

# ---------------------------------------------------------------- 支付（占位符，正式接入时替换）
# provider: mock / alipay / wechat
PAY_PROVIDER = os.getenv("WIFIDOG_PAY_PROVIDER", "mock")
WECHAT_APPID = os.getenv("WIFIDOG_WECHAT_APPID", "<your-wechat-appid>")
WECHAT_MCHID = os.getenv("WIFIDOG_WECHAT_MCHID", "<your-wechat-mchid>")
WECHAT_API_V3 = os.getenv("WIFIDOG_WECHAT_API_V3", "<your-wechat-apiv3-key>")
WECHAT_SERIAL_NO = os.getenv("WIFIDOG_WECHAT_SERIAL_NO", "<your-cert-serial>")
WECHAT_PRIVATE_KEY = os.getenv("WIFIDOG_WECHAT_PRIVATE_KEY", "")  # PEM 原文
WECHAT_NOTIFY_URL = os.getenv("WIFIDOG_WECHAT_NOTIFY_URL", "")

ALIPAY_APPID = os.getenv("WIFIDOG_ALIPAY_APPID", "<your-alipay-appid>")
ALIPAY_PRIVATE_KEY = os.getenv("WIFIDOG_ALIPAY_PRIVATE_KEY", "")  # PEM 原文
ALIPAY_PUBLIC_KEY = os.getenv("WIFIDOG_ALIPAY_PUBLIC_KEY", "")    # 支付宝平台公钥 PEM
ALIPAY_NOTIFY_URL = os.getenv("WIFIDOG_ALIPAY_NOTIFY_URL", "")


def _configured(v: str) -> bool:
    """判断配置项是否已真实填写（非空且非占位符）。"""
    return bool(v) and not (v.startswith("<") and v.endswith(">"))


def sms_ready() -> bool:
    """真实短信通道是否已配置就绪。"""
    if SMS_PROVIDER == "aliyun":
        return _configured(SMS_ACCESS_KEY) and _configured(SMS_SECRET_KEY) \
            and _configured(SMS_SIGN_NAME) and _configured(SMS_TEMPLATE_CODE)
    if SMS_PROVIDER == "http":
        return SMS_SECRET_KEY.startswith("http")
    return False


def pay_ready() -> bool:
    """真实支付通道是否已配置就绪。"""
    if PAY_PROVIDER == "alipay":
        return _configured(ALIPAY_APPID) and ALIPAY_PRIVATE_KEY and ALIPAY_PUBLIC_KEY
    if PAY_PROVIDER == "wechat":
        return _configured(WECHAT_MCHID) and _configured(WECHAT_APPID) \
            and _configured(WECHAT_API_V3) and WECHAT_PRIVATE_KEY and _configured(WECHAT_SERIAL_NO)
    return False


# ---------------------------------------------------------------- 其它
DEBUG = os.getenv("WIFIDOG_DEBUG", "0") == "1"
