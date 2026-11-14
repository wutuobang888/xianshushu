"""采购地址接口复用加密登录会话，避免与后台查单争用浏览器。"""
from contextlib import contextmanager
import json
import logging
import os
import time
from types import SimpleNamespace
import uuid

import config
from services.browser_session import BrowserBusyError


def _path():
    return config.DATA_DIR / 'kongfz-address-session.enc'


def _official_state(state):
    cookies = state.get('cookies')
    if not isinstance(cookies, list):
        raise ValueError('会话格式不符')
    result = {'cookies': [cookie for cookie in cookies if isinstance(cookie, dict)
                        and (cookie.get('domain', '').lstrip('.') == 'kongfz.com'
                             or cookie.get('domain', '').endswith('.kongfz.com'))],
            'origins': []}
    if isinstance(state.get('user_agent'), str) and state['user_agent']:
        result['user_agent'] = state['user_agent']
    return result


def _load():
    from services.kongfz_auth import _cipher
    try:
        return _official_state(json.loads(_cipher().decrypt(_path().read_bytes())))
    except Exception:
        # 旧文件保留；不可解密或已失效时只回退到既有登录流程。
        return None


def save(request, *, user_agent=None):
    from services.kongfz_auth import _cipher
    state = _official_state(request.storage_state())
    if user_agent:
        state['user_agent'] = user_agent
    encrypted = _cipher().encrypt(json.dumps(state).encode())
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(encrypted)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _launch_wait(launch):
    deadline = time.monotonic() + 15
    while True:
        try:
            return launch()
        except BrowserBusyError:
            if time.monotonic() >= deadline:
                raise BrowserBusyError('孔夫子登录会话需要刷新，后台查单或登录窗口仍在使用浏览器，请稍后重试') from None
            time.sleep(0.25)


@contextmanager
def open_session(runtime, launch, authenticate, verify):
    request = None
    browser = None
    user_agent = None
    try:
        state = _load()
        if state and state['cookies']:
            try:
                user_agent = state.get('user_agent')
                request = runtime.request.new_context(storage_state={'cookies':state['cookies'],'origins':[]},
                                                       **({'user_agent':user_agent} if user_agent else {}))
                context = SimpleNamespace(request=request)
                verify(context)  # 只读验证成功才允许进入地址写入流程。
            except Exception:
                if request is not None:
                    request.dispose()
                request = None
        if request is None:
            browser = _launch_wait(launch)
            authenticate(browser)
            verify(browser)
            context = browser
            user_agent = browser.new_page().evaluate('navigator.userAgent')
        yield context
        try:
            save(context.request,user_agent=user_agent)  # 回读及订单复核通过后才更新密文。
        except Exception:
            logging.getLogger(__name__).warning('孔夫子地址会话缓存未更新；本次地址回读核对结果不受影响')
    finally:
        if request is not None:
            request.dispose()
        if browser is not None:
            browser.close()
