"""真正加载 MV3 扩展包，使用离线合成网页验证；不访问用户浏览器或真实账号。"""
import json
import os
from pathlib import Path
import unittest
import uuid
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RECEIVER = dict(receiver_name='测试买家',receiver_mobile='13800000000',prov_name='浙江省',
                city_name='杭州市',area_name='西湖区',town_name='测试街道',address='测试路1号')


class NativeExtensionBrowserTests(unittest.TestCase):
    def setUp(self):
        self.runtime = sync_playwright().start()
        self.addCleanup(self.runtime.stop)
        profile=ROOT/'backups'/'test-artifacts'/('native-extension-'+uuid.uuid4().hex)
        profile.mkdir(parents=True)
        extension=ROOT/'browser-extension'
        self.context=self.runtime.chromium.launch_persistent_context(str(profile),channel='chromium',headless=True,
            executable_path=os.environ.get('XSS_TEST_CHROMIUM_EXECUTABLE'),
            args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}'])
        self.addCleanup(self.context.close)
        self.context.set_default_timeout(10000)
        self.worker=self.context.service_workers[0] if self.context.service_workers else self.context.wait_for_event('serviceworker')
        self.targets={i:dict(order_id=i,order_no='offline-native-'+str(i),receiver=dict(RECEIVER,
            receiver_name='测试买家'+str(i),address='测试路'+str(i)+'号')) for i in [1,2]}
        self.reads=[]
        self.saved=[]
        self.context.route('**/*',self.handle)

    def cards(self):
        return '<ul class="address-list">'+''.join(
            '<li><div class="address-info1"><span>'+item['receiver']['receiver_name']+'</span><span class="phone">13800000000</span></div>'
            '<div class="address-info2">浙江省杭州市西湖区<span class="address-text">测试街道'+item['receiver']['address']+'</span></div>'
            '<button class="address-radio" onclick="document.querySelectorAll(\'li\').forEach(el=>el.classList.remove(\'selected\'));this.closest(\'li\').classList.add(\'selected\')">选中</button></li>'
            for item in self.targets.values())+'</ul><button onclick="window.submitted=true">提交订单</button>'

    def handle(self,route):
        url=route.request.url
        if url.endswith('/browser_target'):
            oid=int(url.split('/orders/')[1].split('/')[0])
            self.reads.append({'id':oid,'csrf':route.request.headers.get('x-csrf-token')})
            return route.fulfill(json={'ok':True,'target':self.targets[oid]})
        if url.startswith('http://127.0.0.1:5000/'):
            html='<main><script data-csrf="offline-token"></script>'+''.join(
                f'<a data-xss-order="{i}" href="https://book.kongfz.com/1/{i}">订单{i}货源</a>' for i in [1,2])+'</main>'
        elif url.startswith('https://book.kongfz.com/'):
            html='<button onclick="window.open(\'https://order.kongfz.com/checkout\',\'_blank\')">立即购买</button>'
        else:
            html=self.cards()
        route.fulfill(body='<meta charset="utf-8">'+html,content_type='text/html')

    def open_source(self,oid):
        local=self.context.new_page();local.goto('http://127.0.0.1:5000/orders')
        local.get_by_text('闲书书扩展已启用',exact=False).wait_for()
        with self.context.expect_page() as event:local.get_by_text(f'订单{oid}货源',exact=True).click()
        source=event.value
        source.wait_for_url('https://book.kongfz.com/**')
        return source

    def checkout(self,source):
        with self.context.expect_page() as event:source.get_by_text('立即购买',exact=True).click()
        page=event.value
        page.get_by_text('本单收货地址已填写并核对',exact=False).wait_for()
        return page

    def test_real_extension_carries_two_buyers_to_their_own_checkout(self):
        source1=self.open_source(1)
        source2=self.open_source(2)
        first=self.checkout(source1)
        second=self.checkout(source2)
        self.assertIn('测试买家1',first.locator('li.selected').inner_text())
        self.assertIn('测试买家2',second.locator('li.selected').inner_text())
        self.assertFalse(first.evaluate('!!window.submitted'))
        self.assertFalse(second.evaluate('!!window.submitted'))
        self.assertEqual(self.reads,[{'id':1,'csrf':'offline-token'},{'id':2,'csrf':'offline-token'}])
        self.assertNotIn('13800000000',first.url+second.url)
        self.assertNotIn('receiver',json.dumps(self.worker.evaluate('chrome.storage.local.get(null)')))

    def test_unrelated_checkout_has_no_order_context(self):
        self.open_source(1)
        page=self.context.new_page();page.goto('https://order.kongfz.com/checkout')
        self.assertEqual(page.locator('li.selected').count(),0)
        self.assertFalse(page.evaluate('!!window.submitted'))

    def test_extension_popup_loads_without_requesting_platform_credentials(self):
        extension_id=self.worker.url.split('/')[2]
        page=self.context.new_page();page.goto('chrome-extension://'+extension_id+'/popup.html')
        self.assertEqual(page.get_by_role('heading',name='闲书书采购地址助手').count(),1)
        self.assertEqual(page.locator('input[type=password]').count(),0)
        self.assertEqual(page.locator('#site').input_value(),'')
