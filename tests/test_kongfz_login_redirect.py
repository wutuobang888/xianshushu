"""离线验证孔夫子 SPA 接口的未登录跳转，不连接任何账号。"""
import unittest
from unittest.mock import Mock
from bots.kongfz_bot import _goto_order_page


class OrderLoginRedirectTests(unittest.TestCase):
    def page(self,location):
        page=Mock()
        handlers=[]
        page.on.side_effect=lambda event,handler:handlers.append(handler)
        page.remove_listener.side_effect=lambda event,handler:handlers.remove(handler)
        def goto(url,**options):
            page.url=url
            if url.startswith('https://order.kongfz.com'):
                response=Mock(status=302,url='https://order.kongfz.com/common-web/v1/common/sites',headers={'location':location})
                for handler in list(handlers):
                    handler(response)
        page.goto.side_effect=goto
        return page,handlers

    def test_api_login_redirect_uses_fixed_official_https_page(self):
        page,handlers=self.page('http://login.kongfz.com/?returnUrl=private')
        _goto_order_page(page,'https://order.kongfz.com/detail/123',45000)
        self.assertEqual(page.goto.call_args.args[0],'https://login.kongfz.com/')
        self.assertEqual(handlers,[])

    def test_other_redirect_does_not_trigger_login(self):
        page,handlers=self.page('https://example.com/login')
        _goto_order_page(page,'https://order.kongfz.com/detail/123',45000)
        self.assertEqual(page.goto.call_count,1)
        self.assertEqual(handlers,[])

    def test_navigation_failure_releases_observer(self):
        page,handlers=self.page('https://login.kongfz.com/')
        page.goto.side_effect=TimeoutError('offline')
        with self.assertRaises(TimeoutError):
            _goto_order_page(page,'https://order.kongfz.com/detail/123',45000)
        self.assertEqual(handlers,[])
