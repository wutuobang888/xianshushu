"""同步范围与重复写入回归；独立内存数据库，不触碰业务订单。"""
import json
import os
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import models
from services import kongfz_monitor as monitor, order_service as orders
from bots import kongfz_bot as bot


class SyncEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.uri = f"file:sync-{uuid.uuid4().hex}?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.uri, uri=True)
        self.anchor.executescript(models.SCHEMA)
        models._migrate(self.anchor)
        self.anchor.commit()
        self.db = patch.object(models, "get_db", self.connection)
        self.db.start()
        self.addCleanup(self.db.stop)
        self.addCleanup(self.anchor.close)
        receiver = {"receiver_name": "测试买家", "receiver_mobile": "13800000000",
                    "prov_name": "北京市", "city_name": "北京市", "area_name": "海淀区",
                    "address": "测试路1号"}
        models.execute("INSERT INTO orders(id,order_no,status,sale_price,receiver_json)"
                       " VALUES(1,'sale-test','pending',20,?)", (json.dumps(receiver),))

    def connection(self):
        conn = sqlite3.connect(self.uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def purchase(self, no="purchase-test", status="等待卖家发货", amount=6):
        return {"no": no, "status": status, "title": "测试书", "total": amount,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "receiver": "测试买家，13800000000，北京市海淀区测试路1号"}

    def run_scan(self, rows, **options):
        with patch.object(bot, "list_buyer_orders", return_value={
                "orders": rows, "complete": True, "pages": 1}) as scan:
            result = monitor.auto_bind_once(**options)
        return result, scan

    def test_first_scan_is_full_and_next_scan_is_active(self):
        _, first = self.run_scan([])
        first.assert_called_once_with(active_only=False)
        self.assertTrue(models.get_setting("kongfz_last_full_scan"))
        _, second = self.run_scan([])
        second.assert_called_once_with(active_only=True)

    def test_full_reconciliation_due_after_one_day(self):
        models.set_setting("kongfz_last_full_scan", (datetime.now() - timedelta(days=1, minutes=1)).isoformat())
        _, scan = self.run_scan([])
        scan.assert_called_once_with(active_only=False)

    def test_manual_full_reconciliation_bypasses_interval(self):
        models.set_setting("kongfz_last_full_scan", datetime.now().isoformat())
        _, scan = self.run_scan([], force_full=True)
        scan.assert_called_once_with(active_only=False)

    def test_failed_scan_does_not_bind_or_advance_success_times(self):
        with patch.object(bot, "list_buyer_orders", return_value={
                "orders": [self.purchase()], "complete": False, "error": "翻页失败"}):
            self.assertEqual(monitor.auto_bind_once(), [])
        self.assertEqual(models.query("SELECT * FROM kongfz_orders"), [])
        self.assertEqual(models.query("SELECT * FROM kongfz_scanned"), [])
        self.assertFalse(models.get_setting("kongfz_last_full_scan"))
        self.assertFalse(models.get_setting("kongfz_last_scan"))

    def test_paid_order_is_bound_once_and_cost_is_not_repeated(self):
        paid = self.purchase()
        bound, _ = self.run_scan([paid])
        self.assertEqual(bound, [(paid["no"], 1)])
        again, _ = self.run_scan([paid])
        self.assertEqual(again, [])
        self.assertEqual(len(models.query("SELECT * FROM kongfz_orders")), 1)
        self.assertEqual(models.query("SELECT kongfz_price FROM orders", one=True)["kongfz_price"], 6)

    def test_unpaid_then_paid_order_is_not_missed(self):
        self.run_scan([self.purchase(status="待付款")])
        self.assertEqual(models.query("SELECT * FROM kongfz_orders"), [])
        bound, _ = self.run_scan([self.purchase()])
        self.assertEqual(len(bound), 1)

    def test_unchanged_cache_keeps_timestamp_and_changed_status_updates(self):
        purchase = self.purchase()
        monitor._cache_unbound(purchase)
        models.execute("UPDATE kongfz_scanned SET scanned_at='2000-01-01'")
        monitor._cache_unbound(purchase)
        row = models.query("SELECT * FROM kongfz_scanned", one=True)
        self.assertEqual(row["scanned_at"], "2000-01-01")
        monitor._cache_unbound({**purchase, "status": "待收货"})
        row = models.query("SELECT * FROM kongfz_scanned", one=True)
        self.assertEqual(row["status"], "待收货")
        self.assertNotEqual(row["scanned_at"], "2000-01-01")

    def test_unchanged_sale_does_not_write_or_erase_product_ids(self):
        remote = {"order_no": "sale-test", "pay_amount": 2000, "order_status": 22,
                  "goods": {"title": "测试书"}}
        orders._upsert_order(remote)
        models.execute("UPDATE orders SET status='completed',item_id='saved-item',updated_at='2000-01-01'")
        orders._upsert_order(remote)
        row = models.query("SELECT * FROM orders", one=True)
        self.assertEqual(row["updated_at"], "2000-01-01")
        self.assertEqual(row["item_id"], "saved-item")
        orders._upsert_order({**remote, "pay_amount": 1900})
        row = models.query("SELECT * FROM orders", one=True)
        self.assertEqual(row["sale_price"], 19)
        self.assertNotEqual(row["updated_at"], "2000-01-01")

    def test_completed_sale_keeps_monitoring_remaining_packages(self):
        orders.bind_kongfz_order(1, "package-a", 6)
        orders.bind_kongfz_order(1, "package-b", 8)
        models.execute("UPDATE orders SET status='completed'")
        models.execute("UPDATE kongfz_orders SET status='shipped' WHERE kongfz_order_no='package-a'")
        rows = orders.monitoring_kongfz_orders()
        self.assertEqual([r["kongfz_order_no"] for r in rows], ["package-b"])
        models.execute("UPDATE kongfz_orders SET status='shipped' WHERE kongfz_order_no='package-b'")
        self.assertEqual(orders.monitoring_kongfz_orders(), [])


class TradingFilterTests(unittest.TestCase):
    def test_filter_waits_for_response_and_accepts_hidden_duplicate_label(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
            page = browser.new_page()
            page.route("**/buyer/order/list", lambda r: r.fulfill(json={"status": True}))
            page.route("**/fixture", lambda r: r.fulfill(body="""
                <meta charset="utf-8">
                <div class='tab' onclick="fetch('/buyer/order/list').then(() => {
                  this.classList.add('active');
                  document.querySelector('.table-item').textContent='订单号：12345 待收货';
                })"><span>交易中订单</span><span style='display:none'>交易中订单</span></div>
                <div class='table-item'>订单号：98765 成功完成</div>
                """, content_type="text/html"))
            page.goto("https://offline.invalid/fixture")
            bot._select_trading_orders(page, 5000)
            self.assertIn("待收货", page.locator(".table-item").inner_text())
            bot._select_trading_orders(page, 5000)  # 已选中时不等待不存在的新请求。
            browser.close()

    def test_missing_filter_is_an_error(self):
        from unittest.mock import Mock
        page = Mock()
        page.get_by_text.return_value.count.return_value = 0
        with self.assertRaisesRegex(ValueError, "无法唯一定位"):
            bot._select_trading_orders(page, 5000)


if __name__ == "__main__":
    unittest.main()
