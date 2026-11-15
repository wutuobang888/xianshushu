"""GitHub 标准 Linux runner 上的独立 Debian VM 验收，不接受远程服务器参数。"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('请通过仓库手动 Actions 工作流运行，不在用户电脑或生产服务器直接执行。')

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT/'work/server-vm'
RESULTS = WORK/'results'
RESULTS.mkdir(parents=True, exist_ok=True)
nonce = secrets.token_hex(24)
key = WORK/'guest-key'
subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
public = key.with_suffix('.pub').read_text().strip()
user_data = WORK/'user-data'
user_data.write_text(f'''#cloud-config
hostname: xss-acceptance-guest
manage_etc_hosts: true
disable_root: false
ssh_pwauth: false
users:
  - name: root
    lock_passwd: true
    ssh_authorized_keys:
      - {public}
write_files:
  - path: /etc/xss-acceptance-guest
    permissions: '0600'
    content: |
      {nonce}
''')
(WORK/'meta-data').write_text('instance-id: xss-acceptance\nlocal-hostname: xss-acceptance-guest\n')
subprocess.run(['cloud-localds', str(WORK/'seed.img'), str(user_data), str(WORK/'meta-data')], check=True)
image = WORK/'debian.qcow2'
base = 'https://cloud.debian.org/images/cloud/trixie/latest/'
filename = 'debian-13-genericcloud-amd64.qcow2'
subprocess.run(['curl', '-fL', '--retry', '3', '-o', str(image), base+filename], check=True)
subprocess.run(['curl', '-fL', '--retry', '3', '-o', str(WORK/'SHA512SUMS'), base+'SHA512SUMS'], check=True)
expected = next(line.split()[0] for line in (WORK/'SHA512SUMS').read_text().splitlines() if line.split()[-1].lstrip('*./') == filename)
assert hashlib.sha512(image.read_bytes()).hexdigest() == expected, 'Debian checksum mismatch'
subprocess.run(['qemu-img', 'resize', str(image), '12G'], check=True)
ssh_options = ['-i', str(key), '-p', '32222', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
               '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={WORK}/known_hosts']
address = 'root@127.0.0.1'
def ssh(command, *, capture=False, check=True, timeout=900):
    return subprocess.run(['ssh', *ssh_options, address, command], check=check, capture_output=capture,
                          text=True, timeout=timeout)

def require_guest():
    # 宿主机不执行任何系统电源命令；SSH 目标固定为本机 QEMU 转发端口，无回退目标。
    value = ssh('cat /etc/xss-acceptance-guest; hostname; systemd-detect-virt --vm', capture=True).stdout.splitlines()
    assert len(value) == 3 and value[0] == nonce and value[1] == 'xss-acceptance-guest' and value[2] in ('qemu', 'kvm'), 'VM identity mismatch'

def wait_ssh():
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError('QEMU exited; inspect qemu-console.log')
        result = ssh('true', capture=True, check=False, timeout=8)
        if result.returncode == 0:
            require_guest()
            return
        time.sleep(2)
    raise RuntimeError('Guest SSH did not become ready')

qemu = ['qemu-system-x86_64', '-m', '3072', '-smp', '2', '-display', 'none', '-monitor', 'none',
        '-serial', 'file:'+str(RESULTS/'qemu-console.log'),
        '-drive', f'file={image},format=qcow2,if=virtio',
        '-drive', f'file={WORK}/seed.img,format=raw,if=virtio',
        '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:32222-:22', '-device', 'virtio-net-pci,netdev=net0']
if Path('/dev/kvm').exists():
    qemu = ['sudo', *qemu, '-accel', 'kvm', '-cpu', 'host']
else:
    qemu += ['-accel', 'tcg,thread=multi']
process = subprocess.Popen(qemu)
try:
    wait_ssh()
    ssh('cloud-init status --wait', check=True)
    archive = WORK/'source.tar'
    subprocess.run(['git', 'archive', '--format=tar', '--output='+str(archive), 'HEAD'], cwd=ROOT, check=True)
    subprocess.run(['scp', '-i', str(key), '-P', '32222', '-o', f'UserKnownHostsFile={WORK}/known_hosts',
                    str(archive), address+':/root/source.tar'], check=True)
    # 从已验明身份的来宾中解包安装脚本，再按部署说明安装当前提交源码。
    ssh('tar -xf /root/source.tar -C /root tests/server_vm; bash /root/tests/server_vm/install.sh', timeout=1500)
    script = '/srv/xianshushu/live-app/tests/server_vm/'
    python = '/srv/xianshushu/live-venv/bin/python '
    ssh(python+script+'acceptance.py', timeout=240)
    ssh(python+script+'regressions.py', timeout=600)
    require_guest()
    boot = ssh('cat /proc/sys/kernel/random/boot_id', capture=True).stdout.strip()
    ssh('systemctl enable xianshushu-worker; systemctl reboot', check=False)
    # 先确认旧 SSH 已离线，再等待同一台有标记的来宾恢复。
    for _ in range(50):
        probe = ssh('cat /proc/sys/kernel/random/boot_id', capture=True, check=False, timeout=8)
        if probe.returncode != 0 or probe.stdout.strip() != boot:
            break
        time.sleep(.5)
    wait_ssh()
    after = ssh('cat /proc/sys/kernel/random/boot_id', capture=True).stdout.strip()
    assert after != boot, 'Guest did not reboot'
    ssh(python+script+'reboot_check.py', timeout=90)
    (RESULTS/'vm-identity.json').write_text(json.dumps({'commit':os.environ['GITHUB_SHA'],
        'fresh_debian_image_checksum_verified':True,'reboot_changed_boot_id':True,
        'production_server_accessed':False,'passed':True}, indent=2))
finally:
    # 仅收集固定的合成数据报告，不上传磁盘、账号文件、SSH 密钥或浏览器配置。
    for name in ['server-acceptance-report.json','server-regression-report.json','server-novnc-verification.png']:
        try:
            with (RESULTS/name).open('wb') as output:
                subprocess.run(['ssh', *ssh_options, address, 'cat /root/'+name], stdout=output, check=False, timeout=15)
        except subprocess.TimeoutExpired:
            print('Report unavailable: '+name, flush=True)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
