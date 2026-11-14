"""单用户正式站入口：会话登录、CSRF 和隐私响应保护。"""
import hmac
import secrets
from urllib.parse import urlparse

from flask import abort, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
import config

if not config.CLOUD_MODE or config.DEMO_MODE or config.SCHEDULER_ENABLED:
    raise RuntimeError("正式 Web 入口需要 CLOUD_MODE=1、DEMO_MODE=0、SCHEDULER=0")
if len(config.SECRET_KEY) < 32:
    raise RuntimeError("正式实例需要独立的会话密钥")

from app import app

app.wsgi_app = ProxyFix(app.wsgi_app,x_proto=1,x_host=1)
app.config.update(SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',MAX_CONTENT_LENGTH=8*1024*1024)

def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

app.jinja_env.globals['csrf_token'] = csrf_token

from services.web_auth import install
install(app)

@app.before_request
def protect_write_requests():
    if request.method in ('GET','HEAD','OPTIONS'):
        return
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        abort(403)
    expected = session.get('csrf_token','')
    submitted = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token','')
    if not expected or not hmac.compare_digest(expected,submitted):
        abort(403)

@app.after_request
def private_headers(response):
    response.headers.update({'Cache-Control':'no-store','X-Robots-Tag':'noindex, nofollow',
                             'X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN',
                             'Referrer-Policy':'same-origin'})
    return response
