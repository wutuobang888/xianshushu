"""加密接口会话与浏览器回退；模拟接口，不访问真实账号。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from services import kongfz_address_session as session
from services.browser_session import BrowserBusyError

COOKIE=dict(name='test',value='private-test-value',domain='.kongfz.com',path='/',expires=-1,httpOnly=True,secure=True,sameSite='Lax')


class AddressSessionTests(unittest.TestCase):
    def setUp(self):
        self.runtime=Mock()
        self.request=self.runtime.request.new_context.return_value
        self.browser=Mock()
        self.browser.new_page.return_value.evaluate.return_value='test-browser-agent'
        self.launch=Mock(return_value=self.browser)
        self.authenticate=Mock()
        self.verify=Mock()
        self.cached=patch.object(session,'_load',return_value={'cookies':[COOKIE],'origins':[]})
        self.cached.start();self.addCleanup(self.cached.stop)
        self.save=patch.object(session,'save')
        self.saved=self.save.start();self.addCleanup(self.save.stop)

    def open(self):
        return session.open_session(self.runtime,self.launch,self.authenticate,self.verify)

    def test_valid_cache_never_takes_browser_even_if_monitor_holds_it(self):
        self.launch.side_effect=BrowserBusyError('后台占用')
        with self.open() as context:
            self.assertIs(context.request,self.request)
            self.saved.assert_not_called()
        self.launch.assert_not_called();self.authenticate.assert_not_called()
        self.saved.assert_called_once_with(self.request,user_agent=None);self.request.dispose.assert_called_once()

    def test_cached_session_preserves_browser_user_agent(self):
        with patch.object(session,'_load',return_value={'cookies':[COOKIE],'origins':[],'user_agent':'test-browser-agent'}):
            with self.open():pass
        self.runtime.request.new_context.assert_called_once_with(storage_state={'cookies':[COOKIE],'origins':[]},user_agent='test-browser-agent')
        self.saved.assert_called_once_with(self.request,user_agent='test-browser-agent')

    def test_expired_cache_only_reads_then_falls_back_to_browser(self):
        self.verify.side_effect=[ValueError('未登录'),None]
        with self.open() as context:self.assertIs(context,self.browser)
        self.request.dispose.assert_called_once();self.authenticate.assert_called_once_with(self.browser)
        self.browser.close.assert_called_once();self.saved.assert_called_once_with(self.browser.request,user_agent='test-browser-agent')

    def test_missing_cache_uses_existing_login_flow(self):
        with patch.object(session,'_load',return_value=None):
            with self.open() as context:self.assertIs(context,self.browser)
        self.runtime.request.new_context.assert_not_called();self.authenticate.assert_called_once()

    def test_business_failure_does_not_save_and_disposes_request(self):
        with self.assertRaises(ValueError):
            with self.open():raise ValueError('地址回读不符')
        self.saved.assert_not_called();self.request.dispose.assert_called_once()

    def test_login_failure_always_releases_browser(self):
        self.authenticate.side_effect=ValueError('需要验证码')
        with patch.object(session,'_load',return_value=None),self.assertRaises(ValueError):
            with self.open():pass
        self.browser.close.assert_called_once();self.saved.assert_not_called()

    def test_invalid_cached_cookie_shape_falls_back(self):
        self.runtime.request.new_context.side_effect=ValueError('格式异常')
        with self.open() as context:self.assertIs(context,self.browser)
        self.browser.close.assert_called_once()

    def test_cache_write_failure_does_not_report_verified_address_as_failed(self):
        self.saved.side_effect=OSError('磁盘不可写')
        with self.assertLogs(session.__name__,level='WARNING'):
            with self.open():pass
        self.request.dispose.assert_called_once()

    def test_browser_busy_wait_is_bounded_and_message_is_specific(self):
        launch=Mock(side_effect=BrowserBusyError('占用'))
        with patch.object(session.time,'monotonic',side_effect=[0,1,16]),patch.object(session.time,'sleep') as sleep:
            with self.assertRaisesRegex(BrowserBusyError,'会话需要刷新'):session._launch_wait(launch)
        self.assertEqual(launch.call_count,2);sleep.assert_called_once()

    def test_browser_released_during_wait_is_reused(self):
        launch=Mock(side_effect=[BrowserBusyError('占用'),self.browser])
        with patch.object(session.time,'monotonic',side_effect=[0,1]),patch.object(session.time,'sleep'):
            self.assertIs(session._launch_wait(launch),self.browser)

    def test_snapshot_encrypted_private_and_scoped_to_official_cookies(self):
        self.save.stop()
        self.cached.stop()
        from services import kongfz_auth
        cipher=Fernet(Fernet.generate_key())
        request=Mock()
        request.storage_state.return_value={'cookies':[COOKIE,dict(COOKIE,domain='evil.test'),dict(COOKIE,domain='kongfz.com.evil.test')],
                                            'origins':[{'origin':'https://evil.test','localStorage':[]}]}
        with tempfile.TemporaryDirectory() as directory,patch.object(session.config,'DATA_DIR',Path(directory)),patch.object(kongfz_auth,'_cipher',return_value=cipher):
            session.save(request)
            content=session._path().read_bytes()
            self.assertNotIn(b'private-test-value',content)
            state=session._load()
            self.assertEqual(state,{'cookies':[COOKIE],'origins':[]})
            if os.name!='nt':self.assertEqual(session._path().stat().st_mode & 0o777,0o600)
            session._path().write_bytes(b'broken')
            self.assertIsNone(session._load())


if __name__=='__main__':unittest.main()
