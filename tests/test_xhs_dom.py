# -*- coding: utf-8 -*-
"""小红书脚本真实DOM回归：独立离线浏览器，不访问账号或下载图片。"""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright


class XhsDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='msedge', headless=True)
        cls.script = Path(__file__).resolve().parents[1].joinpath('scripts/xhs_collect.user.js').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.context.route('**/*', lambda route: route.fulfill(
            body='<html><body><div id="note">离线笔记</div></body></html>', content_type='text/html'))
        self.page.goto('https://www.xiaohongshu.com/search_result#xs_collect&entry_url=book-a&book_title=书A')
        self.page.evaluate('''() => {
            window.probe = {calls:0, loop:false};
            const Original = window.MutationObserver;
            // 有界观测：回归失败时主动断开，防止坏脚本卡死测试浏览器。
            window.MutationObserver = class {
                constructor(callback) {
                    this.inner = new Original(records => {
                        probe.calls++;
                        if (probe.calls >= 30) {probe.loop=true; this.inner.disconnect(); return;}
                        callback(records, this);
                    });
                }
                observe(...args) {this.inner.observe(...args);}
            };
            window.GM_xmlhttpRequest = () => {};
        }''')
        self.page.add_script_tag(content=self.script)

    def test_button_mutations_settle_and_page_remains_responsive(self):
        self.page.wait_for_timeout(150)
        result = self.page.evaluate('probe')
        self.assertFalse(result['loop'], result)
        self.assertLess(result['calls'], 5)
        self.assertEqual(self.page.locator('#xhs-collect-float').count(), 1)
        self.page.evaluate('document.getElementById("note").textContent="继续阅读"')
        self.page.wait_for_timeout(50)
        self.assertFalse(self.page.evaluate('probe.loop'))

    def test_target_switch_updates_label_without_observer_loop(self):
        self.page.evaluate('location.hash="xs_collect&entry_url=book-b&book_title=书B"')
        self.page.wait_for_function('document.getElementById("xhs-collect-float").textContent.includes("书B")')
        self.page.wait_for_timeout(100)
        result = self.page.evaluate('probe')
        self.assertFalse(result['loop'], result)
        self.assertLess(result['calls'], 10)
        self.assertEqual(self.page.locator('#xhs-collect-float').count(), 1)

    def native_page(self, url, html):
        self.page.goto(url)
        self.page.set_content(html)
        self.page.evaluate('() => {window.nativeRequests=[]; window.GM_xmlhttpRequest=request=>nativeRequests.push(JSON.parse(request.data));}')
        self.page.add_script_tag(content=self.script)

    def test_kongfz_seller_link_carries_target_across_domains(self):
        self.native_page('https://item.kongfz.com/book/1.html#xs_collect&entry_url=book-a&book_title=书A',
                         '<html><body><a id="seller" href="https://book.kongfz.com/123/456/" target="_blank">卖家</a></body></html>')
        self.page.evaluate('document.getElementById("seller").addEventListener("click",event=>event.preventDefault())')
        self.page.locator('#seller').click()
        href = self.page.locator('#seller').get_attribute('href')
        self.assertIn('entry_url=book-a', href)
        self.assertIn('book_title=', href)
        self.page.locator('#xhs-collect-float').click()
        self.assertEqual(self.page.evaluate('nativeRequests.length'), 0)

    def test_kongfz_detail_uses_large_photos_and_skips_recommendations(self):
        self.native_page('https://book.kongfz.com/123/456/#xs_collect&entry_url=book-a&book_title=书A', '''
            <html><body><h1>卖家实拍</h1>
            <img data-original="https://img1.kfzimg.com/photo-Ab_s.jpg">
            <img data-src="https://img1.kfzimg.com/photo-Ab_n.jpg">
            <img data-original="https://img1.kfzimg.com/second-Cd_b.jpg">
            <div class="recommend-list"><img data-original="https://img1.kfzimg.com/wrong-Ef_s.jpg"></div>
            </body></html>''')
        self.page.locator('#xhs-collect-float').click()
        data = self.page.evaluate('nativeRequests[0]')
        self.assertEqual(data['source'], 'kongfz')
        self.assertEqual(data['entry_url'], 'book-a')
        self.assertEqual(data['note_url'], 'https://book.kongfz.com/123/456/')
        self.assertEqual(data['urls'], ['https://img1.kfzimg.com/photo-Ab_b.jpg', 'https://img1.kfzimg.com/second-Cd_b.jpg'])

    def test_kongfz_cos_gallery_collects_all_13_photos_in_order(self):
        # 取自东风怀旧书店真实商品页，含未加载的 _src/data-imgurl 图片。
        html = Path(__file__).parent.joinpath('fixtures/kongfz_cos_gallery.html').read_text(encoding='utf-8')
        self.native_page('https://book.kongfz.com/704437/9060658888#xs_collect&entry_url=book-a&book_title=书A', html)
        expected = self.page.locator('.lg-list img').evaluate_all(
            "images => images.map(i => i.getAttribute('data-imgurl').replace(/_n\\.jpg$/, '_b.jpg'))")
        self.page.locator('#xhs-collect-float').click()
        data = self.page.evaluate('nativeRequests[0]')
        self.assertEqual(len(data['urls']), 13)
        self.assertEqual(data['urls'], expected)
        self.assertEqual(data['entry_url'], 'book-a')
        self.assertEqual(data['source'], 'kongfz')

    def test_kongfz_gallery_rejects_foreign_hosts_and_invalid_addresses(self):
        self.native_page('https://book.kongfz.com/123/456/#xs_collect&entry_url=book-a&book_title=书A', '''
            <div class="exhibition"><ul class="lg-list"><li>
            <img data-imgurl="http://[broken" _src="https://www0.kfzimg.com/sw/kfz-cos/kfzimg/a/photo_n.jpg">
            <img data-imgurl="https://kfzimg.com.evil.example/wrong_b.jpg">
            <img data-imgurl="https://foreign.example/wrong_b.jpg">
            <img data-imgurl="https://www0.kfzimg.com/avatar.png">
            </li></ul></div>''')
        self.page.locator('#xhs-collect-float').click()
        self.assertEqual(self.page.evaluate('nativeRequests[0].urls'),
                         ['https://www0.kfzimg.com/sw/kfz-cos/kfzimg/a/photo_b.jpg'])


if __name__ == '__main__':
    unittest.main()
