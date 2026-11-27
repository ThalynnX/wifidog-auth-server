"""核心业务：用户开通、订阅判定、会话与流量/时长计费。"""
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import func

from .config import (
    DEVICE_OFFLINE_TIMEOUT, FREE_TRIAL_TRAFFIC_MB, ONLINE_WINDOW_SECONDS, PORTAL_TOKEN_EXPIRE,
    SESSION_IDLE_TIMEOUT,
)
from .models import (
    AdminAudit, AuthLog, Device, IpWhitelist, KickTask, MacBlacklist, MacWhitelist,
    Order, Plan, Session, Subscription, SubscriptionOrder, User,
)
from . import settings_store

logger = logging.getLogger("wifidog.auth")

SESSION_RECORD_RETENTION_DAYS = 7  # 离线会话记录保留天数（防表膨胀）


def new_order_no() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(4).upper()


def get_or_create_user(db, phone: str, ip: str = "", mac: str = "", grant_trial: bool = True) -> User:
    """按手机号取用户，不存在则创建；登录场景可发放免费体验。"""
    user = db.query(User).filter(User.phone == phone).first()
    if user:
        user.last_login_at = datetime.now()
        user.last_login_ip = ip
        db.commit()
        return user

    user = User(phone=phone, nickname="", status=1, last_login_at=datetime.now(), last_login_ip=ip)
    db.add(user)
    try:
        db.commit()
    except Exception:
        # 并发下唯一索引冲突：回滚后重查
        db.rollback()
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            raise
        return user
    db.refresh(user)
    if grant_trial:
        _grant_free_trial(db, user, mac)
    return user


def _get_free_plan(db) -> Plan:
    """获取（或一次性创建）全局唯一的系统免费体验套餐。

    免费体验套餐不对外售卖，但可以参与发放；因此按 type=free 查找，
    不能只看 status=0，否则后台把系统套餐置为上架后会重复创建。
    """
    plan = db.query(Plan).filter(Plan.type == "free").order_by(Plan.id.asc()).first()
    minutes = settings_store.get_free_trial_minutes(db)
    if not plan:
        plan = Plan(
            name="新用户体验", type="free", price=0,
            duration_minutes=minutes, traffic_mb=0,
            speed_limit_up=10000, speed_limit_down=10000, device_limit=1,
            status=1,
            description="系统自带体验：10 分钟，上下行 10 Mbps，超时需充值",
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
    else:
        # 跟随后台可改的体验时长；限速/设备数由系统套餐配置控制。
        plan.duration_minutes = minutes
        db.commit()
    return plan


def _device_trial_used(db, mac: str) -> bool:
    """同一台设备是否已经领过免费体验，防止换手机号重复领取。"""
    m = (mac or "").strip().lower()
    if not m:
        return False
    uids = set()
    for row in db.query(User.id).filter(func.lower(User.mac) == m).all():
        uids.add(row[0])
    for row in db.query(Session.user_id).filter(func.lower(Session.mac) == m).all():
        uids.add(row[0])
    if not uids:
        return False
    return db.query(Subscription.id).filter(
        Subscription.user_id.in_(uids),
        Subscription.order_id.is_(None),
    ).first() is not None


def _grant_free_trial(db, user: User, mac: str = ""):
    """新用户赠送体验套餐；同一设备只能领一次（分钟数 0 表示不赠送）。"""
    if settings_store.get_free_trial_minutes(db) <= 0:
        return
    if not user.phone:
        return
    if db.query(Subscription).filter(Subscription.user_id == user.id).first():
        return
    if _device_trial_used(db, mac):
        logger.info("free trial skipped: device already used, user=%s mac=%s", user.id, mac)
        return
    plan = _get_free_plan(db)
    activate_subscription(db, user, plan, order_id=None)
    logger.info("free trial granted: user=%s minutes=%s speed_down=%s device_limit=%s",
                user.id, plan.duration_minutes, plan.speed_limit_down, plan.device_limit)


def _record_sub_order(db, sub: Subscription, plan: Plan, order_id: Optional[int]) -> None:
    """记录订单对应的权益快照；同一订单重复开通时保持幂等。"""
    if not order_id:
        return
    exists = db.query(SubscriptionOrder).filter(SubscriptionOrder.order_id == order_id).first()
    if exists:
        return
    db.add(SubscriptionOrder(
        order_id=order_id,
        subscription_id=sub.id,
        plan_id=plan.id,
        granted_until=sub.expire_at,
        granted_traffic_mb=sub.traffic_total_mb or 0,
        granted_speed_up=sub.speed_limit_up or 0,
        granted_speed_down=sub.speed_limit_down or 0,
        granted_device_limit=sub.device_limit or 0,
    ))


def activate_subscription(db, user: User, plan: Plan, order_id: Optional[int] = None) -> Subscription:
    """开通/续费套餐：同套餐未过期叠加到期时间，并同步最新限速/设备数。"""
    now = datetime.now()
    start = now
    expire = None
    if plan.valid_days > 0:
        expire = now + timedelta(days=plan.valid_days)
    elif plan.duration_minutes > 0:
        expire = now + timedelta(minutes=plan.duration_minutes)

    if expire:
        exist = (
            db.query(Subscription)
            .filter(Subscription.user_id == user.id, Subscription.plan_id == plan.id,
                    Subscription.status == 1, Subscription.expire_at.isnot(None))
            .order_by(Subscription.expire_at.desc())
            .first()
        )
        if exist and exist.expire_at and exist.expire_at > now:
            exist.expire_at = exist.expire_at + (expire - now)
            if plan.traffic_mb and plan.traffic_mb > 0:
                # 流量包续费：保留已用流量，新增额度叠加到“剩余额度”上，
                # 不能把总额直接覆盖成新套餐额度（否则老用户续费会凭空少流量）。
                remaining = max(0, int(exist.traffic_total_mb or 0) - int(exist.traffic_used_mb or 0))
                exist.traffic_total_mb = remaining + int(plan.traffic_mb)
            else:
                exist.traffic_total_mb = 0
            exist.plan_name = plan.name
            exist.speed_limit_up = plan.speed_limit_up or 0
            exist.speed_limit_down = plan.speed_limit_down or 0
            exist.device_limit = max(0, int(plan.device_limit or 0))
            if exist.status != 1:
                exist.status = 1
                exist.traffic_used_mb = 0
            _record_sub_order(db, exist, plan, order_id)
            db.commit()
            return exist

    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        plan_name=plan.name,
        order_id=order_id,
        start_at=start,
        expire_at=expire,
        traffic_total_mb=plan.traffic_mb,
        traffic_used_mb=0,
        speed_limit_up=plan.speed_limit_up or 0,
        speed_limit_down=plan.speed_limit_down or 0,
        # 0 表示不限设备，不再用系统默认值覆盖套餐语义
        device_limit=max(0, int(plan.device_limit or 0)),
        status=1,
    )
    db.add(sub)
    db.flush()
    _record_sub_order(db, sub, plan, order_id)
    db.commit()
    db.refresh(sub)
    return sub


def sync_plan_subscriptions(db, plan: Plan) -> int:
    """后台改套餐后，把限速/设备数/额度同步到该套餐所有生效订阅。"""
    n = db.query(Subscription).filter(
        Subscription.plan_id == plan.id, Subscription.status == 1
    ).update({
        "plan_name": plan.name,
        "speed_limit_up": plan.speed_limit_up or 0,
        "speed_limit_down": plan.speed_limit_down or 0,
        "device_limit": max(0, int(plan.device_limit or 0)),
        "traffic_total_mb": plan.traffic_mb or 0,
    }, synchronize_session=False)
    db.commit()
    return int(n or 0)


def get_active_subscription(db, user_id: int) -> Optional[Subscription]:
    """返回当前可用订阅；顺带把过期/用完的置为失效。"""
    now = datetime.now()
    subs = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.status == 1)
        .order_by(Subscription.expire_at.is_(None).desc(), Subscription.expire_at.desc())
        .all()
    )
    chosen = None
    for s in subs:
        if s.expire_at and s.expire_at < now:
            s.status = 2
            continue
        if s.traffic_total_mb > 0 and (s.traffic_used_mb or 0) >= s.traffic_total_mb:
            s.status = 3
            continue
        if chosen is None:
            chosen = s
        elif ((s.device_limit or 0), (s.expire_at.timestamp() if s.expire_at else 9e18)) > ((chosen.device_limit or 0), (chosen.expire_at.timestamp() if chosen.expire_at else 9e18)):
            chosen = s
    db.commit()
    return chosen


def is_session_online(sess: Session, now: Optional[datetime] = None) -> bool:
    """会话是否真的在线：status=1 且最近一次网关上报在有效窗口内。
    只看 status 会把网关掉线/路由器重启后的僵尸会话一直算作在线。"""
    if not sess or sess.status != 1 or not sess.last_update_at:
        return False
    now = now or datetime.now()
    return (now - sess.last_update_at) <= timedelta(seconds=ONLINE_WINDOW_SECONDS)


def online_device_count(db, user_id: int, exclude_session_id: int = None, exclude_mac: str = "") -> int:
    """在线设备数；可排除正在判定的会话/同 MAC 旧会话，避免把自己算进配额。"""
    cutoff = datetime.now() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    q = db.query(func.count(func.distinct(Session.mac))).filter(
        Session.user_id == user_id, Session.status == 1,
        Session.last_update_at >= cutoff, Session.mac != ""
    )
    if exclude_session_id:
        q = q.filter(Session.id != exclude_session_id)
    if exclude_mac:
        q = q.filter(Session.mac != (exclude_mac or "").lower())
    return q.scalar() or 0


def gateway_active(db, gw_id: str) -> bool:
    """网关是否在设备表中登记且启用；未知/禁用网关不得认证。"""
    gid = (gw_id or "").strip()
    if not gid:
        return False
    dev = db.query(Device).filter(Device.gw_id == gid).first()
    return bool(dev and dev.status == 1)


def check_access(db, user: User, exclude_session_id: int = None, exclude_mac: str = "") -> Tuple[bool, str, Optional[Subscription]]:
    """判断用户是否允许上网。返回 (是否允许, 原因, 订阅)

    exclude_session_id / exclude_mac：判定时排除当前这条会话（或同 MAC 的旧会话），
    否则限 1 台设备的套餐会把"自己"算作已占用而立刻被踢下线。
    """
    if not user or user.status != 1:
        return False, "账号已被禁用", None
    sub = get_active_subscription(db, user.id)
    if not sub:
        return False, "没有可用套餐，请先购买", None
    limit = sub.device_limit or 0
    used = online_device_count(db, user.id, exclude_session_id=exclude_session_id, exclude_mac=exclude_mac)
    if limit > 0 and used >= limit:
        return False, f"超过同时在线设备数限制({limit})", sub
    return True, "ok", sub


# ------------------------------------------------------------------ 会话
def is_mac_whitelisted(db, mac: str) -> bool:
    if not mac:
        return False
    return db.query(MacWhitelist).filter(
        MacWhitelist.mac == mac.lower(), MacWhitelist.status == 1
    ).first() is not None


def is_ip_whitelisted(db, ip: str) -> bool:
    """IP 免认证白名单：支持单 IP 与 CIDR 网段（如 192.168.6.0/24，用于有线网段整体放行）。"""
    import ipaddress

    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])  # 去掉 IPv6 zone
    except ValueError:
        return False
    rules = db.query(IpWhitelist).filter(IpWhitelist.status == 1).all()
    for r in rules:
        try:
            net = ipaddress.ip_network(r.cidr, strict=False)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            return True
    return False


def ip_whitelist_token(ip: str) -> str:
    """白名单 IP 的固定会话 token（无需登录签发，凭 IP 直接放行）。"""
    return "wlip-" + ip.replace(".", "-").replace(":", "-")


def create_session(db, user: User, gw_id: str, mac: str, ip: str, token: str) -> Session:
    """激活会话。同设备（同 MAC）重新登录时替换旧在线会话，避免配额虚增。"""
    mac = (mac or "").lower()
    sess = db.query(Session).filter(Session.token == token).first()
    if not sess:
        sess = Session(
            token=token, user_id=user.id, gw_id=gw_id, mac=mac, ip=ip,
            status=0, start_at=datetime.now(), last_update_at=datetime.now(),
        )
        db.add(sess)

    # 同一 MAC 的其他在线会话下线（设备重登场景）
    if mac:
        for old in db.query(Session).filter(
            Session.user_id == user.id, Session.status == 1,
            Session.mac == mac, Session.token != token,
        ).all():
            old.status = 0
            old.stop_at = datetime.now()

    sess.gw_id = gw_id
    sess.mac = mac
    sess.ip = ip
    sess.status = 1
    sess.last_update_at = datetime.now()
    db.commit()
    db.refresh(sess)
    return sess


def create_whitelist_session(db, gw_id: str, mac: str, ip: str) -> Session:
    """MAC 白名单设备：以 MAC 生成固定 token 建立会话，纳入统一管理。"""
    token = f"wl-{mac.replace(':', '').replace('-', '')}"
    return create_session_by_token(db, token, user_id=0, gw_id=gw_id, mac=mac, ip=ip)


def create_session_by_token(db, token: str, user_id: int, gw_id: str, mac: str, ip: str) -> Session:
    sess = db.query(Session).filter(Session.token == token).first()
    if not sess:
        sess = Session(
            token=token, user_id=user_id, gw_id=gw_id, mac=(mac or "").lower(), ip=ip,
            status=1, start_at=datetime.now(), last_update_at=datetime.now(),
        )
        db.add(sess)
    else:
        sess.gw_id = gw_id
        sess.ip = ip
        sess.status = 1
        sess.last_update_at = datetime.now()
    db.commit()
    db.refresh(sess)
    return sess


def get_session(db, token: str) -> Optional[Session]:
    return db.query(Session).filter(Session.token == token).first()


def active_session_for_device(db, user_id: int, mac: str) -> Optional[Session]:
    """同一用户、同一 MAC 当前仍在线的会话；用于自动识别时复用 token，避免每次刷新都新建会话。"""
    mac = (mac or "").lower()
    if not user_id or not mac:
        return None
    sess = (
        db.query(Session)
        .filter(Session.user_id == user_id, Session.mac == mac, Session.status == 1)
        .order_by(Session.last_update_at.desc(), Session.id.desc())
        .first()
    )
    if sess and not token_expired(sess):
        return sess
    return None


def token_expired(sess: Session) -> bool:
    """上网 token 是否已过期（自签发起超过有效期）。白名单固定 token 永不过期。"""
    if sess.token.startswith("wl-"):
        return False
    return sess.start_at and (datetime.now() - sess.start_at).total_seconds() > PORTAL_TOKEN_EXPIRE


def close_session(db, token: str):
    s = get_session(db, token)
    if s and s.status == 1:
        s.status = 0
        s.stop_at = datetime.now()
        db.commit()


def close_sessions_for_mac(db, mac: str) -> int:
    """立即把某 MAC 的所有在线会话置为离线（拉黑/踢线时使用）。"""
    mac = (mac or "").lower()
    if not mac:
        return 0
    rows = db.query(Session).filter(Session.mac == mac, Session.status == 1).all()
    now = datetime.now()
    for s in rows:
        s.status = 0
        s.stop_at = now
    if rows:
        db.commit()
    return len(rows)


def update_traffic(db, sess: Session, incoming: int, outgoing: int, sub: Optional[Subscription]):
    """wifidog counters 上报的是自连接以来的累计字节数，取增量计入订阅。

    同时把最近一次上下行速率落到 sessions 表，保证后台/门户读数据库就能拿到
    与网关上报一致的速率，不依赖单进程内存缓存。
    """
    now = datetime.now()
    polled_at = sess.last_update_at or now
    inc_delta = max(0, incoming - (sess.incoming_bytes or 0))
    out_delta = max(0, outgoing - (sess.outgoing_bytes or 0))
    sess.incoming_bytes = incoming
    sess.outgoing_bytes = outgoing
    sess.last_update_at = now

    elapsed = (now - polled_at).total_seconds()
    if elapsed >= 0.5:
        sess.down_mbps = round(inc_delta * 8 / elapsed / 1_000_000, 3)
        sess.up_mbps = round(out_delta * 8 / elapsed / 1_000_000, 3)
        sess.last_rate_at = now

    if sub and sub.traffic_total_mb > 0:
        used_mb = (inc_delta + out_delta) / 1024 / 1024
        sub.traffic_used_mb = (sub.traffic_used_mb or 0) + used_mb
        if sub.traffic_used_mb >= sub.traffic_total_mb:
            sub.status = 3
    db.commit()


def is_mac_blacklisted(db, mac: str) -> bool:
    """MAC 黑名单：优先级高于 IP/MAC 白名单与任何有效订阅，命中一律拒绝上网。"""
    if not mac:
        return False
    return db.query(func.count(MacBlacklist.id)).filter(
        MacBlacklist.mac == mac.lower()
    ).scalar() > 0


def blacklist_mac(db, mac: str, note: str = "") -> bool:
    mac = (mac or "").strip().lower()
    if not mac:
        return False
    if db.query(MacBlacklist).filter(MacBlacklist.mac == mac).first():
        return False
    db.add(MacBlacklist(mac=mac, note=note or ""))
    db.commit()
    return True


def unblacklist_mac(db, mac: str) -> bool:
    mac = (mac or "").strip().lower()
    n = db.query(MacBlacklist).filter(MacBlacklist.mac == mac).delete()
    db.commit()
    return n > 0


def mark_offline_devices(db, timeout: int = DEVICE_OFFLINE_TIMEOUT) -> int:
    """心跳保活的反面：超过 timeout 秒未 ping 的网关标记为离线。"""
    deadline = datetime.now() - timedelta(seconds=timeout)
    n = db.query(Device).filter(
        Device.online == 1,
        (Device.last_seen_at.is_(None)) | (Device.last_seen_at < deadline),
    ).update({"online": 0}, synchronize_session=False)
    if n:
        db.commit()
    return n


def write_log(db, action: str, result: str, user_id=None, phone="", gw_id="", mac="", ip="", reason=""):
    db.add(AuthLog(user_id=user_id, phone=phone, gw_id=gw_id, mac=mac, ip=ip,
                   action=action, result=result, reason=reason))
    db.commit()


def mark_authenticated(db, sess: Session, action: str = "login", user_id=None, phone="",
                       gw_id="", mac="", ip="", reason="") -> bool:
    """一条会话只记一次成功认证，避免门户登录 + 网关 /auth + gwstats 重复计数。"""
    if not sess or getattr(sess, "auth_logged", False):
        return False
    sess.auth_logged = True
    db.add(AuthLog(
        user_id=user_id if user_id is not None else sess.user_id,
        phone=phone or "", gw_id=gw_id or sess.gw_id or "",
        mac=mac or sess.mac or "", ip=ip or sess.ip or "",
        action=action, result="success", reason=reason or "",
    ))
    db.commit()
    return True


def touch_device(db, gw_id: str, ip: str, uptime: int, memfree: int, load: float,
                 wd_uptime: int, auto_register: bool = True) -> bool:
    """ping 时登记网关状态。返回是否登记成功。
    auto_register=False（正式模式）时，未登记的设备不入库，防垃圾数据。"""
    dev = db.query(Device).filter(Device.gw_id == gw_id).first()
    if not dev:
        if not auto_register:
            logger.warning("未登记的网关尝试心跳(已忽略): gw_id=%s ip=%s", gw_id, ip)
            return False
        dev = Device(gw_id=gw_id, name=f"AP-{gw_id[:8]}", status=1)
        db.add(dev)
    dev.online = 1
    dev.last_seen_at = datetime.now()
    dev.last_ip = ip or ""
    dev.sys_uptime = uptime or 0
    dev.sys_memfree = memfree or 0
    dev.sys_load = load or 0
    dev.wifidog_uptime = wd_uptime or 0
    db.commit()
    return True


def cleanup_idle_sessions(db, timeout: int = SESSION_IDLE_TIMEOUT) -> int:
    """把长时间未上报流量的会话标记为离线，回收设备数配额。"""
    deadline = datetime.now() - timedelta(seconds=timeout)
    rows = db.query(Session).filter(Session.status == 1, Session.last_update_at < deadline).all()
    for s in rows:
        s.status = 0
        s.stop_at = datetime.now()
    if rows:
        db.commit()
    return len(rows)


def cleanup_expired_records(db) -> dict:
    """定期清理：离线会话记录、过期待支付订单。"""
    now = datetime.now()
    ret = {"sessions": 0, "orders": 0, "kick_tasks": 0}
    # 1) 超过保留期的离线会话记录
    keep = now - timedelta(days=SESSION_RECORD_RETENTION_DAYS)
    ret["sessions"] = db.query(Session).filter(
        Session.status == 0, Session.stop_at < keep
    ).delete()
    # 2) 超时未支付的订单置为已关闭
    ret["orders"] = db.query(Order).filter(
        Order.status == 0, Order.expire_at.isnot(None), Order.expire_at < now
    ).update({"status": 2}, synchronize_session=False)
    # 3) 已完成踢线任务保留 7 天；失败任务保留 30 天便于排查。
    ret["kick_tasks"] = db.query(KickTask).filter(
        KickTask.status == "done", KickTask.done_at < now - timedelta(days=7)
    ).delete(synchronize_session=False)
    db.commit()
    return ret


def create_order(db, user: User, plan: Plan) -> Order:
    order = Order(
        order_no=new_order_no(),
        user_id=user.id,
        plan_id=plan.id,
        plan_name=plan.name,
        phone_snapshot=(user.phone or ""),
        amount=plan.price,
        status=0,
        expire_at=datetime.now() + timedelta(minutes=30),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def pay_order_success(db, order: Order, pay_type: str = "mock", trade_no: str = ""):
    """支付成功：用原子条件更新抢占订单，再开通权益。

    并发通知/后台查单同时到达时，只有一个请求能把 status 0 -> 1；
    另一个请求看到更新 0 行后直接返回，不会重复叠加订阅。
    """
    if order.status == 1:
        # 已支付且已有权益账本：幂等成功；缺账本则补开一次（上次开通异常）。
        if db.query(SubscriptionOrder).filter(SubscriptionOrder.order_id == order.id).first():
            return True
        plan = db.query(Plan).filter(Plan.id == order.plan_id).first()
        user = db.query(User).filter(User.id == order.user_id).first()
        # 兼容升级前的历史订单：旧版 Subscription.order_id 已指向该订单，
        # 说明权益早已开通过，只补账本快照，不能再叠加一次时长/流量。
        legacy = db.query(Subscription).filter(Subscription.order_id == order.id).first()
        if legacy and plan:
            _record_sub_order(db, legacy, plan, order.id)
            db.commit()
            return True
        if plan and user:
            activate_subscription(db, user, plan, order_id=order.id)
            return True
        return False
    plan = db.query(Plan).filter(Plan.id == order.plan_id).first()
    user = db.query(User).filter(User.id == order.user_id).first()
    if not plan or not user:
        # 套餐被删除或用户不存在：不要把订单标记为已支付，等待人工退款/补单。
        logger.error("pay_order_success cannot activate order=%s plan=%s user=%s",
                     order.id, order.plan_id, order.user_id)
        return False
    updated = db.query(Order).filter(
        Order.id == order.id, Order.status == 0
    ).update({
        "status": 1,
        "pay_type": pay_type,
        "trade_no": trade_no or order.order_no,
        "paid_at": datetime.now(),
    }, synchronize_session=False)
    if not updated:
        db.rollback()
        return False
    paid_at = datetime.now()
    order.status = 1
    order.pay_type = pay_type
    order.trade_no = trade_no or order.order_no
    order.paid_at = paid_at
    db.commit()
    activate_subscription(db, user, plan, order_id=order.id)
    return True


def expire_subscriptions(db) -> dict:
    """定时任务：把已到期 / 流量耗尽的有效订阅置为失效，并让这些用户的会话下线。

    返回的 targets 是需要在网关侧立即踢掉的 (mac, ip, gw_id) 列表；
    仅改数据库不会让网关移除已认证客户端，调用方必须继续下发拒绝指令。
    """
    now = datetime.now()
    ret = {"expired": 0, "exhausted": 0, "sessions": 0, "targets": []}

    expired_uids = [r[0] for r in db.query(Subscription.user_id).filter(
        Subscription.status == 1,
        Subscription.expire_at.isnot(None),
        Subscription.expire_at < now,
    ).all()]
    if expired_uids:
        ret["expired"] = db.query(Subscription).filter(
            Subscription.status == 1,
            Subscription.expire_at.isnot(None),
            Subscription.expire_at < now,
        ).update({"status": 2}, synchronize_session=False)

    exhausted_uids = [r[0] for r in db.query(Subscription.user_id).filter(
        Subscription.status == 1,
        Subscription.traffic_total_mb > 0,
        Subscription.traffic_used_mb >= Subscription.traffic_total_mb,
    ).all()]
    if exhausted_uids:
        ret["exhausted"] = db.query(Subscription).filter(
            Subscription.status == 1,
            Subscription.traffic_total_mb > 0,
            Subscription.traffic_used_mb >= Subscription.traffic_total_mb,
        ).update({"status": 3}, synchronize_session=False)
    db.commit()

    seen = set()
    for uid in set(expired_uids) | set(exhausted_uids):
        user = db.query(User).filter(User.id == uid).first()
        ok, _why, _sub = check_access(db, user)
        if not ok:
            def add_target(mac: str, ip: str, gw_id: str) -> None:
                mac = (mac or "").lower()
                if not mac:
                    return
                key = (mac, ip or "", gw_id or "")
                if key in seen:
                    return
                seen.add(key)
                ret["targets"].append({"mac": mac, "ip": ip or "", "gw_id": gw_id or ""})

            # 不只看 status=1：网关侧客户端可能因为数据库 idle 清理已经 status=0，
            # 但防火墙放行仍然存在，到期时必须按 MAC 再推一次 deny。
            rows = db.query(Session).filter(Session.user_id == uid).order_by(Session.id.desc()).all()
            for s in rows:
                add_target(s.mac or "", s.ip or "", s.gw_id or "")
            if user and user.mac:
                last = rows[0] if rows else None
                add_target(user.mac, last.ip if last else "", last.gw_id if last else "")
            ret["sessions"] += db.query(Session).filter(
                Session.user_id == uid, Session.status == 1
            ).update({"status": 0, "stop_at": now}, synchronize_session=False)

    # 兜底：修复前已经到期、但会话仍 status=1、订阅早已 status=2/3 的历史脏状态。
    # 只靠“本次订阅状态发生变化”的 uid 列表抓不到这些会话。
    stale = db.query(Session).filter(Session.status == 1).all()
    for s in stale:
        need_deny = bool(getattr(s, "revoked", False)) or token_expired(s)
        if not need_deny and s.user_id:
            user = db.query(User).filter(User.id == s.user_id).first()
            if user:
                ok, _why, _sub = check_access(
                    db, user, exclude_session_id=s.id, exclude_mac=s.mac or "")
                need_deny = not ok
        if not need_deny:
            continue
        mac = (s.mac or "").lower()
        if mac:
            key = (mac, s.ip or "", s.gw_id or "")
            if key not in seen:
                seen.add(key)
                ret["targets"].append({"mac": mac, "ip": s.ip or "", "gw_id": s.gw_id or ""})
        s.status = 0
        s.stop_at = now
        ret["sessions"] += 1
    db.commit()
    if ret["targets"]:
        enqueue_kick(db, ret["targets"], reason="expire_or_traffic")
    return ret


def enqueue_kick(db, targets, reason: str = "", user_id: Optional[int] = None,
                 session_id: Optional[int] = None) -> int:
    """把 (mac, ip, gw_id) 目标写入持久化踢线队列，返回新增/更新数量。"""
    n = 0
    now = datetime.now()
    for t in targets or []:
        if isinstance(t, dict):
            mac, ip, gw = t.get("mac"), t.get("ip"), t.get("gw_id")
        else:
            mac, ip, gw = (list(t) + ["", "", ""])[:3]
        mac = (mac or "").strip().lower()
        gw = (gw or "").strip()
        if not mac:
            continue
        row = db.query(KickTask).filter(
            KickTask.mac == mac, KickTask.gw_id == gw,
            KickTask.status.in_(("pending", "processing")),
        ).order_by(KickTask.id.desc()).first()
        if row:
            row.reason = reason or row.reason
            row.ip = ip or row.ip
            row.next_retry_at = now
            n += 1
            continue
        db.add(KickTask(
            user_id=user_id, session_id=session_id, mac=mac, ip=ip or "",
            gw_id=gw, reason=reason or "", status="pending",
            attempts=0, next_retry_at=now,
        ))
        n += 1
    if n:
        db.commit()
    return n


def pending_kick_tasks(db, limit: int = 20) -> list[KickTask]:
    now = datetime.now()
    return (
        db.query(KickTask)
        .filter(KickTask.status.in_(("pending", "processing")),
                KickTask.next_retry_at <= now)
        .order_by(KickTask.id.asc()).limit(limit).all()
    )


def claim_kick_task(db, task_id: int, worker: str = "") -> bool:
    """原子抢占，避免多 worker 同时处理同一个 target。"""
    n = db.query(KickTask).filter(
        KickTask.id == task_id, KickTask.status.in_(("pending", "processing")),
        KickTask.next_retry_at <= datetime.now()
    ).update({
        "status": "processing",
        "attempts": KickTask.attempts + 1,
        # 给处理中的任务 2 分钟超时窗口，worker 崩溃后可被重新领取。
        "next_retry_at": datetime.now() + timedelta(seconds=120),
    }, synchronize_session=False)
    db.commit()
    return bool(n)


def finish_kick_task(db, task_id: int, ok: bool, error: str = "") -> None:
    row = db.query(KickTask).filter(KickTask.id == task_id).first()
    if not row:
        return
    if ok:
        row.status = "done"
        row.done_at = datetime.now()
        row.last_error = ""
    else:
        row.last_error = (error or "")[:250]
        # 先 5 秒，逐步退避到 5 分钟；不设上限，网关恢复后继续补踢。
        row.next_retry_at = datetime.now() + timedelta(seconds=min(300, 5 * max(1, row.attempts)))
        row.status = "pending" if row.attempts < 1000 else "failed"
    db.commit()


def refund_order(db, order: Order, reason: str = "", channel_refunded: bool = False):
    """退款/退单：按订单权益账本回滚，并保证断网目标进入重试队列。"""
    if order.status == 3:
        return False, "该订单已经退款，无需重复操作"
    if order.status != 1:
        return False, "只有已支付的订单可以退款"
    if order.pay_type not in ("", "mock") and not channel_refunded:
        return False, "该订单为真实渠道支付，请先在支付宝/微信商户后台完成退款，再勾选“渠道已退款”登记"

    stopped = 0
    targets = []
    touched_sub_ids = set()
    links = db.query(SubscriptionOrder).filter(
        SubscriptionOrder.order_id == order.id
    ).order_by(SubscriptionOrder.id.asc()).all()
    for link in links:
        sub = db.query(Subscription).filter(Subscription.id == link.subscription_id).first()
        if not sub:
            continue
        touched_sub_ids.add(sub.id)
        later_active = False
        for later in db.query(SubscriptionOrder).filter(
            SubscriptionOrder.subscription_id == sub.id,
            SubscriptionOrder.id > link.id,
        ).order_by(SubscriptionOrder.id.asc()).all():
            later_order = db.query(Order).filter(Order.id == later.order_id).first()
            if later_order and later_order.status == 1:
                later_active = True
                break
        if later_active:
            # 后面还有未退款的续费订单：不能把整条订阅停掉。
            continue
        prev = db.query(SubscriptionOrder).filter(
            SubscriptionOrder.subscription_id == sub.id,
            SubscriptionOrder.id < link.id,
        ).order_by(SubscriptionOrder.id.desc()).first()
        if prev:
            prev_order = db.query(Order).filter(Order.id == prev.order_id).first()
            if prev_order and prev_order.status == 1:
                sub.expire_at = prev.granted_until
                sub.traffic_total_mb = prev.granted_traffic_mb or 0
                sub.speed_limit_up = prev.granted_speed_up or 0
                sub.speed_limit_down = prev.granted_speed_down or 0
                sub.device_limit = prev.granted_device_limit or 0
                if sub.expire_at and sub.expire_at <= datetime.now():
                    sub.status = 2
                elif sub.traffic_total_mb > 0 and (sub.traffic_used_mb or 0) >= sub.traffic_total_mb:
                    sub.status = 3
                else:
                    sub.status = 1
                stopped += 1
                continue
        if sub.status == 1:
            sub.status = 0
            stopped += 1

    # 兼容历史订单：没有 SubscriptionOrder 账本时仍按 Subscription.order_id 停用。
    if not links:
        for sub in db.query(Subscription).filter(Subscription.order_id == order.id).all():
            touched_sub_ids.add(sub.id)
            if sub.status == 1:
                sub.status = 0
                stopped += 1

    # 只有用户名下已没有生效订阅时才断网；否则保留其他套餐的在线会话。
    db.flush()
    active_else = db.query(Subscription).filter(
        Subscription.user_id == order.user_id, Subscription.status == 1
    ).first() is not None
    if not active_else:
        sessions = db.query(Session).filter(Session.user_id == order.user_id).all()
        for s in sessions:
            if s.mac:
                targets.append((s.mac, s.ip, s.gw_id))
            if s.status == 1:
                s.status = 0
                s.stop_at = datetime.now()
                s.revoked = True
        enqueue_kick(db, targets, reason=f"refund:{order.order_no}", user_id=order.user_id)

    order.status = 3
    order.refund_at = datetime.now()
    order.refund_reason = (reason or "")[:250]
    db.commit()
    suffix = "，已加入网关踢线重试队列" if targets else ""
    return True, f"已退款，按订单权益回滚 {stopped} 条订阅{suffix}"
