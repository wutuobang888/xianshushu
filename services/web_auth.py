"""单用户站点会话登录，同时保护网站和服务器浏览器。"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

from flask import abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


def install(app):
    credential = json.loads(Path(os.environ.get('XIANSHUSHU_ACCESS_FILE','/etc/xianshushu/web-access.json')).read_text())
    username = credential['username']
    password_hash = credential['password_hash']
    version = hashlib.sha256(password_hash.encode()).hexdigest()
    attempts = {}
    attempt_lock = threading.Lock()

    def authenticated():
        return (session.get('web_user') == username and session.get('web_version') == version
                and 0 <= time.time()-session.get('web_login_at',0) < 12*3600)

    def next_path(value):
        value = value or '/orders'
        parsed = urlparse(value)
        return value if value.startswith('/') and not value.startswith('//') and '\\' not in value and not parsed.netloc and not parsed.scheme else '/orders'

    @app.before_request
    def require_login():
        if request.endpoint in ('web_login','browser_auth','static'):
            return
        # 安装包不含订单、凭据或会话；仅公开这个固定JS文件供油猴安装及更新。
        if request.endpoint == 'products.userscript' and request.view_args.get('fname') == 'kfz_address.user.js' and request.method == 'GET':
            return
        if authenticated():
            return
        if request.method not in ('GET','HEAD'):
            abort(401)
        return redirect(url_for('web_login',next=request.full_path.rstrip('?')))

    @app.route('/login',methods=['GET','POST'])
    def web_login():
        destination = next_path(request.values.get('next'))
        if authenticated():
            return redirect(destination)
        error = ''
        if request.method == 'POST':
            key = request.headers.get('X-Real-IP') or request.remote_addr or 'unknown'
            now = time.time()
            with attempt_lock:
                # 有界记录，避免公开登录入口造成无限内存增长。
                for address in list(attempts):
                    if now-attempts[address][0] >= 900:
                        attempts.pop(address)
                if len(attempts) >= 2048 and key not in attempts:
                    abort(429)
                start,count = attempts.get(key,(now,0))
                if count >= 10:
                    error = '尝试次数较多，请15分钟后再试。'
                else:
                    attempts[key] = (start,count+1)
            if not error:
                supplied_user = request.form.get('username','')
                valid_password = check_password_hash(password_hash,request.form.get('password',''))
                if hmac.compare_digest(supplied_user.encode(),username.encode()) and valid_password:
                    session.clear()
                    session.update(web_user=username,web_version=version,web_login_at=now)
                    with attempt_lock:
                        attempts.pop(key,None)
                    return redirect(destination)
                error = '账号或密码不正确，请使用原网站访问账号。'
        return render_template('login.html',error=error,next_path=destination)

    @app.route('/internal/browser-auth')
    def browser_auth():
        return ('',204) if authenticated() else ('',401)

    @app.route('/logout',methods=['POST'])
    def web_logout():
        session.clear()
        return redirect(url_for('web_login'))
