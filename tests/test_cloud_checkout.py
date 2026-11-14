"""云端采购隔离回归：独立内存数据库、模拟业务接口和离线真实DOM。"""
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch
import uuid

import config
import models
from services import checkout_task as task
from bots.cloud_checkout import BANNER_JS, GUARD_JS, _eligible
from bots.checkout_address import FIND_JS, fill_address, enable_manual_submit

RECEIVER = dict(receiver_name='测试买家',receiver_mobile='13800000000',prov_name='浙江省',
                city_name='杭州市',area_name='西湖区',town_name='测试街道',address='测试路1号')


class CheckoutTaskTests(unittest.TestCase):
    def setUp(self):
        self.uri = 'file:checkout-' + uuid.uuid4().hex + '?mode=memory&cache=shared'
        self.anchor = sqlite3.connect(self.uri, uri=True)
        self.anchor.executescript(models.SCHEMA)
        models._migrate(self.anchor)
        self.anchor.commit()
        self.patcher = patch.object(models, 'get_db', self.connection)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.anchor.close)
        for oid in (1,2):
            models.execute('INSERT INTO orders(id,order_no,status,receiver_json) VALUES(?,?,?,?)',
                           (oid,'test-'+str(oid),'pending',json.dumps(RECEIVER)))
        self.folder = Path(__file__).resolve().parents[1] / 'backups' / 'test-artifacts' / ('checkout-'+uuid.uuid4().hex)
        self.folder.mkdir(parents=True)

    def connection(self):
        connection = sqlite3.connect(self.uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def active_task(self):
        models.set_setting(task.KEY,json.dumps(dict(id='fixed',order_id=1,order_no='test-1',
                           title='测试图书',state='browsing',receiver=RECEIVER,pid=os.getpid())))

    def test_url_allowlist_rejects_credentials_and_lookalike_domains(self):
        for url in ('http://book.kongfz.com/1/2/','https://book.kongfz.com.evil.test/',
                    'https://user:secret@book.kongfz.com/','https://127.0.0.1/',
                    'https://book.kongfz.com:8080/','javascript:alert(1)'):
            self.assertFalse(task.valid_source_url(url))
        self.assertTrue(task.valid_source_url('https://book.kongfz.com/1/2/'))

    def test_other_order_cannot_replace_active_target_and_double_click_reuses_task(self):
        self.active_task()
        with patch.object(config,'CLOUD_MODE',True), patch.object(config,'DATA_DIR',self.folder), \
                patch.object(task,'_alive',return_value=True), patch.object(task,'fresh_receiver') as fetch, \
                patch.object(task.subprocess,'Popen') as spawn:
            self.assertEqual(task.start(1)['id'],'fixed')
            with self.assertRaisesRegex(ValueError,'已有采购窗口'):
                task.start(2)
            fetch.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual(task.current(private=True)['order_id'],1)

    def test_refunded_order_cannot_start_browser(self):
        models.execute("UPDATE orders SET status='refunded' WHERE id=1")
        with patch.object(config,'CLOUD_MODE',True), patch.object(config,'DATA_DIR',self.folder), \
                patch.object(task,'fresh_receiver') as fetch:
            with self.assertRaisesRegex(ValueError,'仅待采购'):
                task.start(1)
            fetch.assert_not_called()

    def test_changed_address_or_status_invalidates_snapshot(self):
        snapshot = dict(order_id=1,receiver=RECEIVER)
        self.assertTrue(_eligible(snapshot))
        changed = dict(RECEIVER,address='测试路2号')
        models.execute('UPDATE orders SET receiver_json=? WHERE id=1',(json.dumps(changed),))
        self.assertFalse(_eligible(snapshot))
        models.execute("UPDATE orders SET receiver_json=?,status='refunded' WHERE id=1",(json.dumps(RECEIVER),))
        self.assertFalse(_eligible(snapshot))

    def test_public_status_excludes_address_snapshot_and_process_id(self):
        self.active_task()
        result = task.current()
        self.assertNotIn('receiver',result)
        self.assertNotIn('pid',result)

    def test_stale_close_request_does_not_close_new_task(self):
        self.active_task()
        with self.assertRaisesRegex(ValueError,'任务已更换'):
            task.close('previous')
        self.assertEqual(json.loads(models.get_setting(task.KEY))['state'],'browsing')

    def test_fresh_remote_refund_blocks_address_use(self):
        from services import xgj_client
        api = Mock()
        api.order_detail.return_value = dict(order_no='test-1',order_status=12,refund_status=1,**RECEIVER)
        with patch.object(xgj_client,'XgjClient',return_value=api):
            with self.assertRaisesRegex(ValueError,'不是待发货'):
                task.fresh_receiver(dict(id=1,order_no='test-1'))


class CheckoutDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def card(self, address='测试路1号', name='测试买家'):
        self.page.set_content('<ul class="address-list"><li><div class="address-info1"><span>'+name+
            '</span><span class="phone">13800000000</span></div><div class="address-info2">浙江省杭州市西湖区测试街道'+
            '<span class="address-text">'+address+'</span></div>'+ 
            '<button class="address-radio" onclick="window.chosen=true;this.closest(\'li\').classList.add(\'selected\')">选中</button></li></ul>')

    def test_same_phone_different_name_or_door_number_is_not_match(self):
        for address,name in [('测试路2号','测试买家'),('测试路1号楼2室','测试买家'),('测试路1号','小测试买家')]:
            self.card(address,name)
            self.assertEqual(self.page.evaluate(FIND_JS,RECEIVER)['match'],-1)

    def test_verified_address_is_selected_without_submitting(self):
        self.card()
        with patch('bots.checkout_address._fill_remark',return_value=True):
            result = fill_address(self.page,RECEIVER)
        self.assertTrue(result['ok'])
        self.assertTrue(self.page.evaluate('window.chosen'))

    def test_guard_blocks_submit_until_address_verified(self):
        self.page.set_content('<button onclick="window.submitted=true">提交订单</button>')
        self.page.evaluate(GUARD_JS)
        self.page.on('dialog',lambda dialog:dialog.dismiss())
        self.page.get_by_text('提交订单').click()
        self.assertFalse(self.page.evaluate('!!window.submitted'))
        self.page.evaluate('window.__xssAddressReady=true;window.__xssAddressCheck=()=>true')
        self.page.get_by_text('提交订单').click()
        self.assertTrue(self.page.evaluate('window.submitted'))

    def test_banner_escapes_order_title_instead_of_executing_html(self):
        self.page.set_content('<main>离线结算页</main>')
        self.page.evaluate(BANNER_JS,dict(order_no='test-1',title='<img src=x onerror="window.injected=true">',message='请核对'))
        self.assertFalse(self.page.evaluate('!!window.injected'))
        self.assertIn('test-1',self.page.locator('#xss-checkout-banner').inner_text())

    def test_switching_address_after_fill_blocks_manual_submit(self):
        self.card()
        self.page.evaluate(GUARD_JS)
        self.page.locator('.address-radio').click()
        enable_manual_submit(self.page,RECEIVER)
        self.assertTrue(self.page.evaluate('window.__xssAddressCheck()'))
        self.page.locator('.address-text').evaluate("el => el.textContent='测试路2号'")
        self.assertFalse(self.page.evaluate('window.__xssAddressCheck()'))


if __name__ == '__main__':
    unittest.main()
