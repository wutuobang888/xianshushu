#!/bin/bash
set -euo pipefail
python3 /root/tests/server_vm/guard.py
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv nginx xvfb xauth x11vnc openbox novnc websockify curl openssl procps nftables
install -d -o root -g www-data -m 0750 /srv/xianshushu
install -d -m 0755 /srv/xianshushu/live-app
tar -xf /root/source.tar -C /srv/xianshushu/live-app
install -d -o www-data -g www-data -m 0700 /srv/xianshushu/live-data
install -d -o www-data -g www-data -m 0750 /srv/xianshushu/browsers
install -d -o root -g www-data -m 0750 /etc/xianshushu
python3 -m venv /srv/xianshushu/live-venv
/srv/xianshushu/live-venv/bin/python -m pip install -r /srv/xianshushu/live-app/requirements-server.txt
/srv/xianshushu/live-venv/bin/python -m pip check
/srv/xianshushu/live-venv/bin/python -m playwright install-deps chromium
runuser -u www-data -- env HOME=/srv/xianshushu/live-data PLAYWRIGHT_BROWSERS_PATH=/srv/xianshushu/browsers /srv/xianshushu/live-venv/bin/python -m playwright install chromium
/srv/xianshushu/live-venv/bin/python /srv/xianshushu/live-app/tests/server_vm/configure.py
systemctl daemon-reload
systemctl start xianshushu-display xianshushu-window-manager xianshushu-vnc xianshushu-browser xianshushu-web nginx
nginx -t
systemctl reload nginx
# 安装完成后隔离外网：平台桩、HTTPS、VNC 只使用来宾自己的回环网络。
cat > /etc/nftables.conf <<'NFT'
table inet xss_acceptance {
 chain output {
  type filter hook output priority 0; policy drop;
  oifname "lo" accept
  udp sport 68 udp dport 67 accept
  ip daddr 10.0.2.2 tcp sport 22 accept
  ct state established,related accept
 }
}
NFT
nft -f /etc/nftables.conf
systemctl enable nftables
