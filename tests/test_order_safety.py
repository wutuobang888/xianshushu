# -*- coding: utf-8 -*-
"""订单安全回归：仅使用独立测试数据库和模拟接口。"""
import json
import sqlite3
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import models
from services import order_service as orders
from services import kongfz_monitor as monitor


class OrderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.uri = f"file:safety-{uuid.uuid4().hex}?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.uri, uri=True)
        self.anchor.executescript(models.SCHEMA)
        models._migrate(self.anchor)
        self.anchor.commit()
        self.db_patch = patch.object(models, "get_db", self.connection)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.anchor.close)
        self.api = Mock()
        self.api.configured = True
        self.api.order_detail.side_effect = lambda no,**kwargs: {"order_no":no,"order_status":12,"refund_status":0}
        self.api_patch = patch.object(orders.xgj, "XgjClient", return_value=self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.add_order(1)

    def connection(self):
        conn = sqlite3.connect(self.uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def add_order(self, oid, status="pending", phone="13800000000"):
        models.execute(
            "INSERT INTO orders(id,order_no,status,sale_price,receiver_json) VALUES(?,?,?,?,?)",
            (oid, f"test-{oid}", status, 20, json.dumps({"receiver_mobile": phone})))

    def order(self, oid=1):
        return models.query("SELECT * FROM orders WHERE id=?", (oid,), one=True)

    def bind(self, no="test-purchase", oid=1, price=6):
        orders.bind_kongfz_order(oid, no, price)

    def cache(self, no="test-purchase", phone="13800000000"):
        models.execute(
            "INSERT INTO kongfz_scanned(kongfz_order_no,recv_phone) VALUES(?,?)",
            (no, phone))

    def test_repeated_binding_does_not_add_cost(self):
        self.bind()
        self.bind()
        self.assertEqual(self.order()["kongfz_price"], 6)
        self.assertEqual(len(models.query("SELECT * FROM kongfz_orders")), 1)

    def test_repeated_binding_after_shipping_is_idempotent(self):
        self.bind()
        orders.mark_shipped(1, "test-waybill", auto_fill_xianyu=False)
        self.bind()
        self.assertEqual(self.order()["status"], "shipped")
        self.assertEqual(self.order()["kongfz_price"], 6)

    def test_purchase_cannot_be_bound_to_another_order(self):
        self.add_order(2)
        self.bind()
        with self.assertRaisesRegex(ValueError, "已关联其他订单"):
            self.bind(oid=2)
        self.assertIsNone(self.order(2)["kongfz_order_no"])

    def test_distinct_purchases_accumulate_cost(self):
        self.bind("a", price=6)
        self.bind("b", price=8)
        self.assertEqual(self.order()["kongfz_price"], 14)
        self.assertEqual(self.order()["profit"], 6)

    def test_binding_rolls_back_all_updates_when_profit_fails(self):
        with patch.object(orders, "_recalc_profit", side_effect=RuntimeError("故障注入")):
            with self.assertRaises(RuntimeError):
                self.bind()
        self.assertEqual(self.order()["status"], "pending")
        self.assertIsNone(self.order()["kongfz_price"])
        self.assertEqual(models.query("SELECT * FROM kongfz_orders"), [])
        self.bind()  # 回滚后连接和事务上下文可继续使用

    def test_empty_and_joined_purchase_numbers_rejected(self):
        for no in ("", " ", "a,b", "a，b"):
            with self.subTest(no=no), self.assertRaises(ValueError):
                self.bind(no)

    def test_terminal_orders_cannot_bind(self):
        for status in ("refunding", "refunded", "closed", "completed"):
            models.execute("UPDATE orders SET status=? WHERE id=1", (status,))
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.bind()

    def test_refunded_closed_refunding_orders_never_call_shipping(self):
        for status in ("refunding", "refunded", "closed"):
            models.execute("UPDATE orders SET status=? WHERE id=1", (status,))
            with self.subTest(status=status), self.assertRaises(ValueError):
                orders.mark_shipped(1, "waybill")
            self.assertEqual(self.order()["status"], status)
        self.api.ship_order.assert_not_called()

    def test_empty_waybill_never_calls_shipping(self):
        with self.assertRaises(ValueError):
            orders.mark_shipped(1, " ")
        self.api.ship_order.assert_not_called()

    def test_missing_scan_blocks_automatic_shipping(self):
        self.bind()
        self.assertIsNone(orders.on_kongfz_shipped("test-purchase", "waybill", "圆通"))
        self.api.ship_order.assert_not_called()
        self.assertIn("缺少", self.order()["alert"])

    def test_missing_or_mismatched_phone_blocks_automatic_shipping(self):
        self.bind()
        self.cache(phone="")
        for phone in ("", "13900000000"):
            models.execute("UPDATE kongfz_scanned SET recv_phone=?", (phone,))
            with self.subTest(phone=phone):
                self.assertIsNone(orders.on_kongfz_shipped("test-purchase", "waybill", "圆通"))
        models.execute("UPDATE orders SET receiver_json='{}' WHERE id=1")
        models.execute("UPDATE kongfz_scanned SET recv_phone='13800000000'")
        self.assertIsNone(orders.on_kongfz_shipped("test-purchase", "waybill", "圆通"))
        self.api.ship_order.assert_not_called()

    def test_valid_phone_allows_shipping(self):
        self.bind()
        self.cache()
        self.assertEqual(orders.on_kongfz_shipped("test-purchase", "waybill", "圆通"), 1)
        self.api.ship_order.assert_called_once()
        self.assertEqual(self.order()["status"], "shipped")

    def test_live_refund_blocks_shipping_even_if_local_status_is_pending(self):
        self.api.order_detail.side_effect = None
        self.api.order_detail.return_value = {"order_no":"test-1","order_status":12,"refund_status":1}
        with self.assertRaisesRegex(ValueError,"退款"):
            orders.mark_shipped(1,"waybill")
        self.api.ship_order.assert_not_called()
        self.assertEqual(self.order()["status"],"pending")

    def test_existing_remote_waybill_is_reconciled_without_resending(self):
        self.api.order_detail.side_effect = None
        self.api.order_detail.return_value = {"order_no":"test-1","order_status":21,"waybill_no":"waybill"}
        orders.mark_shipped(1,"waybill")
        self.api.ship_order.assert_not_called()
        self.assertEqual(self.order()["waybill_no"],"waybill")

    def test_remote_identity_mismatch_never_calls_shipping(self):
        self.api.order_detail.side_effect = None
        self.api.order_detail.return_value = {"order_no":"another-order","order_status":12}
        with self.assertRaisesRegex(ValueError,"身份"):
            orders.mark_shipped(1,"waybill")
        self.api.ship_order.assert_not_called()

    def test_duplicate_legacy_ownership_blocks_shipping(self):
        self.bind()
        self.cache()
        self.add_order(2)
        models.execute("INSERT INTO kongfz_orders(order_id,kongfz_order_no) VALUES(2,'test-purchase')")
        self.assertIsNone(orders.on_kongfz_shipped("test-purchase", "waybill", "圆通"))
        self.api.ship_order.assert_not_called()

    def test_refund_during_remote_call_is_not_overwritten(self):
        self.bind()
        self.api.ship_order.side_effect = lambda *a, **k: models.execute(
            "UPDATE orders SET status='refunded' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "状态已变化"):
            orders.mark_shipped(1, "waybill")
        self.assertEqual(self.order()["status"], "refunded")
        self.assertEqual(models.query("SELECT status FROM kongfz_orders", one=True)["status"], "monitoring")

    def test_second_package_does_not_resend_or_replace_first_waybill(self):
        self.bind("a")
        self.bind("b")
        orders.mark_shipped(1, "first", kongfz_order_no="a")
        orders.mark_shipped(1, "second", kongfz_order_no="b")
        self.api.ship_order.assert_called_once()
        self.assertEqual(self.order()["waybill_no"], "first")
        self.assertEqual([r["waybill_no"] for r in models.query(
            "SELECT waybill_no FROM kongfz_orders ORDER BY id")], ["first", "second"])

    def test_completed_order_records_remaining_package_without_resending(self):
        self.bind()
        models.execute("UPDATE orders SET status='completed' WHERE id=1")
        orders.mark_shipped(1, "last", kongfz_order_no="test-purchase")
        self.api.ship_order.assert_not_called()
        self.assertEqual(self.order()["status"], "completed")

    def test_monitor_excludes_refund_and_closed_orders(self):
        self.bind()
        for status in ("refunding", "refunded", "closed"):
            models.execute("UPDATE orders SET status=? WHERE id=1", (status,))
            self.assertEqual(orders.monitoring_kongfz_orders(), [])

    def test_blocked_shipping_is_not_reported_as_monitor_success(self):
        self.bind()
        with patch.object(monitor.kongfz_bot, "check_order", return_value={
                "found": True,"shipped": True, "waybill_no": "waybill"}), \
                patch.object(monitor, "auto_bind_once", return_value=[]):
            self.assertEqual(monitor.check_all_once(), [])
        self.api.ship_order.assert_not_called()

    def test_parallel_binding_is_serialized(self):
        # 使用真实文件锁验证并发；离线测试文件保留，不触碰业务数据库。
        folder = Path(__file__).resolve().parents[1] / "backups" / "test-artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / f"concurrent-{uuid.uuid4().hex}.db"
        with sqlite3.connect(filename) as conn:
            conn.executescript(models.SCHEMA)
            models._migrate(conn)
            conn.execute("INSERT INTO orders(id,order_no,sale_price) VALUES(1,'offline',20)")
        def disk_connection():
            conn = sqlite3.connect(filename, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        barrier = Barrier(2)
        def run():
            barrier.wait()
            self.bind()
        with patch.object(models, "get_db", disk_connection):
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(lambda _: run(), range(2)))
            self.assertEqual(self.order()["kongfz_price"], 6)
            self.assertEqual(len(models.query("SELECT * FROM kongfz_orders")), 1)


if __name__ == "__main__":
    unittest.main()
