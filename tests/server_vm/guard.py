"""验收脚本仅允许在有独立内核的、带本次标记的 QEMU 来宾运行。"""
import os
from pathlib import Path
import socket
import subprocess


def require_guest():
    if os.name != 'posix' or os.geteuid() != 0 or socket.gethostname() != 'xss-acceptance-guest':
        raise SystemExit('拒绝运行：不是专用验收虚拟机。')
    if not Path('/etc/xss-acceptance-guest').is_file():
        raise SystemExit('拒绝运行：缺少虚拟机验收标记。')
    virtualization = subprocess.check_output(['systemd-detect-virt', '--vm'], text=True).strip()
    if virtualization not in ('qemu', 'kvm'):
        raise SystemExit('拒绝运行：要求 QEMU/KVM 独立虚拟机。')


if __name__ == '__main__':
    require_guest()
