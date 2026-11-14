"""核对地址后直达官方页；所有网络均为隔离浏览器模拟。"""
import json
import os
from pathlib import Path
import unittest


class ImmediateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime=sync_playwright().start()
        cls.browser=cls.runtime.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def fixture(self):
        context=self.browser.new_context()
        context.set_default_timeout(6000)
        self.addCleanup(context.close)
        root=Path(__file__).resolve().parents[1]
        script=(root/'static/source-immediate.js').read_text(encoding='utf-8')
        html='''<meta charset="utf-8"><script data-csrf="offline-token"></script>
        <a href="/orders/1/source" data-source-url="https://book.kongfz.com/1/2/" target="_blank" rel="noopener" data-xss-native-source="1">去货源</a>
        <small data-source-sync-status="1" hidden></small>
        <a href="/orders/2/source" data-source-url="https://book.kongfz.com/2/3/" target="_blank" rel="noopener" data-xss-native-source="2">另一笔货源</a>
        <small data-source-sync-status="2" hidden></small>'''
        pending=[]
        visits=[]
        dialogs=[]
        def route(item):
            visits.append(item.request.url)
            if item.request.url.endswith('/sync_address'):
                pending.append(item)
            elif item.request.url.startswith('https://book.kongfz.com/'):
                item.fulfill(body='<h1>官方货源模拟页</h1>',content_type='text/html')
            else:
                item.fulfill(body=html+'<script>'+script+'</script>',content_type='text/html')
        context.route('**/*',route)
        page=context.new_page()
        page.on('dialog',lambda dialog:(dialogs.append(dialog.message),dialog.dismiss()))
        page.goto('https://offline.test/orders')
        return page,context,pending,visits,dialogs

    def open(self,page,context):
        with context.expect_page() as event:
            page.get_by_text('去货源',exact=True).click()
        popup=event.value
        popup.wait_for_load_state('domcontentloaded')
        return popup

    def test_official_page_cannot_open_before_verified_address_and_uses_csrf(self):
        page,context,pending,visits,dialogs=self.fixture()
        popup=self.open(page,context)
        self.assertEqual(popup.url,'about:blank')
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        self.assertFalse(any(url.startswith('https://book.kongfz.com/') for url in visits))
        self.assertIsNone(popup.evaluate('window.opener'))
        self.assertEqual(len(pending),1)
        request=pending[0].request
        self.assertEqual(request.method,'POST')
        self.assertEqual(request.headers['x-csrf-token'],'offline-token')
        self.assertFalse(any('/source' in url for url in visits))
        pending[0].fulfill(json=dict(ok=True,verified=True,source_url='https://book.kongfz.com/1/2/'))
        popup.wait_for_url('https://book.kongfz.com/1/2/')
        page.get_by_text('本单地址已同步并核对，已打开孔夫子',exact=True).wait_for()
        self.assertFalse(dialogs)

    def test_sync_failure_is_visible_and_not_shown_as_success(self):
        page,context,pending,visits,dialogs=self.fixture()
        popup=self.open(page,context)
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        pending[0].fulfill(status=400,json=dict(ok=False,error='地区核对失败'))
        page.get_by_text('地址同步失败：地区核对失败',exact=True).wait_for()
        self.assertEqual(len(dialogs),1)
        self.assertIn('尚未打开货源',dialogs[0])
        self.assertTrue(popup.is_closed())
        self.assertFalse(any(url.startswith('https://book.kongfz.com/') for url in visits))
        self.assertEqual(page.get_by_text('地址已同步并核对',exact=False).count(),0)

    def test_repeated_click_during_sync_does_not_open_or_write_again(self):
        page,context,pending,visits,dialogs=self.fixture()
        self.open(page,context)
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        page.get_by_text('去货源',exact=True).click()
        self.assertEqual(len(context.pages),2)
        self.assertEqual(len(pending),1)
        pending[0].fulfill(json=dict(ok=True,verified=True))
        page.get_by_text('地址已同步并核对',exact=False).wait_for()

    def test_unverified_response_or_html_error_cannot_show_success(self):
        page,context,pending,visits,dialogs=self.fixture()
        self.open(page,context)
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        pending[0].fulfill(status=502,body='<html>gateway error</html>',content_type='text/html')
        page.get_by_text('地址同步失败：服务器未返回同步结果',exact=False).wait_for()
        self.assertEqual(len(dialogs),1)

    def test_switching_order_waits_for_current_sync_and_invalidates_previous_ready(self):
        page,context,pending,visits,dialogs=self.fixture()
        self.open(page,context)
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        page.get_by_text('另一笔货源',exact=True).click()
        page.get_by_text('正在核对另一笔地址',exact=False).wait_for()
        self.assertEqual(len(pending),1)
        self.assertEqual(len(context.pages),2)
        pending[0].fulfill(json=dict(ok=True,verified=True))
        page.get_by_text('本单地址已同步并核对',exact=False).wait_for()
        page.get_by_text('另一笔货源',exact=True).click()
        page.locator('[data-source-sync-status="2"]').get_by_text('正在核对本单地址',exact=False).wait_for()
        self.assertEqual(len(pending),2)
        pending[1].fulfill(json=dict(ok=True,verified=True))
        page.locator('[data-source-sync-status="1"]').get_by_text('已切换其他订单',exact=False).wait_for()
        page.locator('[data-source-sync-status="2"]').get_by_text('本单地址已同步并核对',exact=False).wait_for()

    def test_invalid_destination_never_opens_external_site(self):
        page,context,pending,visits,dialogs=self.fixture()
        popup=self.open(page,context)
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        pending[0].fulfill(json=dict(ok=True,verified=True,source_url='https://example.org/'))
        page.get_by_text('地址同步失败：货源链接无效',exact=False).wait_for()
        self.assertTrue(popup.is_closed())
        self.assertFalse(any('example.org' in url for url in visits))

    def test_popup_blocked_navigates_current_tab_only_after_verification(self):
        page,context,pending,visits,dialogs=self.fixture()
        page.evaluate('window.open=()=>null')
        page.get_by_text('去货源',exact=True).click()
        page.get_by_text('正在核对本单地址',exact=False).wait_for()
        self.assertEqual(page.url,'https://offline.test/orders')
        pending[0].fulfill(json=dict(ok=True,verified=True))
        page.wait_for_url('https://book.kongfz.com/1/2/')


if __name__=='__main__':
    unittest.main()
