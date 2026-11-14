import unittest

from bots.kongfz_bot import _looks_logged_in, _parse_shipping_info


class ShippingParseTests(unittest.TestCase):
    def test_inline_logistics_card(self):
        result = _parse_shipping_info(
            "订单详情\n物流信息  韵达速递  313000000000001  官方电话：95546\n运输中\n收货信息\n测试收货人"
        )
        self.assertTrue(result["shipped"])
        self.assertEqual(result["waybill_no"], "313000000000001")
        self.assertEqual(result["express_name"], "韵达速递")

    def test_multiline_logistics_card(self):
        result = _parse_shipping_info("物流信息\n中国邮政\n9310000000000\n运输中")
        self.assertEqual(result["waybill_no"], "9310000000000")
        self.assertEqual(result["express_name"], "中国邮政")

    def test_existing_history_format(self):
        result = _parse_shipping_info("卖家发货，快递【圆通速递】：YT0000000000001\n")
        self.assertEqual(result["waybill_no"], "YT0000000000001")

    def test_contact_number_is_not_waybill(self):
        result = _parse_shipping_info("物流信息\n韵达速递 官方电话：95546\n收货信息\n测试收货人 13800000000")
        self.assertFalse(result["shipped"])
        self.assertFalse(result["waybill_no"])
        self.assertIn("不能据此判断未发货", result["detail"])

    def test_status_without_waybill_is_inconclusive(self):
        result = _parse_shipping_info("订单状态：待收货\n订单号：311000000")
        self.assertFalse(result["shipped"])
        self.assertFalse(result["waybill_no"])


class LoginDetectionTests(unittest.TestCase):
    class Page:
        def __init__(self, url, text, title=""):
            self.url, self.text, self.heading = url, text, title

        def inner_text(self, selector):
            return self.text

        def title(self):
            return self.heading

    def test_private_order_page_with_global_login_text(self):
        page = self.Page("https://order.kongfz.com/order-views/management/buyer/detail/311000000",
                         "请登录\n订单号：311000000\n物流信息")
        self.assertTrue(_looks_logged_in(page))

    def test_login_page_is_not_authenticated(self):
        page = self.Page("https://login.kongfz.com/", "订单号 登录/注册")
        self.assertFalse(_looks_logged_in(page))

    def test_empty_or_public_page_is_not_login_success(self):
        page = self.Page("https://www.kongfz.com/", "孔夫子旧书网")
        self.assertFalse(_looks_logged_in(page))

    def test_unrelated_domain_is_not_login_success(self):
        page = self.Page("https://example.com/", "订单号 退出登录")
        self.assertFalse(_looks_logged_in(page))


if __name__ == "__main__":
    unittest.main()
