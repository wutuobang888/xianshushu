"""普通浏览器地址助手：离线站点、模拟订单及独立浏览器，不读取真实账号。"""
import json
import os
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

SCRIPT = (Path(__file__).resolve().parents[1]/'scripts/kfz_address.user.js').read_text(encoding='utf-8')
RECEIVER = dict(receiver_name='测试买家',receiver_mobile='13800000000',prov_name='浙江省',
                city_name='杭州市',area_name='西湖区',town_name='测试街道',address='测试路1号')
TARGET = dict(order_id=1,order_no='offline-1',product_title='测试图书',receiver=RECEIVER)
CARD = '''<ul class="address-list"><li>
<div class="address-info1"><span>测试买家</span><span class="phone">13800000000</span></div>
<div class="address-info2">浙江省杭州市西湖区<em class="default">默认</em><span class="address-text">测试街道测试路1号</span></div>
<button class="address-radio" onclick="this.closest('li').classList.add('selected')">选中</button>
<span class="edit" onclick="document.querySelector('.el-dialog').style.display='block'">修改</span>
</li></ul><button onclick="window.submitted=true">提交订单</button>'''


class BrowserAddressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=sync_playwright().start()
        cls.browser=cls.engine.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.engine.stop()

    def setUp(self):
        self.context=self.browser.new_context()
        self.addCleanup(self.context.close)
        self.html={}
        self.requests=[]
        def handle(route):
            request=route.request
            if request.url.endswith('/browser_target'):
                self.requests.append(dict(method=request.method,csrf=request.headers.get('x-csrf-token')))
                route.fulfill(json=dict(ok=True,target=TARGET))
            else:
                route.fulfill(body=self.html.get(request.url.split('?')[0],'<main>离线孔夫子</main>'),content_type='text/html; charset=utf-8')
        self.context.route('**/*',handle)

    def install(self,cache=None):
        seed=json.dumps(cache or {},ensure_ascii=False)
        self.context.add_init_script('window.testCache='+seed+';'
            'window.GM_getValue=(k,d)=>window.testCache[k]||d;'
            'window.GM_setValue=(k,v)=>{window.testCache[k]=v};'
            'window.GM_listValues=()=>Object.keys(window.testCache);'
            'window.GM_deleteValue=k=>{delete window.testCache[k]};'
            'window.GM_setClipboard=v=>{window.copied=v};window.unsafeWindow=window;')
        self.context.add_init_script("window.addEventListener('DOMContentLoaded',()=>{"+SCRIPT+"},{once:true});")

    def seed(self):
        import time
        return {
            'xss_browser_targets_v2:nonce-a':dict(target=TARGET,ts=int(time.time()*1000),used=False),
            'xss_browser_targets_v2:nonce-b':dict(target=dict(TARGET,order_id=2,order_no='offline-2',
                receiver=dict(RECEIVER,receiver_name='另一买家',receiver_mobile='13900000000',address='测试路2号')),ts=int(time.time()*1000),used=False)}

    def test_site_reads_current_order_with_csrf_and_opens_direct_official_page(self):
        self.install()
        url='https://books.example.com/orders'
        self.html[url]='<script data-csrf="offline-csrf"></script><a data-xss-order="1" href="https://book.kongfz.com/1/2">去货源</a>'
        page=self.context.new_page();page.goto(url)
        with page.expect_popup() as popup:
            page.get_by_text('去货源').click()
        opened=popup.value
        opened.wait_for_url('https://book.kongfz.com/**')
        self.assertIn('xss_ctx=',opened.url)
        self.assertNotIn('13800000000',opened.url)
        self.assertNotIn('/browser/',opened.url)
        self.assertEqual(self.requests,[dict(method='POST',csrf='offline-csrf')])
        self.assertEqual(len(page.evaluate('Object.keys(window.testCache)')),1)

    def test_purchase_popup_inherits_its_own_order_context_not_last_other_tab(self):
        self.install(self.seed())
        url='https://book.kongfz.com/1/2'
        self.html[url]='<button onclick="window.open(\'https://order.kongfz.com/order-views/create\')">立即购买</button>'
        for nonce in ('nonce-a','nonce-b'):
            page=self.context.new_page();page.goto(url+'?xss_ctx='+nonce)
            with page.expect_popup() as popup:page.get_by_text('立即购买').click()
            opened=popup.value;opened.wait_for_url('https://order.kongfz.com/**')
            self.assertIn('xss_ctx='+nonce,opened.url)

    def test_checkout_selects_exact_address_without_submitting_purchase(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        self.html[url]=CARD
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        page.get_by_text('本单收货地址已填写并核对，请核对书籍和金额后自行提交付款').wait_for()
        self.assertTrue(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertFalse(page.evaluate('!!window.submitted'))
        self.assertTrue(page.evaluate('window.testCache["xss_browser_targets_v2:nonce-a"].used'))

    def test_user_cannot_submit_after_switching_to_unverified_address(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        self.html[url]=CARD
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        page.get_by_text('本单收货地址已填写并核对，请核对书籍和金额后自行提交付款').wait_for()
        page.locator('li').evaluate("li=>li.classList.remove('selected')")
        messages=[]
        page.on('dialog',lambda dialog:(messages.append(dialog.message),dialog.dismiss()))
        page.get_by_text('提交订单',exact=True).click()
        self.assertFalse(page.evaluate('!!window.submitted'))
        self.assertTrue(messages)

    def test_two_order_contexts_select_their_own_buyer(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        first=CARD[CARD.index('<li>'):CARD.index('</ul>')]
        second=first.replace('测试买家','另一买家').replace('13800000000','13900000000').replace('测试路1号','测试路2号')
        self.html[url]='<ul class="address-list">'+first+second+'</ul>'
        for nonce,name in [('nonce-a','测试买家'),('nonce-b','另一买家')]:
            page=self.context.new_page();page.goto(url+'?xss_ctx='+nonce)
            page.get_by_text('本单收货地址已填写并核对，请核对书籍和金额后自行提交付款').wait_for()
            self.assertIn(name,page.locator('li.selected').inner_text())

    def test_other_door_number_is_not_silently_selected_or_saved(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        dialog='''<div class="address-dialog"><div class="el-dialog" style="display:none">
        <textarea></textarea><div class="btn-parse-w"><button>识别</button></div>
        <div class="k-area-select-wrap"><span class="area-text">浙江省杭州市西湖区</span></div>
        <input placeholder="收货人" value="其他人"><input placeholder="手机" value="13800000000">
        <input placeholder="详细地址" value="测试街道测试路1号"><button onclick="window.saved=true">保存并使用</button>
        </div></div>'''
        self.html[url]='<div class="address-operate">'+CARD.replace('测试路1号','测试路2号')+'</div>'+dialog+'<button onclick="document.querySelector(\'.el-dialog\').style.display=\'block\'">新增收货地址</button>'
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        page.get_by_text('识别结果与本单地址不一致，已停止保存——完整地址已复制到剪贴板').wait_for()
        self.assertFalse(page.evaluate('!!window.saved'))
        self.assertFalse(page.evaluate('!!window.submitted'))

    def test_local_orders_have_helper_indicator_and_fetch_same_origin(self):
        self.install()
        url='http://127.0.0.1:5000/orders'
        self.html[url]='<main><script data-csrf="local-token"></script><a data-xss-order="1" href="https://book.kongfz.com/1/2">去货源</a></main>'
        page=self.context.new_page();page.goto(url)
        page.get_by_text('地址助手已启用',exact=False).wait_for()
        with page.expect_popup() as popup:page.get_by_text('去货源').click()
        popup.value.wait_for_url('https://book.kongfz.com/**')
        self.assertEqual(self.requests,[dict(method='POST',csrf='local-token')])

    def test_mismatched_existing_address_is_not_overwritten(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        self.html[url]=CARD.replace('测试路1号','测试路2号')
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        page.get_by_text('未找到新增地址入口',exact=False).wait_for()
        self.assertFalse(page.evaluate('!!window.saved || !!window.submitted'))
        self.assertIn('测试路2号',page.locator('li').inner_text())

    def test_adds_new_address_and_reads_it_back_before_selecting(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        dialog='''<div class="address-dialog"><div class="el-dialog" style="display:none">
        <textarea></textarea><div class="btn-parse-w"><button>识别</button></div>
        <div class="k-area-select-wrap"><span class="area-text">浙江省杭州市西湖区</span></div>
        <input placeholder="收货人" value="测试买家"><input placeholder="手机" value="13800000000">
        <input placeholder="详细地址" value="测试街道测试路1号">
        <button onclick="document.querySelector('.address-list').innerHTML=window.correctCard;this.closest('.el-dialog').style.display='none';window.saved=true">保存并使用</button>
        </div></div><button onclick="document.querySelector('.el-dialog').style.display='block'">新增收货地址</button>'''
        self.html[url]='<ul class="address-list"></ul>'+dialog+'<script>window.correctCard='+json.dumps(CARD[CARD.index('<li>'):CARD.index('</ul>')],ensure_ascii=False)+'</script>'
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        page.get_by_text('本单收货地址已填写并核对，请核对书籍和金额后自行提交付款').wait_for()
        self.assertTrue(page.evaluate('!!window.saved'))
        self.assertTrue(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertFalse(page.evaluate('!!window.submitted'))

    def test_expired_context_never_selects_or_submits(self):
        seed=self.seed();seed['xss_browser_targets_v2:nonce-a']['ts']=1
        self.install(seed)
        url='https://order.kongfz.com/order-views/create'
        self.html[url]=CARD
        page=self.context.new_page();page.goto(url+'?xss_ctx=nonce-a')
        self.assertFalse(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertFalse(page.evaluate('!!window.submitted'))

    def test_localhost_alias_has_helper_indicator(self):
        self.install()
        page=self.context.new_page();page.goto('http://localhost:5000/orders')
        page.get_by_text('地址助手已启用',exact=False).wait_for()

    def test_unmarked_normal_browsing_does_not_fill_any_address(self):
        self.install(self.seed())
        url='https://order.kongfz.com/order-views/create'
        self.html[url]=CARD
        page=self.context.new_page();page.goto(url)
        self.assertFalse(page.locator('li').evaluate("li=>li.classList.contains('selected')"))
        self.assertFalse(page.evaluate('!!window.submitted'))


if __name__=='__main__':unittest.main()
