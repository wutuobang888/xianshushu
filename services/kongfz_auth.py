"""孔夫子凭据加密保存及有限次数重登；验证码始终留给人工处理。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

import config


def _cipher():
    from cryptography.fernet import Fernet
    if not config.KONGFZ_KEY_FILE:
        raise ValueError("服务器尚未配置登录凭据加密密钥")
    return Fernet(Path(config.KONGFZ_KEY_FILE).read_bytes().strip())


def save_credentials(username, password):
    if not username or not password:
        raise ValueError("请填写孔夫子账号和密码")
    content = json.dumps({"username":username.strip(),"password":password},ensure_ascii=False).encode()
    encrypted = _cipher().encrypt(content)
    config.KONGFZ_CREDENTIAL_FILE.parent.mkdir(parents=True,exist_ok=True)
    # 新文件只对服务用户可读；已有文件沿用部署时设置的权限。
    descriptor = os.open(config.KONGFZ_CREDENTIAL_FILE,os.O_CREAT | os.O_WRONLY | os.O_TRUNC,0o600)
    with os.fdopen(descriptor,"wb") as target:
        target.write(encrypted)
    set_login_status("saved",last_attempt=0)


def load_credentials():
    if not config.KONGFZ_CREDENTIAL_FILE.exists():
        return None
    return json.loads(_cipher().decrypt(config.KONGFZ_CREDENTIAL_FILE.read_bytes()))


def public_status():
    try:
        state = json.loads(config.KONGFZ_LOGIN_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        state = {}
    return {"configured":config.KONGFZ_CREDENTIAL_FILE.exists(),
            "state":state.get("state","unknown"),"updated_at":state.get("updated_at"),
            "last_attempt":state.get("last_attempt",0),"login_pid":state.get("login_pid")}


def set_login_status(state, **extra):
    previous = public_status()
    previous.update(state=state,updated_at=int(time.time()),**extra)
    config.KONGFZ_LOGIN_STATUS_FILE.parent.mkdir(parents=True,exist_ok=True)
    config.KONGFZ_LOGIN_STATUS_FILE.write_text(json.dumps(previous),encoding="utf-8")


def try_login(page):
    """只在孔夫子官方 HTTPS 密码页尝试一次，失败后冷却30分钟，绝不处理验证码。"""
    if not config.CLOUD_MODE:
        return False
    url = urlparse(page.url)
    if url.scheme != "https" or url.hostname != "login.kongfz.com":
        return False
    if time.time() - public_status()["last_attempt"] < 1800:
        return False
    try:
        credentials = load_credentials()
        if not credentials:
            set_login_status("login_required")
            return False
        username = page.locator("#username")
        password = page.locator("#password")
        if not username.is_visible() or not password.is_visible():
            mode = page.get_by_text("其他登录方式",exact=True)
            if mode.count() == 1 and mode.is_visible():
                mode.click()
                try:
                    password.wait_for(state="visible",timeout=5000)
                except Exception:
                    pass
        if not username.is_visible() or not password.is_visible():
            set_login_status("verification_required")
            return False
        if any(field.is_visible() for field in page.locator("#login input[name=verify]").all()):
            set_login_status("verification_required",last_attempt=int(time.time()))
            return False
        set_login_status("logging_in",last_attempt=int(time.time()))
        username.fill(credentials["username"])
        password.fill(credentials["password"])
        page.locator("#login input[name=checkbox]").check()
        page.locator("#login input[type=submit]").click()
        try:
            page.wait_for_url(lambda value: urlparse(str(value)).hostname != "login.kongfz.com",timeout=15000)
        except Exception:
            set_login_status("verification_required")
            return False
        page.goto("https://order.kongfz.com/order-views/management/buyer/list",wait_until="domcontentloaded",timeout=45000)
        page.wait_for_timeout(3000)
        from bots.kongfz_bot import _looks_logged_in
        if _looks_logged_in(page):
            set_login_status("logged_in")
            return True
        set_login_status("verification_required")
    except Exception:
        # 不记录 Playwright 原始异常，避免将输入内容或页面隐私写入日志。
        set_login_status("login_failed",last_attempt=int(time.time()))
    return False


def start_login_window():
    if not config.CLOUD_MODE:
        raise ValueError("本地请使用原来的孔夫子登录命令")
    pid = public_status().get("login_pid")
    if pid:
        try:
            os.kill(int(pid),0)
            return
        except (OSError,ValueError):
            pass
    process = subprocess.Popen([sys.executable,"-u","-m","bots.kongfz_bot","login"],
                               cwd=str(config.BASE_DIR),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    set_login_status("window_opening",login_pid=process.pid)
