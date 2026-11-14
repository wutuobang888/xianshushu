"""云端运行回归；使用保留的隔离测试目录及模拟浏览器，不读取真实登录态。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import uuid

import config
from services import kongfz_auth
from services.browser_session import launch_persistent, BrowserBusyError
from services.kongfz_monitor import _address_key

ARTIFACTS = Path(__file__).resolve().parents[1] / "backups" / "test-artifacts"

def test_directory():
    directory = ARTIFACTS / ("cloud-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    return directory


class BrowserSessionTests(unittest.TestCase):
    def test_province_alias_keeps_street_and_building_exact(self):
        self.assertEqual(_address_key('广西壮族自治区测试市测试街1号'),_address_key('广西测试市测试街1号'))
        self.assertNotEqual(_address_key('广西壮族自治区测试市测试街1号'),_address_key('广西测试市测试街2号'))

    def test_busy_profile_is_not_opened_twice_and_close_releases_lock(self):
        profile = test_directory() / "profile"
        browser = Mock()
        first = launch_persistent(browser,profile,headless=True)
        try:
            with self.assertRaises(BrowserBusyError):
                launch_persistent(browser,profile,headless=True)
            self.assertEqual(browser.launch_persistent_context.call_count,1)
        finally:
            first.close()
        second = launch_persistent(browser,profile,headless=True)
        second.close()

    def test_failed_launch_releases_profile_lock(self):
        browser = Mock()
        browser.launch_persistent_context.side_effect = RuntimeError("offline failure")
        profile = test_directory() / "profile"
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,"offline failure"):
                launch_persistent(browser,profile)


class LoginProtectionTests(unittest.TestCase):
    def test_no_credentials_transmitted_to_other_domain_or_http(self):
        for url in ('https://example.com/','http://login.kongfz.com/'):
            page = Mock(url=url)
            with patch.object(config,'CLOUD_MODE',True), patch.object(kongfz_auth,'load_credentials') as load:
                self.assertFalse(kongfz_auth.try_login(page))
                load.assert_not_called()
                page.locator.assert_not_called()

    def test_failed_login_cooldown_prevents_repeated_attempts(self):
        page = Mock(url='https://login.kongfz.com/')
        with patch.object(config,'CLOUD_MODE',True), patch.object(kongfz_auth,'public_status',return_value={'last_attempt':time.time()}), \
                patch.object(kongfz_auth,'load_credentials') as load:
            self.assertFalse(kongfz_auth.try_login(page))
            load.assert_not_called()

    def test_visible_captcha_is_left_untouched(self):
        page = Mock(url='https://login.kongfz.com/')
        fields = {'#username':Mock(),'#password':Mock(),'#login input[name=verify]':Mock()}
        fields['#login input[name=verify]'].all.return_value = [Mock()]
        page.locator.side_effect = lambda selector:fields[selector]
        with patch.object(config,'CLOUD_MODE',True), patch.object(kongfz_auth,'public_status',return_value={'last_attempt':0}), \
                patch.object(kongfz_auth,'load_credentials',return_value={'username':'offline-user','password':'offline-password'}), \
                patch.object(kongfz_auth,'set_login_status'):
            self.assertFalse(kongfz_auth.try_login(page))
        fields['#username'].fill.assert_not_called()
        fields['#password'].fill.assert_not_called()

    def test_saved_password_is_encrypted_and_not_in_public_status(self):
        from cryptography.fernet import Fernet
        folder = test_directory()
        key = folder / 'encryption.key'
        key.write_bytes(Fernet.generate_key())
        with patch.object(config,'KONGFZ_KEY_FILE',str(key)), \
                patch.object(config,'KONGFZ_CREDENTIAL_FILE',folder / 'credentials.enc'), \
                patch.object(config,'KONGFZ_LOGIN_STATUS_FILE',folder / 'status.json'):
            kongfz_auth.save_credentials('offline-user','offline-password')
            self.assertNotIn(b'offline-password',(folder / 'credentials.enc').read_bytes())
            self.assertEqual(kongfz_auth.load_credentials()['password'],'offline-password')
            self.assertNotIn('offline-password',json.dumps(kongfz_auth.public_status()))


class ProductionWebTests(unittest.TestCase):
    def test_form_protection_and_real_mode_without_live_business_requests(self):
        folder = test_directory()
        environment = dict(os.environ,XIANSHUSHU_DATA_DIR=str(folder),XIANSHUSHU_DATABASE=str(folder/'test.db'),
            XIANSHUSHU_CLOUD_MODE='1',XIANSHUSHU_SCHEDULER='0',DEMO_MODE='0',FLASK_DEBUG='0',FLASK_SECRET_KEY='offline-test-secret-'*3,
            XIANSHUSHU_ACCESS_FILE=str(folder/'access.json'))
        code = r'''
import json,re,os,time
from pathlib import Path
from werkzeug.security import generate_password_hash
Path(os.environ['XIANSHUSHU_ACCESS_FILE']).write_text(json.dumps({'username':'offline','password_hash':generate_password_hash('offline-test-password')}))
from production_wsgi import app
import models
@app.route('/offline-write',methods=['POST'])
def offline_write(): return {'ok':True}
client=app.test_client()
assert client.get('/orders',base_url='https://test.invalid').status_code == 302
script=client.get('/scripts/kfz_address.user.js',base_url='https://test.invalid')
assert script.status_code == 200 and '@version      2.1.0' in script.get_data(as_text=True)
assert client.post('/orders/1/browser_target',base_url='https://test.invalid').status_code == 401
assert client.post('/orders/1/sync_address',base_url='https://test.invalid').status_code == 401
assert client.get('/internal/browser-auth',base_url='https://test.invalid').status_code == 401
assert client.post('/login',base_url='https://test.invalid').status_code == 403
html=client.get('/login',base_url='https://test.invalid').get_data(as_text=True)
token=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
bad=client.post('/login',base_url='https://test.invalid',data={'username':'offline','password':'incorrect','csrf_token':token})
assert '账号或密码不正确' in bad.get_data(as_text=True)
assert client.get('/internal/browser-auth',base_url='https://test.invalid').status_code == 401
login=client.post('/login',base_url='https://test.invalid',data={'username':'offline','password':'offline-test-password','csrf_token':token,'next':'//evil.invalid'})
assert login.status_code == 302 and login.location == '/orders'
assert client.get('/internal/browser-auth',base_url='https://test.invalid').status_code == 204
assert client.get('/orders/cloud_purchase',base_url='https://test.invalid').json['task'] is None
assert client.post('/orders/999/cloud_purchase',base_url='https://test.invalid').status_code == 403
pages={path:client.get(path,base_url='https://test.invalid').status_code for path in
    ['/orders','/publish','/shop_products','/hotlist','/templates','/reports','/customers','/settings']}
assert set(pages.values()) == {200}, pages
assert models.query('SELECT COUNT(*) c FROM orders',one=True)['c'] == 0
models.execute("INSERT INTO orders(id,order_no,status,isbn,product_title) VALUES(1,'offline-source','pending','9787201077642','测试图书')")
orders_html=client.get('/orders',base_url='https://test.invalid').get_data(as_text=True)
assert 'data-xss-native-source="1"' in orders_html
assert 'data-xss-order=' not in orders_html and '/orders/1/source' in orders_html
assert re.search(r'data-source-url="https?://(?:item|search|book)\.kongfz\.com/',orders_html)
assert '/static/source-immediate.js' in orders_html
html=client.get('/settings',base_url='https://test.invalid').get_data(as_text=True)
token=re.search(r'data-csrf="([^"]+)"',html).group(1)
assert 'DEMO' not in html.upper()
assert client.post('/offline-write',base_url='https://test.invalid').status_code == 403
assert client.post('/orders/1/sync_address',base_url='https://test.invalid').status_code == 403
prepare_page=client.get('/orders/1/source',base_url='https://test.invalid')
assert prepare_page.status_code == 200 and '/orders/1/sync_address' in prepare_page.get_data(as_text=True)
assert client.post('/offline-write',headers={'X-CSRF-Token':token,'Origin':'https://evil.invalid'},base_url='https://test.invalid').status_code == 403
assert client.post('/offline-write',headers={'X-CSRF-Token':token,'Origin':'https://test.invalid'},base_url='https://test.invalid').status_code == 200
assert client.post('/orders/999/cloud_purchase',headers={'X-CSRF-Token':token,'Origin':'https://test.invalid'},base_url='https://test.invalid').status_code == 400
assert client.post('/orders/999/browser_target',headers={'X-CSRF-Token':token,'Origin':'https://test.invalid'},base_url='https://test.invalid').status_code == 400
assert 'id="cloud-purchase"' not in client.get('/orders',base_url='https://test.invalid').get_data(as_text=True)
assert client.post('/logout',headers={'X-CSRF-Token':token},base_url='https://test.invalid').status_code == 302
assert client.get('/orders',base_url='https://test.invalid').status_code == 302
print(json.dumps({'pages':pages,'csrf_verified':True,'no_demo_orders':True}))
'''
        result = subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=str(Path(__file__).resolve().parents[1]),
                                env=environment,capture_output=True,text=True,encoding='utf-8',timeout=40)
        self.assertEqual(result.returncode,0,result.stdout + result.stderr)
        self.assertIn('"csrf_verified": true',result.stdout)
