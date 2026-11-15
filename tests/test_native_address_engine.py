"""独立扩展的结算引擎 DOM 回归；使用合成地址，不提交真实订单。"""
import json
import os
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
TARGET=dict(order_id=1,receiver=dict(receiver_name='测试买家',receiver_mobile='13800000000',
    prov_name='浙江省',city_name='杭州市',area_name='西湖区',town_name='测试街道',address='测试路1号'))
CARD='''<ul class="address-list"><li>
<div class="address-info1"><span>测试买家</span><span class="phone">13800000000</span></div>
<div class="address-info2">浙江省杭州市西湖区<span class="address-text">测试街道测试路1号</span></div>
<button class="address-radio" onclick="this.closest('li').classList.add('selected')">选中</button>
</li></ul><button onclick="window.submitted=true">提交订单</button>'''


class NativeAddressEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime=sync_playwright().start()
        cls.browser=cls.runtime.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def page(self,html,expired=False):
        context=self.browser.new_context()
        self.addCleanup(context.close)
        context.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html; charset=utf-8'))
        engine=(ROOT/'browser-extension/address-engine.js').read_text(encoding='utf-8')
        context.add_init_script(engine+'\nwindow.addEventListener("DOMContentLoaded",()=>XssAddressEngine('+json.dumps(TARGET,ensure_ascii=False)+','+('1' if expired else 'Date.now()+600000')+'));')
        page=context.new_page();page.goto('https://order.kongfz.com/checkout')
        return page

    def test_selects_full_match_and_blocks_manual_switch_before_submit(self):
        page=self.page(CARD)
        page.get_by_text('本单收货地址已填写并核对',exact=False).wait_for()
        self.assertTrue(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertFalse(page.evaluate('!!window.submitted'))
        page.locator('li').evaluate("li=>li.classList.remove('selected')")
        messages=[]
        page.on('dialog',lambda dialog:(messages.append(dialog.message),dialog.dismiss()))
        page.get_by_text('提交订单',exact=True).click()
        self.assertTrue(messages)
        self.assertFalse(page.evaluate('!!window.submitted'))

    def test_wrong_door_number_is_not_overwritten(self):
        page=self.page(CARD.replace('测试路1号','测试路2号'))
        page.get_by_text('未找到新增地址入口',exact=False).wait_for()
        self.assertFalse(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertIn('测试路2号',page.locator('li').inner_text())

    def test_expired_context_does_not_fill(self):
        page=self.page(CARD,expired=True)
        page.get_by_text('本单地址已过期',exact=False).wait_for()
        self.assertFalse(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
