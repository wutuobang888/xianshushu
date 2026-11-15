"""独立扩展收图器的真实 DOM 回归；只使用合成网页。"""
from pathlib import Path
import os
import unittest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


class NativeCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime=sync_playwright().start()
        cls.browser=cls.runtime.chromium.launch(channel='msedge' if os.name == 'nt' else None,headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def page(self,url,html):
        context=self.browser.new_context();self.addCleanup(context.close)
        context.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html; charset=utf-8'))
        page=context.new_page();page.goto(url)
        page.evaluate('''() => {window.requests=[];window.chrome={runtime:{sendMessage:async msg=>{
            requests.push(msg);return {ok:true,count:msg.payload.urls.length,target:'https://book.kongfz.com/1/2',title:'书A'};
        }}};}''')
        page.add_script_tag(content=(ROOT/'browser-extension/collector.js').read_text(encoding='utf-8'))
        page.evaluate("XssImageCollector({entry_url:'https://book.kongfz.com/1/2',title:'书A'})")
        return page

    def test_gallery_keeps_13_photos_and_uses_native_runtime_messages(self):
        html=(ROOT/'tests/fixtures/kongfz_cos_gallery.html').read_text(encoding='utf-8')
        page=self.page('https://book.kongfz.com/704437/9060658888',html)
        expected=page.locator('.lg-list img').evaluate_all("images=>images.map(i=>i.getAttribute('data-imgurl').replace(/_n\\.jpg$/,'_b.jpg'))")
        page.locator('#xhs-collect-float').click()
        page.get_by_text('已收 13 张图',exact=False).wait_for()
        request=page.evaluate('requests[0]')
        self.assertEqual(request['type'],'COLLECT_IMAGES')
        self.assertEqual(request['payload']['urls'],expected)

    def test_invalid_hosts_are_not_collected(self):
        page=self.page('https://book.kongfz.com/1/2','''<div class="exhibition"><ul class="lg-list">
        <img data-imgurl="https://foreign.invalid/a_n.jpg"><img data-imgurl="https://kfzimg.com.evil.invalid/a_n.jpg">
        <img data-imgurl="https://www0.kfzimg.com/good_n.jpg"></ul></div>''')
        page.locator('#xhs-collect-float').click()
        page.get_by_text('已收 1 张图',exact=False).wait_for()
        self.assertEqual(page.evaluate('requests[0].payload.urls'),['https://www0.kfzimg.com/good_b.jpg'])

    def test_non_seller_page_does_not_report_cover_as_seller_photos(self):
        page=self.page('https://item.kongfz.com/book/1.html','<img src="https://www0.kfzimg.com/cover_b.jpg">')
        page.locator('#xhs-collect-float').click()
        page.get_by_text('请先进入某家卖家的商品详情页',exact=False).wait_for()
        self.assertEqual(page.evaluate('requests.length'),0)

    def test_button_does_not_create_mutation_observer_loop(self):
        page=self.page('https://www.xiaohongshu.com/explore/note','<meta property="og:image" content="https://test.xhscdn.com/photo.jpg">')
        page.locator('#xhs-collect-float').click()
        page.get_by_text('已收 1 张图',exact=False).wait_for()
        self.assertEqual(page.locator('#xhs-collect-float').count(),1)
