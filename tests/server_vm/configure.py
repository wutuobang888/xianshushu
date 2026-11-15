from guard import require_guest
require_guest()
from pathlib import Path
import json, os, pty, secrets, select, subprocess, time

os.umask(0o077)
base=Path('/srv/xianshushu'); source=base/'live-app'
password=secrets.token_urlsafe(24)
Path('/root/acceptance-input.json').write_text(json.dumps({'username':'acceptance-user','password':password}))
master,slave=pty.openpty()
p=subprocess.Popen([str(base/'live-venv/bin/python'),str(source/'scripts/init_server_credentials.py')],stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
os.close(slave)
buffer=b''
for prompt,value in [('网站账号:', 'acceptance-user'),('网站密码:',password),('再次输入密码:',password)]:
    deadline=time.monotonic()+10
    while prompt.encode() not in buffer:
        assert time.monotonic()<deadline,'credential prompt timeout'
        if select.select([master],[],[],1)[0]:buffer+=os.read(master,4096)
    os.write(master,(value+'\n').encode())
    buffer=b''
assert p.wait(timeout=10)==0,'credential initialization failed'
os.close(master)
assert Path('/etc/xianshushu/kongfz.key').stat().st_mode&0o777==0o600
env=(source/'deploy/production.env.example').read_text()
env=env.replace('SET_DURING_INSTALLATION',secrets.token_urlsafe(48))
env+='\nXGJ_BASE_URL=http://127.0.0.1:17777\n'
Path('/etc/xianshushu-production.env').write_text(env)
for name in ['/etc/xianshushu-production.env','/etc/xianshushu/web-access.json','/etc/xianshushu/kongfz.key']:
    os.chown(name,0,33);os.chmod(name,0o640)
subprocess.run(['runuser','-u','www-data','--','touch',str(base/'live-data/Xauthority')],check=True)
subprocess.run(['runuser','-u','www-data','--','xauth','-f',str(base/'live-data/Xauthority'),'add',':99','.',secrets.token_hex(16)],check=True)
import shutil
for file in (source/'deploy').glob('xianshushu-*.service'):
    shutil.copyfile(file,Path('/etc/systemd/system')/file.name)
Path('/etc/nginx/snippets/xianshushu-browser.locations').write_text((source/'deploy/browser-nginx.locations').read_text().replace('books.example.com','books.acceptance.test'))
Path('/etc/hosts').open('a').write('\n127.0.0.1 books.acceptance.test\n')
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','3','-subj','/CN=books.acceptance.test','-addext','subjectAltName=DNS:books.acceptance.test','-keyout','/etc/nginx/acceptance.key','-out','/etc/nginx/acceptance.crt'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path('/etc/nginx/conf.d/acceptance.conf').write_text('''server {
 listen 80;
 server_name books.acceptance.test;
 return 301 https://$host$request_uri;
}
server {
 listen 443 ssl;
 server_name books.acceptance.test;
 ssl_certificate /etc/nginx/acceptance.crt;
 ssl_certificate_key /etc/nginx/acceptance.key;
 location / {
  proxy_pass http://127.0.0.1:5051;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Real-IP $remote_addr;
 }
 include /etc/nginx/snippets/xianshushu-browser.locations;
}
''')
subprocess.run(['nginx','-t'],check=True)
subprocess.run(['systemctl','enable','xianshushu-display','xianshushu-window-manager','xianshushu-vnc','xianshushu-browser','xianshushu-web','nginx'],check=True)
print('Configured new isolated instance with interactive credential script; no production settings used')

shutil.copyfile(source/'tests/server_vm/fixture_browser.py', base/'live-data/fixture-browser.py')
Path('/etc/systemd/system/xss-acceptance-headed.service').write_text('[Unit]\nRequires=xianshushu-display.service\nAfter=xianshushu-display.service\nJoinsNamespaceOf=xianshushu-display.service\n[Service]\nUser=www-data\nGroup=www-data\nEnvironmentFile=/etc/xianshushu-production.env\nExecStart=/srv/xianshushu/live-venv/bin/python /srv/xianshushu/live-data/fixture-browser.py\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\nReadWritePaths=/srv/xianshushu/live-data\n')
