# -*- coding: utf-8 -*-
"""挂售及迁移回归：独立内存数据库，所有平台接口均为模拟。"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import models
from services import listing_service as listings, product_service as products
from services import order_service, xgj_client as xgj
from services.money import to_fen


class ListingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.uri = f"file:listings-{uuid4().hex}?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.uri, uri=True)
        self.anchor.executescript(models.SCHEMA)
        models._migrate(self.anchor)
        self.anchor.commit()
        self.db_patch = patch.object(models, "get_db", self.connection)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.anchor.close)
        self.pid = products.save_product({"book_title": "测试书", "isbn": "9780000000001"}, 20)
        self.client = Mock(configured=True)
        self.client.create_product.return_value = {"product_id": "101"}
        self.client.product_detail.return_value = self.detail("a")

    def connection(self):
        conn = sqlite3.connect(self.uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def detail(self, sid, price=2000, item="item-a", stock=1, sold=18):
        return {"price": price, "stock": stock, "sold": sold, "online_time": 100,
                "offline_time": 0, "publish_shop": [{"user_name": sid, "item_id": item}]}

    def payload(self, sid="a", price=2000):
        return {"title": "测试书", "publish_shop": [{"user_name": sid, "price": price}]}

    def publish(self, sid="a"):
        return listings.publish(self.pid, sid, lambda: self.payload(sid), self.client)

    def test_cumulative_sales_with_inventory_still_listed(self):
        d = self.detail("a", sold=1677)
        self.assertEqual(listings.remote_status(d, d["publish_shop"][0]), "listed")

    def test_zero_inventory_and_sales_is_sold(self):
        d = self.detail("a", stock=0)
        self.assertEqual(listings.remote_status(d, d["publish_shop"][0]), "sold")

    def test_offline_time_is_out_of_stock(self):
        d = self.detail("a")
        d["offline_time"] = 200
        self.assertEqual(listings.remote_status(d, d["publish_shop"][0]), "out_of_stock")

    def test_cross_shop_identity_and_price_are_independent(self):
        listings.reconcile(self.pid, "101", self.detail("a"))
        listings.reconcile(self.pid, "102", self.detail("b", price=3550, item="item-b"))
        a, b = listings.get(self.pid, "a"), listings.get(self.pid, "b")
        self.assertEqual((a["remote_product_id"], a["sale_price_fen"]), ("101", 2000))
        self.assertEqual((b["remote_product_id"], b["sale_price_fen"]), ("102", 3550))

    def test_publish_rejection_resumes_without_second_create(self):
        self.client.publish_product.side_effect = xgj.XgjError("拒绝")
        with self.assertRaises(xgj.XgjError):
            self.publish()
        self.assertEqual(listings.get(self.pid, "a")["status"], "created")
        self.client.publish_product.side_effect = None
        self.publish()
        self.assertEqual(self.client.create_product.call_count, 1)
        self.assertEqual(listings.get(self.pid, "a")["status"], "listed")

    def test_uncertain_creation_prevents_duplicate(self):
        self.client.create_product.side_effect = xgj.XgjUncertainError("超时")
        with self.assertRaises(xgj.XgjUncertainError):
            self.publish()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.create_product.call_count, 1)

    def test_uncertain_creation_recovers_by_operation_token(self):
        self.client.create_product.side_effect = xgj.XgjUncertainError("超时")
        with self.assertRaises(xgj.XgjUncertainError):
            self.publish()
        token = json.loads(listings.get(self.pid, "a")["payload_json"])["outer_id"]
        detail = self.detail("a")
        detail["outer_id"] = token
        self.assertEqual(products.reconcile_remote_product("101", detail), self.pid)
        self.assertEqual(listings.get(self.pid, "a")["status"], "listed")
        self.assertEqual(len(models.query("SELECT * FROM products")), 1)

    def test_outer_id_wildcards_do_not_match(self):
        self.publish()
        self.assertIsNone(listings.find_product("999", {"outer_id": "%"}))

    def test_import_identity_lookup_is_locked_and_repeat_is_idempotent(self):
        original = listings.find_product
        def locked_lookup(remote_id, detail):
            self.assertIsNotNone(models._transaction_connection.get())
            return original(remote_id, detail)
        with patch.object(listings, "find_product", side_effect=locked_lookup):
            first = products.reconcile_remote_product("999", self.detail("b"))
            second = products.reconcile_remote_product("999", self.detail("b"))
        self.assertEqual(first, second)
        self.assertEqual(len(models.query("SELECT * FROM products")), 2)

    def test_publish_accepted_without_confirmation_is_pending(self):
        self.client.product_detail.return_value = self.detail("a", item="")
        self.publish()
        self.assertEqual(listings.get(self.pid, "a")["status"], "publish_pending")
        self.assertNotEqual(products.get_product(self.pid)["status"], "listed")

    def test_read_failure_after_publish_does_not_repeat_write(self):
        self.client.product_detail.side_effect = xgj.XgjError("读取失败")
        self.publish()
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.client.publish_product.call_count, 1)

    def test_concurrent_submission_only_creates_once(self):
        started, finish = Event(), Event()
        def create(payload):
            started.set()
            if not finish.wait(5):
                raise AssertionError("测试等待超时")
            return {"product_id": "101"}
        self.client.create_product.side_effect = create
        with ThreadPoolExecutor(2) as executor:
            pending = executor.submit(self.publish)
            self.assertTrue(started.wait(5))
            try:
                with self.assertRaises(ValueError):
                    self.publish()
            finally:
                finish.set()
            pending.result()
        self.assertEqual(self.client.create_product.call_count, 1)

    def test_active_operation_not_overwritten_by_sync(self):
        models.execute("INSERT INTO product_listings(product_id,seller_id,status) VALUES(?,'a','creating')", (self.pid,))
        listings.reconcile(self.pid, "101", self.detail("a"))
        self.assertEqual(listings.get(self.pid, "a")["status"], "creating")

    def test_crashed_operation_recovers_after_lease(self):
        models.execute("INSERT INTO product_listings(product_id,seller_id,status,updated_at)"
                       " VALUES(?,'a','publishing','2000-01-01 00:00:00')", (self.pid,))
        listings.reconcile(self.pid, "101", self.detail("a"))
        self.assertEqual(listings.get(self.pid, "a")["status"], "listed")

    def test_single_shop_down_uses_that_shops_remote_id(self):
        listings.reconcile(self.pid, "101", self.detail("a"))
        listings.reconcile(self.pid, "102", self.detail("b"))
        self.client.product_detail.return_value = self.detail("b")
        listings.down(self.pid, "b", self.client)
        self.client.down_shelf.assert_called_once_with(product_id="102")
        self.assertEqual(listings.get(self.pid, "a")["status"], "listed")

    def test_shared_remote_product_blocks_single_shop_down(self):
        detail = self.detail("a")
        detail["publish_shop"].append({"user_name": "b", "item_id": "item-b"})
        listings.reconcile(self.pid, "101", detail)
        self.client.product_detail.return_value = detail
        with self.assertRaises(ValueError):
            listings.down(self.pid, "a", self.client)
        self.client.down_shelf.assert_not_called()

    def test_submission_token_reuses_product(self):
        token = str(uuid4())
        a = products.save_product({"book_title": "新书"}, 20, submission_token=token)
        b = products.save_product({"book_title": "新书"}, 20, submission_token=token)
        self.assertEqual(a, b)
        self.assertEqual(len(models.query("SELECT * FROM products")), 2)

    def test_submission_token_changed_content_is_rejected(self):
        token = str(uuid4())
        products.save_product({"book_title": "新书"}, 20, submission_token=token)
        with self.assertRaises(ValueError):
            products.save_product({"book_title": "改书"}, 20, submission_token=token)

    def test_draft_image_order_is_saved_and_used_when_publishing(self):
        entry = 'offline-book'
        ids = [models.execute("INSERT INTO image_pool(entry_url,image_url) VALUES(?,?)", (entry, f'https://example.invalid/{i}')) for i in range(2)]
        pid = products.save_product({'book_title': '图片排序', 'kongfz_url': entry}, 20, image_ids=ids[::-1])
        self.assertEqual(json.loads(models.get_setting(f'product_image_order:{pid}')), ids[::-1])
        models.execute("INSERT INTO xianyu_accounts(seller_id,is_active) VALUES('a',1)")
        with patch.object(products.xgj, 'XgjClient', return_value=self.client), \
                patch.object(products, '_prepare_publish_payload', return_value=self.payload()) as prepare:
            products.publish_product(pid, 'a')
            self.assertEqual(prepare.call_args.args[-1], ids[::-1])

    def test_changed_image_order_on_same_submission_is_rejected(self):
        entry = 'offline-book'
        ids = [models.execute("INSERT INTO image_pool(entry_url,image_url) VALUES(?,?)", (entry, f'https://example.invalid/{i}')) for i in range(2)]
        token = str(uuid4())
        book = {'book_title': '图片排序', 'kongfz_url': entry}
        products.save_product(book, 20, submission_token=token, image_ids=ids)
        with self.assertRaises(ValueError):
            products.save_product(book, 20, submission_token=token, image_ids=ids[::-1])

    def test_money_is_integer_fen_and_rejects_invalid_values(self):
        self.assertEqual(to_fen("1.005"), 101)
        self.assertIsNone(to_fen(None, allow_none=True))
        for value in ("NaN", "Infinity", -1):
            with self.assertRaises(ValueError):
                to_fen(value)

    def test_purchase_preserves_each_amount_and_basis(self):
        models.execute("INSERT INTO orders(id,order_no,sale_price,status) VALUES(1,'test',30,'pending')")
        order_service.bind_kongfz_order(1, "purchase-a", 6.5, amount_basis="list_total")
        order_service.bind_kongfz_order(1, "purchase-b", None)
        rows = models.query("SELECT * FROM kongfz_orders ORDER BY id")
        self.assertEqual((rows[0]["paid_amount_fen"], rows[0]["amount_basis"]), (650, "list_total"))
        self.assertIsNone(rows[1]["paid_amount_fen"])

    def test_cost_rounding_accepts_string_amount_and_adds_integer_fen(self):
        models.execute("INSERT INTO orders(id,order_no,sale_price,status) VALUES(1,'test',30,'pending')")
        order_service.bind_kongfz_order(1, "purchase-a", "1.005")
        order_service.bind_kongfz_order(1, "purchase-b", "2.005")
        self.assertEqual(models.query("SELECT kongfz_price FROM orders", one=True)["kongfz_price"], 3.02)
        self.assertEqual([r["paid_amount_fen"] for r in models.query("SELECT paid_amount_fen FROM kongfz_orders ORDER BY id")], [101, 201])

    def test_order_page_shows_individual_amount_and_source(self):
        from flask import Flask
        from routes import order_routes, product_routes, report_routes, template_routes, settings_routes
        app = Flask(__name__, template_folder=str(Path(models.__file__).parent / "templates"))
        app.secret_key = "test"
        for module in (order_routes, product_routes, report_routes, template_routes, settings_routes):
            app.register_blueprint(module.bp)
        models.execute("INSERT INTO orders(id,order_no,sale_price,status) VALUES(1,'test',30,'pending')")
        order_service.bind_kongfz_order(1, "purchase-a", 6.5, amount_basis="list_total")
        order_service.bind_kongfz_order(1, "purchase-b", None)
        response = app.test_client().get("/orders")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("逐笔采购金额与来源", html)
        self.assertIn("列表订单总额", html)
        self.assertIn("金额待核对", html)
        self.assertIn("¥6.50", html)

    def test_order_backfill_uses_seller_identity(self):
        listings.reconcile(self.pid, "101", self.detail("a", item="same"))
        listings.reconcile(self.pid, "102", self.detail("b", item="same"))
        models.execute("INSERT INTO orders(order_no,item_id,seller_id) VALUES('o','same','b')")
        products.backfill_order_product_ids()
        self.assertEqual(models.query("SELECT product_id FROM orders", one=True)["product_id"], "102")

    def test_title_does_not_guess_remote_identity(self):
        listings.reconcile(self.pid, "101", self.detail("a"))
        models.execute("INSERT INTO orders(order_no,product_title,seller_id) VALUES('o','测试书','b')")
        products.backfill_order_product_ids()
        row = models.query("SELECT * FROM orders", one=True)
        self.assertIsNone(row["product_id"])
        self.assertEqual(row["isbn"], "9780000000001")

    def test_invalid_price_does_not_upload_images(self):
        with patch.object(products.image_service, "publish_image_urls") as upload:
            with self.assertRaises(ValueError):
                products._prepare_publish_payload(products.get_product(self.pid), self.client, "a", None, None, -1, None)
            upload.assert_not_called()

    def test_demo_does_not_call_network_or_upload(self):
        models.execute("INSERT INTO xianyu_accounts(seller_id,is_active) VALUES('a',1)")
        self.client.configured = False
        with patch.object(products.config, "DEMO_MODE", True), \
                patch.object(products.xgj, "XgjClient", return_value=self.client), \
                patch.object(products.image_service, "publish_image_urls") as upload:
            products.publish_product(self.pid, "a")
            products.down_shelf(self.pid, "a")
            upload.assert_not_called()
        self.assertFalse(self.client.mock_calls)

    def test_migration_is_idempotent_and_preserves_costs(self):
        models.execute("UPDATE products SET seller_id='a,b',xianyu_product_id='101' WHERE id=?", (self.pid,))
        models.execute("INSERT INTO orders(id,order_no,kongfz_price) VALUES(1,'one',18)")
        models.execute("INSERT INTO orders(id,order_no,kongfz_price) VALUES(2,'two',10)")
        for oid, no in ((1, 'unique'), (1, 'duplicate'), (2, 'duplicate')):
            models.execute("INSERT INTO kongfz_orders(order_id,kongfz_order_no) VALUES(?,?)", (oid, no))
        for no in ('unique', 'duplicate'):
            models.execute("INSERT INTO kongfz_scanned(kongfz_order_no,total) VALUES(?,7.25)", (no,))
        for _ in range(2):
            with self.connection() as conn:
                models._migrate(conn)
        self.assertEqual(len(models.query("SELECT * FROM product_listings")), 2)
        self.assertEqual(models.query("SELECT kongfz_price FROM orders WHERE id=1", one=True)["kongfz_price"], 18)
        self.assertEqual(models.query("SELECT paid_amount_fen FROM kongfz_orders WHERE kongfz_order_no='unique'", one=True)["paid_amount_fen"], 725)
        self.assertTrue(all(r["paid_amount_fen"] is None for r in models.query("SELECT paid_amount_fen FROM kongfz_orders WHERE kongfz_order_no='duplicate'")))

    def test_old_schema_gains_new_fields_without_losing_rows(self):
        old = sqlite3.connect(":memory:")
        self.addCleanup(old.close)
        schema = models.SCHEMA.replace("    payload_json TEXT,\n", "")
        schema = schema.replace("    paid_amount_fen INTEGER CHECK(paid_amount_fen IS NULL OR paid_amount_fen >= 0),\n", "")
        schema = schema.replace("    refund_amount_fen INTEGER CHECK(refund_amount_fen IS NULL OR refund_amount_fen >= 0),\n", "")
        schema = schema.replace("    amount_basis TEXT,\n", "")
        old.executescript(schema)
        old.execute("INSERT INTO orders(id,order_no) VALUES(1,'old')")
        old.execute("INSERT INTO kongfz_orders(order_id,kongfz_order_no) VALUES(1,'old-purchase')")
        models._migrate(old)
        models._migrate(old)
        self.assertEqual(old.execute("SELECT COUNT(*) FROM kongfz_orders").fetchone()[0], 1)
        self.assertIn("payload_json", {r[1] for r in old.execute("PRAGMA table_info(product_listings)")})


class WriteResponseTests(unittest.TestCase):
    def test_unconfirmed_write_results_are_uncertain(self):
        client = xgj.XgjClient(app_key="test", app_secret="test")
        responses = [Mock(status_code=500, json=Mock(return_value={"code": 0})),
                     Mock(status_code=200, json=Mock(side_effect=ValueError)),
                     Mock(status_code=200, json=Mock(return_value=[])),
                     Mock(status_code=200, json=Mock(return_value={"data": {}}))]
        for response in responses:
            with patch.object(xgj.requests, "post", return_value=response) as post:
                with self.assertRaises(xgj.XgjUncertainError):
                    client.call("/api/open/product/create", {})
                self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
