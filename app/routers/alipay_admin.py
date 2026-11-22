"""支付宝后台管理接口：状态、查单、关单、原路退款、账单对账、事件日志。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import alipay, services_auth as svc
from ..database import get_db
from ..models import Order, PaymentEvent
from .admin import current_admin

logger = logging.getLogger("wifidog.alipay.admin")
router = APIRouter(prefix="/admin/api/alipay", tags=["admin-alipay"])


def _order(db: Session, oid: int) -> Order:
    order = db.get(Order, oid)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


def _event(db: Session, event_type: str, key: str, order: Order, payload: dict, status: str, note: str = "") -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    db.add(PaymentEvent(
        channel="alipay", event_type=event_type, event_key=key[:160],
        order_no=order.order_no, trade_no=(order.trade_no or "")[:128],
        payload_hash=hashlib.sha256(text.encode()).hexdigest(),
        payload=text[:4000], status=status, note=note[:255],
    ))


@router.get("/status")
def alipay_status(db: Session = Depends(get_db), _adm=Depends(current_admin)):
    data = alipay.status()
    data["public_key_fingerprint"] = alipay.public_key_fingerprint()
    data["paid_orders"] = db.query(Order).filter(Order.pay_type == "alipay", Order.status == 1).count()
    data["refunded_orders"] = db.query(Order).filter(Order.pay_type == "alipay", Order.status == 3).count()
    data["events"] = db.query(PaymentEvent).filter(PaymentEvent.channel == "alipay").count()
    return {"ok": True, **data}


@router.get("/events")
def alipay_events(
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
    _adm=Depends(current_admin),
):
    rows = db.query(PaymentEvent).filter(PaymentEvent.channel == "alipay") \
        .order_by(PaymentEvent.id.desc()).limit(limit).all()
    return {"ok": True, "rows": [{
        "id": e.id, "event_type": e.event_type, "event_key": e.event_key,
        "order_no": e.order_no, "trade_no": e.trade_no, "status": e.status,
        "note": e.note, "created_at": e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
    } for e in rows]}


@router.post("/orders/{oid}/query")
def query_order(oid: int, db: Session = Depends(get_db), _adm=Depends(current_admin)):
    order = _order(db, oid)
    if order.pay_type not in ("alipay", ""):
        return {"ok": False, "msg": "该订单不是支付宝订单"}
    if not alipay.pay_ready():
        return {"ok": False, "msg": "支付宝参数未配置"}
    try:
        node = alipay.query_trade(order.order_no)
    except alipay.AlipayError as exc:
        return {"ok": False, "msg": str(exc)}

    status = str(node.get("trade_status") or "")
    trade_no = str(node.get("trade_no") or "")
    order.alipay_trade_status = status[:32]
    order.last_query_at = datetime.now()
    if trade_no:
        order.trade_no = trade_no[:128]
    db.commit()

    if status in ("TRADE_SUCCESS", "TRADE_FINISHED") and order.status == 0:
        try:
            paid_fen = alipay.fen_from_yuan(node.get("total_amount"))
        except alipay.AlipayError as exc:
            return {"ok": False, "msg": str(exc), "trade_status": status}
        if paid_fen != order.amount:
            svc.write_log(db, "pay", "fail", user_id=order.user_id,
                          reason=f"alipay query amount mismatch {paid_fen} != {order.amount}")
            return {"ok": False, "msg": f"支付宝金额 {paid_fen} 分与订单 {order.amount} 分不一致", "trade_status": status}
        svc.pay_order_success(db, order, pay_type="alipay", trade_no=trade_no)
        try:
            _event(db, "query", f"alipay:query:{trade_no or order.order_no}:{status}", order,
                   node, "paid", "主动查单补单")
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return {"ok": True, "msg": "查单成功，订单已补单开通", "trade_status": status}

    if status == "TRADE_CLOSED" and order.status == 0:
        order.status = 2
        db.commit()
        return {"ok": True, "msg": "支付宝侧已关闭，订单已同步关闭", "trade_status": status}

    return {"ok": True, "msg": f"查单成功，交易状态 {status or '未知'}", "trade_status": status}


@router.post("/orders/{oid}/close")
def close_order(oid: int, db: Session = Depends(get_db), _adm=Depends(current_admin)):
    order = _order(db, oid)
    if order.status != 0:
        return {"ok": False, "msg": "只有待支付订单可以关闭"}
    if not alipay.pay_ready():
        return {"ok": False, "msg": "支付宝参数未配置"}
    try:
        node = alipay.close_trade(order.order_no)
    except alipay.AlipayError as exc:
        # 支付宝侧尚无交易（用户从未打开收银台）时，仍允许关闭本地订单
        if "TRADE_NOT_EXIST" in str(exc) or "交易不存在" in str(exc):
            order.status = 2
            db.commit()
            return {"ok": True, "msg": "支付宝侧无交易，已关闭本地订单"}
        return {"ok": False, "msg": str(exc)}
    order.status = 2
    order.alipay_trade_status = "TRADE_CLOSED"
    db.commit()
    return {"ok": True, "msg": "关单成功", "raw": node}


@router.post("/orders/{oid}/refund")
def refund_order(oid: int, payload: dict = None, db: Session = Depends(get_db), _adm=Depends(current_admin)):
    order = _order(db, oid)
    if order.status == 3:
        return {"ok": False, "msg": "该订单已退款，无需重复操作"}
    if order.status != 1:
        return {"ok": False, "msg": "只有已支付订单可以退款"}
    reason = str((payload or {}).get("reason") or "管理员退款")[:250]
    if order.pay_type in ("", "mock"):
        ok, msg = svc.refund_order(db, order, reason=reason, channel_refunded=True)
        return {"ok": ok, "msg": msg}
    if order.pay_type != "alipay":
        return {"ok": False, "msg": "该订单不是支付宝订单，请在对应渠道后台退款"}
    if not alipay.pay_ready():
        return {"ok": False, "msg": "支付宝参数未配置"}

    refund_no = order.refund_no or ("RF" + order.order_no)
    try:
        node = alipay.refund_trade(order.order_no, order.amount, reason, refund_no)
    except alipay.AlipayError as exc:
        return {"ok": False, "msg": f"支付宝退款失败：{exc}"}

    order.refund_no = refund_no
    db.commit()
    ok, msg = svc.refund_order(db, order, reason=reason, channel_refunded=True)
    if not ok:
        return {"ok": False, "msg": f"支付宝已退款，但本地订单更新失败：{msg}", "raw": node}
    try:
        _event(db, "refund", f"alipay:refund:{refund_no}", order, node, "refunded", reason)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"ok": True, "msg": "支付宝退款成功，套餐已停用", "refund_no": refund_no}


@router.post("/orders/{oid}/refund-query")
def refund_query(oid: int, db: Session = Depends(get_db), _adm=Depends(current_admin)):
    order = _order(db, oid)
    if order.pay_type != "alipay":
        return {"ok": False, "msg": "该订单不是支付宝订单"}
    if not alipay.pay_ready():
        return {"ok": False, "msg": "支付宝参数未配置"}
    refund_no = order.refund_no or ("RF" + order.order_no)
    try:
        node = alipay.refund_query(order.order_no, refund_no)
    except alipay.AlipayError as exc:
        return {"ok": False, "msg": str(exc)}
    return {"ok": True, "refund_no": refund_no, "raw": node}


@router.get("/reconcile")
def reconcile(
    bill_date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    _adm=Depends(current_admin),
):
    """下载支付宝交易账单并与本地订单核对。"""
    try:
        datetime.strptime(bill_date, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "msg": "日期格式应为 YYYY-MM-DD"}
    if not alipay.pay_ready():
        return {"ok": False, "msg": "支付宝参数未配置"}
    try:
        bill = alipay.fetch_bill_rows(bill_date)
    except alipay.AlipayError as exc:
        return {"ok": False, "msg": str(exc)}

    matched = amount_mismatch = missing_local = 0
    details = []
    for row in bill["rows"]:
        out_no = str(row.get("商户订单号") or "").strip()
        trade_no = str(row.get("支付宝交易号") or "").strip()
        status = str(row.get("交易状态") or "").strip()
        amount_raw = str(row.get("金额（元）") or row.get("总金额") or row.get("订单金额") or "").strip()
        order = db.query(Order).filter(Order.order_no == out_no).first() if out_no else None
        if not order:
            missing_local += 1
            if len(details) < 50:
                details.append({"order_no": out_no, "trade_no": trade_no, "status": status,
                                "amount": amount_raw, "issue": "本地无此订单"})
            continue
        try:
            bill_fen = alipay.fen_from_yuan(Decimal(amount_raw)) if amount_raw else order.amount
        except (InvalidOperation, alipay.AlipayError):
            bill_fen = None
        if bill_fen is not None and bill_fen != order.amount:
            amount_mismatch += 1
            if len(details) < 50:
                details.append({"order_no": out_no, "trade_no": trade_no, "status": status,
                                "amount": amount_raw, "local_amount": order.amount, "issue": "金额不一致"})
        else:
            matched += 1
        if order.status == 0 and status in ("交易成功", "TRADE_SUCCESS", "TRADE_FINISHED"):
            if len(details) < 50:
                details.append({"order_no": out_no, "trade_no": trade_no, "status": status,
                                "amount": amount_raw, "issue": "支付宝成功但本地待支付，可在查单补单"})
    return {"ok": True, "bill_date": bill_date, "total": bill["count"], "matched": matched,
            "amount_mismatch": amount_mismatch, "missing_local": missing_local, "details": details}
