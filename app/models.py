"""数据模型：用户 / 网关设备 / 套餐 / 订单 / 订阅 / 会话 / 日志 / 管理员 / 配置。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text,
)

from .database import Base


def _now():
    return datetime.now()


class TimestampMixin:
    created_at = Column(DateTime, default=_now, nullable=False)


# ------------------------------------------------------------------ 用户
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    phone = Column(String(20), unique=True, index=True, nullable=True)   # 手机号登录
    username = Column(String(64), unique=True, index=True, nullable=True)  # 账号密码登录
    password_hash = Column(String(255), nullable=True)
    nickname = Column(String(64), default="")
    avatar = Column(String(255), default="")

    status = Column(Integer, default=1, nullable=False)  # 1正常 0禁用
    balance = Column(Integer, default=0, nullable=False)  # 余额(分)

    mac = Column(String(32), default="", nullable=True)  # 已认证绑定的设备 MAC
    mac_updated_at = Column(DateTime, nullable=True)
    mac_change_count = Column(Integer, default=0, nullable=False)

    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(64), default="")
    remark = Column(String(255), default="")

    @property
    def display_name(self) -> str:
        return self.nickname or self.username or self.phone or f"用户{self.id}"


# ------------------------------------------------------------------ 网关设备(AP/路由器)
class Device(Base, TimestampMixin):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    gw_id = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(128), default="")        # 设备名称/热点名
    location = Column(String(255), default="")    # 安装位置(门店)
    secret = Column(String(128), default="")      # 网关共享密钥(可选校验)

    status = Column(Integer, default=1, nullable=False)   # 1启用 0禁用
    online = Column(Integer, default=0, nullable=False)   # 是否在线(由 ping 维护)

    last_seen_at = Column(DateTime, nullable=True)
    last_ip = Column(String(64), default="")
    sys_uptime = Column(BigInteger, default=0)
    sys_memfree = Column(BigInteger, default=0)
    sys_load = Column(Float, default=0)
    wifidog_uptime = Column(BigInteger, default=0)

    remark = Column(String(255), default="")


# ------------------------------------------------------------------ 套餐
class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(20), default="time", nullable=False)  # free/time/flow/period
    price = Column(Integer, default=0, nullable=False)         # 售价(分)
    original_price = Column(Integer, default=0, nullable=False)

    duration_minutes = Column(Integer, default=0)   # 可用时长(分钟)，0=不限
    traffic_mb = Column(Integer, default=0)         # 可用流量(MB)，0=不限
    valid_days = Column(Integer, default=0)         # 自购买起有效天数，0=按 duration 计

    speed_limit_up = Column(Integer, default=0)     # 上行限速 kbps，0=不限
    speed_limit_down = Column(Integer, default=0)   # 下行限速 kbps，0=不限
    device_limit = Column(Integer, default=0)       # 同时在线设备数，0=不限

    status = Column(Integer, default=1, nullable=False)  # 1上架 0下架
    sort = Column(Integer, default=0)
    description = Column(Text, default="")


# ------------------------------------------------------------------ 订单
class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_user", "user_id"),)

    id = Column(Integer, primary_key=True)
    order_no = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    plan_id = Column(Integer, nullable=False)
    plan_name = Column(String(128), default="")
    amount = Column(Integer, default=0, nullable=False)  # 金额(分)

    status = Column(Integer, default=0, nullable=False)  # 0待支付 1已支付 2已关闭 3已退款
    pay_type = Column(String(20), default="")            # wechat/alipay/mock
    trade_no = Column(String(128), default="")
    paid_at = Column(DateTime, nullable=True)
    expire_at = Column(DateTime, nullable=True)
    refund_at = Column(DateTime, nullable=True)          # 退款时间
    refund_reason = Column(String(255), default="")      # 退款原因


# ------------------------------------------------------------------ 用户订阅(生效中的套餐)
class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_sub_user", "user_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    plan_id = Column(Integer, nullable=False)
    plan_name = Column(String(128), default="")
    order_id = Column(Integer, nullable=True)

    start_at = Column(DateTime, nullable=False)
    expire_at = Column(DateTime, nullable=True)   # NULL=长期有效

    traffic_total_mb = Column(Integer, default=0)  # 0=不限
    traffic_used_mb = Column(Float, default=0)

    speed_limit_up = Column(Integer, default=0)
    speed_limit_down = Column(Integer, default=0)
    device_limit = Column(Integer, default=0)

    status = Column(Integer, default=1, nullable=False)  # 1生效 0停用 2已过期 3已用完


# ------------------------------------------------------------------ 在线会话
class Session(Base, TimestampMixin):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sess_token", "token"),
        Index("ix_sess_user", "user_id"),
        Index("ix_sess_mac", "mac"),
    )

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    gw_id = Column(String(64), index=True, default="")
    mac = Column(String(32), default="")
    ip = Column(String(64), default="")

    status = Column(Integer, default=1, nullable=False)  # 1在线 0离线
    start_at = Column(DateTime, default=_now, nullable=False)
    stop_at = Column(DateTime, nullable=True)
    last_update_at = Column(DateTime, default=_now, nullable=False)

    incoming_bytes = Column(BigInteger, default=0)   # 下载
    outgoing_bytes = Column(BigInteger, default=0)   # 上传
    user_agent = Column(String(255), default="")


# ------------------------------------------------------------------ 短信验证码
class SmsCode(Base, TimestampMixin):
    __tablename__ = "sms_codes"

    id = Column(Integer, primary_key=True)
    phone = Column(String(20), index=True, nullable=False)
    code = Column(String(12), nullable=False)
    purpose = Column(String(20), default="login")
    ip = Column(String(64), default="")
    expire_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    attempts = Column(Integer, default=0, nullable=False)  # 校验失败次数，超限作废


# ------------------------------------------------------------------ 认证日志
class AuthLog(Base, TimestampMixin):
    __tablename__ = "auth_logs"
    __table_args__ = (Index("ix_log_created", "created_at"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    phone = Column(String(20), default="")
    gw_id = Column(String(64), index=True, default="")
    mac = Column(String(32), default="")
    ip = Column(String(64), default="")
    action = Column(String(20), default="")     # login/logout/denied/ping
    result = Column(String(20), default="")     # success/fail
    reason = Column(String(255), default="")


# ------------------------------------------------------------------ 管理员
class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="admin")   # admin/operator
    status = Column(Integer, default=1, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(64), default="")


# ------------------------------------------------------------------ 系统配置(KV)
class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=_now, onupdate=_now)


# ------------------------------------------------------------------ MAC 免认证白名单
class MacWhitelist(Base, TimestampMixin):
    __tablename__ = "mac_whitelist"

    id = Column(Integer, primary_key=True)
    mac = Column(String(32), unique=True, index=True, nullable=False)
    note = Column(String(255), default="")
    status = Column(Integer, default=1, nullable=False)


# ------------------------------------------------------------------ MAC 黑名单（强制断网，优先级高于一切白名单）
class MacBlacklist(Base, TimestampMixin):
    __tablename__ = "mac_blacklist"

    id = Column(Integer, primary_key=True)
    mac = Column(String(32), unique=True, index=True, nullable=False)
    note = Column(String(255), default="")


# ------------------------------------------------------------------ IP 免认证白名单（支持单 IP 与 CIDR 网段）
class IpWhitelist(Base, TimestampMixin):
    __tablename__ = "ip_whitelist"

    id = Column(Integer, primary_key=True)
    cidr = Column(String(64), unique=True, index=True, nullable=False)  # 单 IP 或网段，如 192.168.6.100 / 192.168.6.0/24
    note = Column(String(255), default="")
    status = Column(Integer, default=1, nullable=False)
