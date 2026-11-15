from guard import require_guest
require_guest()
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import base64,json,os,re,socket,ssl,sqlite3,subprocess,sys,threading,time
import requests
from playwright.sync_api import sync_playwright

ROOT=Path('/srv/xianshushu/live-app');DATA=Path('/srv/xianshushu/live-data')
PYTHON='/srv/xianshushu/live-venv/bin/python';BASE='https://books.acceptance.test'
credentials=json.loads(Path('/root/acceptance-input.json').read_text())
report={'environment':'fresh Debian 13 QEMU virtual machine; synthetic platform data','checks':{}}
def record(name,value=True):
    report['checks'][name]=value
    Path('/root/server-acceptance-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(name+': PASS',flush=True)

calls=[]
receivers={i:dict(receiver_name='验收买家'+str(i),receiver_mobile='1380000000'+str(i),prov_name='浙江省',city_name='杭州市',area_name='西湖区',town_name='测试街道',address='验收路'+str(i)+'号') for i in [1,2]}
orders={f'ACCEPTANCE-{i}':dict(order_no=f'ACCEPTANCE-{i}',seller_name='acceptance-shop',buyerName='合成买家'+str(i),title='验收图书'+str(i),pay_amount=1500,order_status=12,refund_status=0,**receivers[i]) for i in [1,2]}
class Platform(BaseHTTPRequestHandler):
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
        path=self.path.split('?')[0];calls.append(path)
        if path.endswith('/user/authorize/list'):value=[dict(user_name='acceptance-shop',user_nick='验收店铺',is_valid=True,authorize_id=1)]
        elif path.endswith('/order/list'):value=list(orders.values())
        elif path.endswith('/order/detail'):value=orders.get(data.get('order_no'),{})
        elif path.endswith('/refund/list') or path.endswith('/product/list'):value=[]
        else:self.send_response(405);self.end_headers();return
        body=json.dumps(dict(code=0,data=value)).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass
stub=ThreadingHTTPServer(('127.0.0.1',17777),Platform)
threading.Thread(target=stub.serve_forever,daemon=True).start()
client=requests.Session();client.trust_env=False;client.verify='/etc/nginx/acceptance.crt'
def csrf():
    text=client.get(BASE+'/settings').text
    return re.search(r'data-csrf="([^"]+)"',text)[1]
def login():
    text=client.get(BASE+'/login').text
    token=re.search(r'name="csrf_token" value="([^"]+)"',text)[1]
    response=client.post(BASE+'/login',data={**credentials,'csrf_token':token},allow_redirects=False)
    assert response.status_code==302,response.status_code
    return response
def websocket(session,origin):
    context=ssl.create_default_context(cafile='/etc/nginx/acceptance.crt')
    sock=context.wrap_socket(socket.create_connection(('127.0.0.1',443),timeout=5),server_hostname='books.acceptance.test')
    cookie='; '.join(f'{c.name}={c.value}' for c in session.cookies)
    headers=f'GET /browser/websockify HTTP/1.1\r\nHost: books.acceptance.test\r\nOrigin: {origin}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\nCookie: {cookie}\r\n\r\n'
    sock.sendall(headers.encode());data=b''
    while b'\r\n\r\n' not in data:data+=sock.recv(4096)
    status=int(data.split(b' ',2)[1])
    if status==101:
        deadline=time.monotonic()+5
        while b'RFB ' not in data and time.monotonic()<deadline:data+=sock.recv(4096)
        assert b'RFB ' in data,'no actual VNC greeting'
    sock.close();return status


try:
    services=['xianshushu-display','xianshushu-window-manager','xianshushu-vnc','xianshushu-browser','xianshushu-web']
    for name in services:assert subprocess.check_output(['systemctl','is-active',name],text=True).strip()=='active',name
    record('systemd_templates_started',services)
    for _ in range(100):
        ports=subprocess.check_output(['ss','-ltn'],text=True)
        if all(f'127.0.0.1:{p}' in ports for p in [5051,5900,6080]):break
        time.sleep(.1)
    for port in [5051,5900,6080]:
        assert f'127.0.0.1:{port}' in ports,ports
        assert f'0.0.0.0:{port}' not in ports and f'[::]:{port}' not in ports,ports
    record('backend_ports_loopback_only')
    r=client.get('http://books.acceptance.test/orders?check=1',allow_redirects=False)
    assert r.status_code==301 and r.headers.get('Location')==BASE+'/orders?check=1', (r.status_code,r.headers.get('Location'))
    r=client.get(BASE+'/orders',allow_redirects=False);assert r.status_code==302
    assert client.post(BASE+'/settings/xgj',data={}).status_code==401
    assert client.get(BASE+'/browser/vnc.html',allow_redirects=False).status_code==302
    assert websocket(client,BASE)==401
    record('anonymous_http_https_and_browser_protection')
    assert client.post(BASE+'/login',data=credentials).status_code==403
    response=login()
    cookie=response.headers['Set-Cookie'];assert all(x in cookie for x in ['Secure','HttpOnly','SameSite=Strict'])
    pages=['/orders','/publish','/shop_products','/hotlist','/templates','/reports','/customers','/settings']
    statuses={p:client.get(BASE+p).status_code for p in pages};assert set(statuses.values())=={200},statuses
    assert client.get(BASE+'/internal/browser-auth').status_code==404
    token=csrf()
    assert client.post(BASE+'/settings/xgj',data={}).status_code==403
    assert client.post(BASE+'/settings/xgj',data={},headers={'Origin':'https://wrong.test','X-CSRF-Token':token}).status_code==403
    assert client.get(BASE+'/browser/vnc.html').status_code==200
    assert websocket(client,'https://wrong.test')==403
    assert websocket(client,BASE)==101
    record('login_csrf_origin_and_actual_vnc_websocket',statuses)
    # 实际设置/同步入口，经原接口客户端访问隔离平台桩。
    assert client.post(BASE+'/settings/xgj',data={'app_key':'offline-key','app_secret':'offline-secret','csrf_token':token}).status_code==200
    assert client.post(BASE+'/orders/sync',data={'csrf_token':csrf()}).status_code==200
    with sqlite3.connect(DATA/'database.db') as db:
        rows=db.execute('SELECT id,order_no FROM orders ORDER BY id').fetchall()
    assert len(rows)==2,rows
    for oid,no in rows:
        r=client.post(BASE+f'/orders/{oid}/browser_target',headers={'Origin':BASE,'X-CSRF-Token':csrf()})
        assert r.status_code==200,r.status_code
        receiver=r.json()['target']['receiver'];expected=receivers[int(no[-1])]
        assert all(receiver[k]==v for k,v in expected.items())
    record('settings_sync_and_per_order_address_api',{'orders':2,'real_platform_calls':0})
    # 网站实际加密保存入口；仅合成账号，验证密文、权限与解密。
    fake_password='synthetic-kongfz-password'
    assert client.post(BASE+'/settings/kongfz',data={'username':'synthetic-kongfz-user','password':fake_password,'csrf_token':csrf()}).status_code==200
    encrypted=DATA/'kongfz-credentials.enc'
    assert fake_password.encode() not in encrypted.read_bytes() and encrypted.stat().st_mode&0o777==0o600
    from cryptography.fernet import Fernet
    decrypted=json.loads(Fernet(Path('/etc/xianshushu/kongfz.key').read_bytes().strip()).decrypt(encrypted.read_bytes()))
    assert decrypted['password']==fake_password
    record('credential_encryption_permissions_and_decryption')
    # 真实独立 worker，缩短测试订单/退款间隔到一分钟；不改发布模板。
    with Path('/etc/xianshushu-production.env').open('a') as f:
        f.write('\nORDER_SYNC_INTERVAL_MINUTES=1\nREFUND_SYNC_INTERVAL_MINUTES=1\nKONGFZ_CHECK_INTERVAL_MINUTES=60\n')
    before=len([x for x in calls if x.endswith('/order/list')])
    subprocess.run(['systemctl','start','xianshushu-worker'],check=True)
    for _ in range(100):
        logs=subprocess.check_output(['journalctl','-u','xianshushu-worker','--since','-20 seconds','--no-pager'],text=True)
        if 'Scheduler started' in logs:break
        time.sleep(.1)
    else:raise AssertionError('worker scheduler did not start; inspect isolated service logs')
    env=os.environ.copy()
    for line in Path('/etc/xianshushu-production.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key,value=line.split('=',1);env[key]=value
    env['XIANSHUSHU_SCHEDULER']='1'
    env['PLAYWRIGHT_BROWSERS_PATH']='/srv/xianshushu/browsers'
    duplicate=subprocess.run(['runuser','-u','www-data','--',PYTHON,'worker.py'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
    assert duplicate.returncode!=0 and '另一个定时任务进程已在运行' in duplicate.stderr
    record('worker_single_instance_lock')
    # 真正打开服务显示中的有头浏览器，并由 noVNC 客户端连接显示。
    launched=time.time()
    subprocess.run(['systemctl','start','xss-acceptance-headed'],check=True)
    for _ in range(100):
        ready=DATA/'fixture-browser-ready'
        if ready.exists() and ready.stat().st_mtime>=launched:break
        assert subprocess.run(['systemctl','is-active','--quiet','xss-acceptance-headed']).returncode==0,'headed browser failed'
        time.sleep(.1)
    else:raise AssertionError('headed browser did not show test page')
    os.environ['PLAYWRIGHT_BROWSERS_PATH']='/srv/xianshushu/browsers'
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch(headless=True,args=['--no-sandbox'])
        context=browser.new_context(ignore_https_errors=True,viewport={'width':1280,'height':800})
        page=context.new_page();page.goto(BASE+'/login')
        page.locator('input[name=username]').fill(credentials['username']);page.locator('input[name=password]').fill(credentials['password'])
        page.locator('button[type=submit]').click();page.wait_for_url('**/orders')
        page.goto(BASE+'/browser/vnc.html?autoconnect=1&resize=scale&path=/browser/websockify')
        page.wait_for_function("document.documentElement.classList.contains('noVNC_connected')",timeout=30000)
        page.locator('#noVNC_transition').wait_for(state='hidden',timeout=15000)
        assert subprocess.run(['systemctl','is-active','--quiet','xss-acceptance-headed']).returncode==0,'headed browser did not start'
        page.screenshot(path='/root/server-novnc-verification.png')
        context.close();browser.close()
    record('headed_chromium_and_real_novnc_client')
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        if len([x for x in calls if x.endswith('/order/list')])>before:break
        time.sleep(1)
    else:raise AssertionError('worker scheduled order job did not execute')
    assert subprocess.check_output(['systemctl','is-active','xianshushu-worker'],text=True).strip()=='active'
    record('worker_scheduled_sync_executed')
    subprocess.run(['systemctl','stop','xianshushu-worker'],check=True)
    subprocess.run(['systemctl','restart','xianshushu-web'],check=True)
    for _ in range(50):
        try:
            if client.get(BASE+'/orders').status_code==200:break
        except requests.RequestException:pass
        time.sleep(.2)
    assert '验收图书1' in client.get(BASE+'/orders').text
    record('restart_preserves_database_and_login_session')
    record('package_environment',{'python':sys.version.split()[0],'pip_check':subprocess.check_output([PYTHON,'-m','pip','check'],text=True).strip()})
    report['passed']=True;report['real_purchases_submitted']=0;report['real_payments']=0
    record('all_checks_complete')
finally:
    subprocess.run(['systemctl','stop','xianshushu-worker'],check=False)
    subprocess.run(['systemctl','stop','xss-acceptance-headed'],check=False)
    stub.shutdown()
    Path('/root/server-acceptance-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
