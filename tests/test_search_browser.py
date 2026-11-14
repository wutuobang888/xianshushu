# -*- coding: utf-8 -*-
"""公开书目搜索与采购登录目录隔离，使用模拟浏览器验证。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bots import kongfz_bot


class SearchBrowserTests(unittest.TestCase):
    def test_search_uses_independent_browser_and_closes_it(self):
        runtime = Mock()
        browser = runtime.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.evaluate.return_value = [{
            'title': '离线书目', 'url': 'https://item.kongfz.com/book/1.html',
            'meta': [], 'others': []}]
        with patch('playwright.sync_api.sync_playwright') as factory, \
                patch.object(kongfz_bot, '_launch') as persistent:
            factory.return_value.__enter__.return_value = runtime
            result = kongfz_bot.search_entries('离线书目')
        persistent.assert_not_called()
        runtime.chromium.launch_persistent_context.assert_not_called()
        self.assertTrue(runtime.chromium.launch.call_args.kwargs['headless'])
        self.assertIn('&sortType=20&page=1', page.goto.call_args.args[0])
        self.assertEqual(result[0]['book_title'], '离线书目')
        browser.close.assert_called_once()

    def test_failed_search_still_closes_browser(self):
        runtime = Mock()
        browser = runtime.chromium.launch.return_value
        browser.new_context.return_value.new_page.return_value.goto.side_effect = RuntimeError('离线连接失败')
        with patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.__enter__.return_value = runtime
            with self.assertRaisesRegex(RuntimeError, '离线连接失败'):
                kongfz_bot.search_entries('离线书目')
        browser.close.assert_called_once()

    def test_no_results_page_returns_empty_list_without_timeout(self):
        runtime = Mock()
        browser = runtime.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.evaluate.return_value = []
        with patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.__enter__.return_value = runtime
            self.assertEqual(kongfz_bot.search_entries('不存在的完整标题'), [])
        self.assertIn(".error-title:has-text('暂无结果')", page.wait_for_selector.call_args.args[0])
        self.assertEqual(page.goto.call_args.kwargs['wait_until'], 'domcontentloaded')
        browser.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
