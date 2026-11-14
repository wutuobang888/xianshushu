"""本地地址助手接口：独立数据库、模拟平台读取，不访问真实订单。"""
import importlib
from pathlib import Path
import re
import unittest
import uuid
from unittest.mock import patch

import config
import models

RECEIVER = dict(receiver_name='测试买家', receiver_mobile='13800000000',
                prov_name='浙江省', city_name='杭州市', area_name='西湖区',
                town_name='测试街道', address='测试路1号')


class LocalUserscriptTests(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[1] / 'backups' / 'test-artifacts' / ('local-helper-' + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        for key, value in dict(DATA_DIR=folder, DATABASE_PATH=folder/'database.db',
                               CLOUD_MODE=False, DEMO_MODE=False, SCHEDULER_ENABLED=False,
                               AUTOMATION_PAUSE_FILE=folder/'automation.paused').items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        module = importlib.import_module('app')
        self.app = module.create_app()
        self.client = self.app.test_client()
        models.execute('INSERT INTO orders(id,order_no,status,product_title) VALUES(1,?,?,?)', ('offline-local-1','pending','测试图书'))
        self.origin = 'http://127.0.0.1:5000'
        response = self.client.get('/orders', base_url=self.origin)
        self.token = re.search(r'data-csrf="([^"]+)"', response.get_data(as_text=True)).group(1)

    def post(self, **headers):
        return self.client.post('/orders/1/browser_target', base_url=self.origin,
                                headers={'Origin':self.origin, 'X-CSRF-Token':self.token, **headers})

    def test_local_read_requires_session_csrf_and_same_origin(self):
        for headers in ({'X-CSRF-Token':''}, {'Origin':'https://foreign.invalid'}, {'Origin':''},
                        {'Origin':'https://127.0.0.1:5000'}):
            self.assertEqual(self.post(**headers).status_code,403)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post('/orders/1/browser_target',base_url=self.origin,
                         headers={'Origin':self.origin,'X-CSRF-Token':self.token}).status_code,403)

    def test_local_read_uses_latest_receiver_and_rejects_closed_order(self):
        with patch('services.checkout_task.fresh_receiver',return_value=RECEIVER) as fresh:
            result = self.post()
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json['target']['receiver'],RECEIVER)
            self.assertEqual(result.headers['Cache-Control'],'no-store')
            fresh.assert_called_once()
            models.execute("UPDATE orders SET status='closed' WHERE id=1")
            self.assertEqual(self.post().status_code,400)
            self.assertEqual(fresh.call_count,1)

    def test_remote_read_failure_never_returns_cached_private_address(self):
        with patch('services.checkout_task.fresh_receiver',side_effect=ValueError('平台已退款')):
            result=self.post()
            self.assertEqual(result.status_code,400)
            self.assertNotIn('receiver',result.json)

    def test_rebinding_host_is_rejected_and_install_file_is_local(self):
        self.assertEqual(self.client.get('/orders',base_url='http://foreign.invalid:5000').status_code,403)
        response=self.client.get('/scripts/kfz_address.user.js',base_url=self.origin)
        self.assertEqual(response.status_code,200)
        script=response.get_data(as_text=True)
        self.assertIn('// @match        http://127.0.0.1:5000/orders*',script)
        self.assertNotIn('GM_xmlhttpRequest',script)
        response.close()
        settings=self.client.get('/settings',base_url=self.origin).get_data(as_text=True)
        self.assertIn('安装 / 更新本地地址助手',settings)


if __name__ == '__main__':
    unittest.main()
