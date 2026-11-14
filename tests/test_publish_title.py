# -*- coding: utf-8 -*-
"""上架标题长度与载荷一致性，不调用真实平台接口。"""
import unittest
from unittest.mock import Mock, patch
from services import template_service as templates, product_service as products


class PublishTitleTests(unittest.TestCase):
    def test_short_title_is_unchanged(self):
        self.assertEqual(templates.publish_title('耶路撒冷三千年'), '耶路撒冷三千年')

    def test_long_title_is_limited_to_60(self):
        self.assertEqual(templates.publish_title('书' * 87), '书' * 60)

    def test_emoji_is_not_split_at_boundary(self):
        title = templates.publish_title('书' * 59 + '📚包邮')
        self.assertEqual(title, '书' * 59)

    def test_top_level_and_shop_titles_use_same_limit(self):
        book = {'id': 1, 'book_title': '耶路撒冷三千年', 'title_template_id': 1,
                'desc_template_id': 2, 'sale_price': 16.9, 'kongfz_url': 'offline',
                'image_url': '', 'pure_title': '', 'author': '作者', 'publisher': '出版社'}
        def template(tid):
            return {'content': '【正版二手】{书名}' + '作者' * 50 if tid == 1 else '完整说明'}
        with patch.object(templates, 'get_template', side_effect=template), \
                patch.object(products.image_service, 'publish_image_urls', return_value=['offline-image']), \
                patch.object(products, '_shop_region', return_value={}), \
                patch.object(products, '_ensure_isbn', return_value=''):
            payload = products._prepare_publish_payload(book, Mock(), 'offline', None, None, None, [1])
        self.assertEqual(len(payload['title']), 60)
        self.assertEqual(payload['title'], payload['publish_shop'][0]['title'])
        self.assertEqual(payload['publish_shop'][0]['content'], '完整说明')
        self.assertEqual(book['book_title'], '耶路撒冷三千年')

    def test_empty_title_blocks_before_image_preparation(self):
        book = {'id': 1, 'book_title': '书名', 'title_template_id': 1,
                'desc_template_id': 2, 'sale_price': 16.9}
        with patch.object(templates, 'get_template', return_value={'content': '  '}), \
                patch.object(products.image_service, 'publish_image_urls') as images:
            with self.assertRaisesRegex(ValueError, '标题不能为空'):
                products._prepare_publish_payload(book, Mock(), 'offline', None, None, None, [])
        images.assert_not_called()


if __name__ == '__main__':
    unittest.main()
