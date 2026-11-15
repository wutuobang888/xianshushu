from guard import require_guest
require_guest()
from pathlib import Path
import json,re,sqlite3,subprocess,time
import requests
names=['xianshushu-display','xianshushu-window-manager','xianshushu-vnc','xianshushu-browser','xianshushu-web','xianshushu-worker']
for _ in range(100):
    states={name:subprocess.run(['systemctl','is-active',name],capture_output=True,text=True).stdout.strip() for name in names}
    if set(states.values())=={'active'}:break
    time.sleep(.2)
assert set(states.values())=={'active'},states
client=requests.Session();client.trust_env=False;client.verify='/etc/nginx/acceptance.crt'
for _ in range(100):
    try:
        response=client.get('https://books.acceptance.test/login')
        if response.status_code==200:break
    except requests.RequestException:pass
    time.sleep(.2)
token=re.search('name="csrf_token" value="([^"]+)"',response.text)[1]
credentials=json.loads(Path('/root/acceptance-input.json').read_text())
response=client.post('https://books.acceptance.test/login',data={**credentials,'csrf_token':token})
assert response.status_code==200 and '验收图书1' in response.text
with sqlite3.connect('/srv/xianshushu/live-data/database.db') as db:
    assert db.execute('SELECT count(*) FROM orders').fetchone()[0]==2
path=Path('/root/server-acceptance-report.json');report=json.loads(path.read_text())
report['checks']['full_vm_restart_autostart_and_data_persistence']=states
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
subprocess.run(['systemctl','stop','xianshushu-worker'],check=True)
print('Full independent VM restart: all six services auto-started, HTTPS login and two synthetic orders preserved')
