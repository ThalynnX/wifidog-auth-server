"""用户设备控制接口测试（隔离临时数据库，不连生产）。

运行：
  python -m unittest tests.test_user_device_control -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="wifidog-user-control-")
os.environ.update({
    "WIFIDOG_DB_URL": f"sqlite:///{TMP}/app.db",
    "WIFIDOG_DB_ECHO": "0",
    "WIFIDOG_SECRET": "test-secret-" + TMP,
    "WIFIDOG_LICENSE_FILE": f"{TMP}/license.json",
    "WIFIDOG_LICENSE_PUBLIC_KEY_FILE": f"{TMP}/public.pem",
    "WIFIDOG_DEPLOYMENT_FILE": f"{TMP}/.deployment_id",
    "WIFIDOG_LICENSE_DOMAIN": "test.example.com",
    "WIFIDOG_MACHINE_CODE": "mc_test",
    "WIFIDOG_MACHINE_SEED_FILE": f"{TMP}/.machine_seed",
    "WIFIDOG_REDIS_URL": "redis://127.0.0.1:6399/0",
})

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Order, Plan, Session as Sess, SmsCode, Subscription, User  # noqa: E402
from app import device_auth, settings_store  # noqa: E402


class UserDeviceControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()
        r = cls.client.post("/admin/api/login", json={"username": "admin", "password": "admin123"})
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def setUp(self) -> None:
        db = SessionLocal()
        try:
            for row in db.query(Sess).all():
                db.delete(row)
            for row in db.query(Subscription).all():
                db.delete(row)
            for row in db.query(Order).all():
                db.delete(row)
            for row in db.query(SmsCode).all():
                db.delete(row)
            for row in db.query(User).all():
                db.delete(row)
            db.commit()

            self.plan = Plan(name="测试月卡", type="period", price=100, valid_days=30, status=1)
            db.add(self.plan)
            db.commit()
            self.user = User(
                phone="13900001111", nickname="设备测试用户", status=1,
                mac="aa:bb:cc:dd:ee:01", mac_updated_at=datetime.now(),
                mac_change_count=0, device_change_limit=3,
                device_change_window_start=datetime.now(),
            )
            db.add(self.user)
            db.commit()
            db.refresh(self.user)
            self.uid = self.user.id
            db.add(Subscription(
                user_id=self.uid, plan_id=self.plan.id, plan_name=self.plan.name,
                start_at=datetime.now(), expire_at=datetime.now() + timedelta(days=30),
                traffic_total_mb=0, status=1, device_limit=0,
            ))
            db.add(Order(
                order_no="TESTORDER001", user_id=self.uid, plan_id=self.plan.id,
                plan_name=self.plan.name, amount=100, status=1,
                pay_type="mock", paid_at=datetime.now(),
            ))
            self.session = Sess(
                token="test-token-001", user_id=self.uid, gw_id="gw-test",
                mac="aa:bb:cc:dd:ee:01", ip="192.168.1.5", status=1,
                start_at=datetime.now(), last_update_at=datetime.now(),
            )
            db.add(self.session)
            db.add(SmsCode(phone=self.user.phone, code="123456", purpose="login",
                           expire_at=datetime.now() + timedelta(minutes=5)))
            db.commit()
        finally:
            db.close()

    def _get_user(self) -> User:
        db = SessionLocal()
        try:
            return db.query(User).filter(User.id == self.uid).first()
        finally:
            db.close()

    def test_detail_and_list(self) -> None:
        r = self.client.get("/admin/api/users")
        self.assertEqual(r.status_code, 200, r.text)
        row = next(x for x in r.json()["rows"] if x["id"] == self.uid)
        self.assertEqual(row["device_limit"], 3)
        self.assertEqual(row["device_used"], 0)
        self.assertEqual(row["device_remaining"], 3)
        self.assertEqual(row["mac_masked"], "aa:bb**:**ee:01")

        r = self.client.get(f"/admin/api/users/{self.uid}")
        self.assertEqual(r.status_code, 200, r.text)
        detail = r.json()
        self.assertEqual(detail["user"]["mac"], "aa:bb:cc:dd:ee:01")
        self.assertEqual(detail["user"]["device_remaining"], 3)
        self.assertEqual(len(detail["sessions"]), 1)

    def test_quota_add_reduce_set_reset(self) -> None:
        r = self.client.post(f"/admin/api/users/{self.uid}/device-quota", json={"delta": 2})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["device_limit"], 5)

        r = self.client.post(f"/admin/api/users/{self.uid}/device-quota", json={"delta": -1})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["device_limit"], 4)

        r = self.client.post(f"/admin/api/users/{self.uid}/device-quota", json={"limit": 0})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["device_remaining"], 0)

        r = self.client.post(f"/admin/api/users/{self.uid}/device-quota", json={"limit": 3, "reset_used": True})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["device_limit"], 3)
        self.assertEqual(r.json()["device_used"], 0)
        self.assertEqual(r.json()["device_remaining"], 3)

    def test_self_rebind_obeys_user_limit(self) -> None:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == self.uid).first()
            for i, mac in enumerate(("aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03", "aa:bb:cc:dd:ee:04")):
                ok, msg = device_auth.rebind(db, u, mac)
                self.assertTrue(ok, msg)
                self.assertEqual(u.mac_change_count, i + 1)
            ok, msg = device_auth.rebind(db, u, "aa:bb:cc:dd:ee:05")
            self.assertFalse(ok, msg)
            self.assertEqual(u.mac_change_count, 3)
        finally:
            db.close()

        r = self.client.post(f"/admin/api/users/{self.uid}/device-quota", json={"delta": 1})
        self.assertEqual(r.json()["device_limit"], 4)

        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == self.uid).first()
            ok, msg = device_auth.rebind(db, u, "aa:bb:cc:dd:ee:05")
            self.assertTrue(ok, msg)
            self.assertEqual(u.mac_change_count, 4)
        finally:
            db.close()

    def test_unbind_and_bind_device(self) -> None:
        r = self.client.post(f"/admin/api/users/{self.uid}/device", json={"mac": "", "kick": True})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["mac"], "")
        self.assertEqual(r.json()["kicked"], 1)
        self.assertEqual(self._get_user().mac, "")

        r = self.client.post(f"/admin/api/users/{self.uid}/device", json={"mac": "00-11-22-33-44-55", "kick": True})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertEqual(r.json()["mac"], "00:11:22:33:44:55")

    def test_delete_keeps_orders_and_queues_kick(self) -> None:
        # 后台删除由前端二次确认弹窗承担；接口按当前产品约定直接执行。
        r = self.client.post(f"/admin/api/users/{self.uid}/delete", json={})
        self.assertTrue(r.json()["ok"], r.text)
        self.assertGreaterEqual(r.json()["kept_orders"], 1)
        self.assertGreaterEqual(r.json()["kick_targets"], 1)

        db = SessionLocal()
        try:
            self.assertIsNone(db.query(User).filter(User.id == self.uid).first())
            self.assertIsNone(db.query(Subscription).filter(Subscription.user_id == self.uid).first())
            self.assertIsNone(db.query(Sess).filter(Sess.user_id == self.uid).first())
            self.assertIsNone(db.query(SmsCode).filter(SmsCode.phone == "13900001111").first())
            self.assertEqual(db.query(Order).filter(Order.user_id == self.uid).count(), 1)
        finally:
            db.close()


    def test_no_sms_channel_hides_sms_and_never_echoes_code(self) -> None:
        db = SessionLocal()
        old = settings_store.get_run_mode(db)
        try:
            settings_store.set_setting(db, "run_mode", "production")
            db.commit()
            html = self.client.get("/wifidog/login").text
            self.assertIn("当前未配置短信通道", html)
            self.assertIn('class="tabs hide"', html)
            self.assertIn('<form id="pwdForm" novalidate>', html)
            r = self.client.post("/portal/api/send_code", json={"phone": "13900001111"})
            self.assertFalse(r.json()["ok"])
            self.assertIn("未配置", r.json()["msg"])

            settings_store.set_setting(db, "run_mode", "test")
            db.commit()
            r = self.client.post("/portal/api/send_code", json={"phone": "13900001111"})
            self.assertTrue(r.json()["ok"], r.text)
            self.assertEqual(r.json()["dev_code"], "")
        finally:
            settings_store.set_setting(db, "run_mode", old)
            db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main()
