from guard import require_guest
require_guest()
from pathlib import Path
import json,os,re,subprocess,sys
root=Path('/srv/xianshushu/live-app')
folder=Path('/root/regression-data');folder.mkdir(exist_ok=True)
env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':'/srv/xianshushu/browsers','XIANSHUSHU_SCHEDULER':'0',
     'XIANSHUSHU_CLOUD_MODE':'0','DEMO_MODE':'1','FLASK_DEBUG':'0','XIANSHUSHU_DATA_DIR':str(folder)}
results=[]
for file in sorted((root/'tests').glob('test_*.py')):
    p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',file.name],cwd=root,env=env,capture_output=True,text=True,timeout=160)
    output=p.stdout+p.stderr;match=re.search(r'Ran (\d+) tests?',output)
    item={'module':file.name,'exit':p.returncode,'tests':int(match[1]) if match else 0}
    if p.returncode:item['error']=output[-3500:]
    results.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
    Path('/root/server-regression-report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
assert all(x['exit']==0 for x in results)
