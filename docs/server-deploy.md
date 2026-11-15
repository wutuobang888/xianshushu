# Linux 服务器部署说明

这是面向能管理 Linux、systemd 和 HTTPS 反向代理的部署者的说明。没有服务器经验时，建议先按 [Windows 安装说明](windows-install.md) 在本机使用。它不是一键安装器，以下步骤适用于独立的新实例。

以下以 Debian 系统、独立新实例为例。需要自己的域名、已指向服务器的 DNS、有效 HTTPS 证书、闲管家授权和孔夫子账号。命令里的 `books.example.com` 必须替换为自己的域名。安装前确认目录、端口和服务名未被已有业务使用；不要覆盖现有实例。

2026-11-15 已在全新 Debian 13 / Python 3.13.5 隔离系统中按下述流程完成安装、HTTPS 登录、桌面网关和定时任务验收。具体检查及合成平台测试的边界见 [服务器验收记录](server-verification.md)。

## 1. 安装运行环境

以下服务器命令由有管理权限的部署者执行，不是在本地 Windows PowerShell 执行：

```bash
apt-get update
apt-get install -y git python3 python3-venv nginx xvfb xauth x11vnc openbox novnc websockify
install -d -o root -g www-data -m 0750 /srv/xianshushu
git clone https://github.com/wutuobang888/xianshushu.git /srv/xianshushu/live-app
install -d -o www-data -g www-data -m 0700 /srv/xianshushu/live-data
install -d -o www-data -g www-data -m 0750 /srv/xianshushu/browsers
install -d -o root -g www-data -m 0750 /etc/xianshushu
python3 -m venv /srv/xianshushu/live-venv
/srv/xianshushu/live-venv/bin/python -m pip install -r /srv/xianshushu/live-app/requirements-server.txt
/srv/xianshushu/live-venv/bin/python -m playwright install-deps chromium
```

以服务用户安装浏览器到固定位置：

```bash
runuser -u www-data -- env HOME=/srv/xianshushu/live-data PLAYWRIGHT_BROWSERS_PATH=/srv/xianshushu/browsers /srv/xianshushu/live-venv/bin/python -m playwright install chromium
```

## 2. 创建独立凭据

用编辑器新建 `/etc/xianshushu-production.env`，参考仓库 `deploy/production.env.example`，并补全以下值：

```dotenv
XIANSHUSHU_CLOUD_MODE=1
XIANSHUSHU_DATA_DIR=/srv/xianshushu/live-data
XIANSHUSHU_DATABASE=/srv/xianshushu/live-data/database.db
XIANSHUSHU_ACCESS_FILE=/etc/xianshushu/web-access.json
XIANSHUSHU_SCHEDULER=0
KONGFZ_USER_DATA_DIR=/srv/xianshushu/live-data/browser_data
KONGFZ_BROWSER_CHANNEL=chromium
KONGFZ_HEADLESS=1
KONGFZ_KEY_FILE=/etc/xianshushu/kongfz.key
PLAYWRIGHT_BROWSERS_PATH=/srv/xianshushu/browsers
DISPLAY=:99
XAUTHORITY=/srv/xianshushu/live-data/Xauthority
HOME=/srv/xianshushu/live-data
DEMO_MODE=0
FLASK_DEBUG=0
FLASK_SECRET_KEY=替换为独立的随机密钥
TZ=Asia/Shanghai
```

`FLASK_SECRET_KEY` 必须至少 32 字符；可用下面命令在自己终端生成，填入环境文件，不要发给别人：

```bash
/srv/xianshushu/live-venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

在自己的服务器终端交互设置网站登录账号、密码及孔夫子凭据加密密钥。以下仅用于尚无这些文件的新实例；重复执行会被独占创建模式拦截，不能用于覆盖旧密钥：

```bash
/srv/xianshushu/live-venv/bin/python /srv/xianshushu/live-app/scripts/init_server_credentials.py
chown root:www-data /etc/xianshushu-production.env /etc/xianshushu/web-access.json /etc/xianshushu/kongfz.key
chmod 0640 /etc/xianshushu-production.env /etc/xianshushu/web-access.json /etc/xianshushu/kongfz.key
```

网站账号与孔夫子账号是两套不同的凭据。孔夫子账号在部署后的设置页保存；不要把明文密码写进服务文件或 Git 仓库。

## 3. 初始化受保护的浏览器显示

新实例创建 Xauthority（只执行一次，不覆盖运行中显示会话）：

```bash
runuser -u www-data -- touch /srv/xianshushu/live-data/Xauthority
runuser -u www-data -- xauth -f /srv/xianshushu/live-data/Xauthority add :99 . "$(mcookie)"
```

将仓库 `deploy/xianshushu-*.service` 逐个核对后安装到 `/etc/systemd/system/`。模板按 `/srv/xianshushu/live-app`、`live-data`、`live-venv` 固定路径编写；如果使用其他路径，先统一修改模板。

```bash
cp /srv/xianshushu/live-app/deploy/xianshushu-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xianshushu-display.service xianshushu-window-manager.service xianshushu-vnc.service xianshushu-browser.service xianshushu-web.service
```

此时先不启动 worker。确认 5051、5900、6080 都只监听回环地址。模板中的 VNC 不提供独立密码，依赖回环监听及 nginx 网站认证保护，不能把这两个浏览器端口开放到公网。

```bash
systemctl is-active xianshushu-display xianshushu-window-manager xianshushu-vnc xianshushu-browser xianshushu-web
ss -ltnp
```

五项应全部为 `active`，并且稍候能看到三个回环监听端口。`activating` 可能正在反复重试，不表示成功；用 `journalctl -u 对应服务名 -n 60 --no-pager` 查错。显示模板会在自身 PrivateTmp 中准备 root 所有的 `/tmp/.X11-unix`，不要删去该准备命令，也不要通过关闭 Xauthority 验证解决连接错误。

## 4. 配置 HTTPS 和 nginx

先准备好自己域名的有效证书与私钥，不要用仓库示例域名。按自己的证书管理方式配置续期。新建独立的 nginx 站点，以如下结构配置；证书路径只是示例，必须换成实际文件：

```nginx
server {
    listen 80;
    server_name books.example.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name books.example.com;
    ssl_certificate /etc/nginx/ssl/books.fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/books.key;

    location / {
        proxy_pass http://127.0.0.1:5051;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
    include /etc/nginx/snippets/xianshushu-browser.locations;
}
```

把仓库 `deploy/browser-nginx.locations` 复制到上面的 include 路径，先把其中的 `https://books.example.com` 改为自己的 HTTPS Origin（协议、域名和端口必须对应）。保留 `auth_request` 和 Origin 校验，不要为排查问题取消这些保护。

```bash
nginx -t
systemctl reload nginx
```

如果测试失败，不重载，先修复证书、模块或配置错误。nginx 需支持 `auth_request`。只把你配置的 HTTP/HTTPS 入口开放给外网，应用和浏览器后端端口保持私有。

## 5. 首次登录与接入平台

1. 打开 `https://你的域名/login`，输入第 2 节设置的网站账号密码。
2. 在“系统设置”保存自己的闲管家凭据，然后到订单页手动同步，核对店铺及订单。
3. 在设置页加密保存孔夫子账号密码，点击“启动登录窗口”，再打开“孔夫子窗口”完成登录及验证码。
4. 浏览器地址使用 `/browser/vnc.html?autoconnect=1&resize=scale&path=/browser/websockify`；path 中的前导 `/` 不可省略。
5. 等待显示登录成功并正常结束窗口。采购仍在普通孔夫子网页完成，服务器窗口主要用于登录与验证。

在自己电脑的普通 Edge / Chrome 采购时，建议按 [独立扩展教程](browser-extension.md) 连接自己的 HTTPS 网站，在同一浏览器登录孔夫子，并确认订单页显示扩展已启用。后台项目浏览器的登录态与普通浏览器互相独立。

不要在有人登录或采购时重启 Web 服务。首次账户没有历史地址同步策略；不要把自己实例的账号或地址编号换成其他人的固定编号，也不要仅根据设置页文案认为满额地址自动清理已配置。

## 6. 启用后台任务

确认账号、真实订单、采购匹配与发货核验适合自己的业务后，再运行：

```bash
systemctl enable --now xianshushu-worker.service
systemctl is-active xianshushu-web.service xianshushu-worker.service
journalctl -u xianshushu-worker.service -n 80 --no-pager
```

worker 和 Web 的 `ExecStart` 分别通过 `/usr/bin/env` 固定调度开关为 1 和 0。systemd 的 EnvironmentFile 优先于 Environment，不能把这个写法简化成仅添加一行 Environment，否则共用环境文件中的 0 会使 worker 启动失败。只允许一个 worker；自动监控可能真实回填闲鱼发货。采购提交和付款仍由人工操作。

日志应出现 `Scheduler started`；已配置闲管家凭据时，还应注册 `order_sync`、`refund_sync` 和 `product_sync`。仅显示 `active` 不代表已经配置平台或定时同步已成功；到期后核对任务日志及订单数据。

## 7. 验证、备份与维护

- 匿名访问业务页应跳转登录；登录后八个页面可打开。
- 匿名访问浏览器页面或 WebSocket 不能获得桌面；合法页面能显示孔夫子窗口。
- POST 缺少 CSRF 应被拒绝，不为方便而禁用验证。
- 同步一笔自己的订单，核对收货信息；进入采购前确认普通浏览器的孔夫子账号与服务器一致。
- 备份前正常结束浏览器操作并停止 Web 和 worker，再私有备份 `live-data`、环境文件、网站登录哈希及孔夫子加密密钥。恢复时必须配套，不能重新生成密钥后期待旧密文还能解开。
- 更新前备份，检查自己的修改，更新依赖后先验证 Web，再恢复 worker。不要将示例数据库覆盖正式数据库。

本说明依据仓库代码及服务模板编写；新服务器的证书、DNS、发行版依赖和真实平台权限仍需部署者验证。遇到 502 先查看 Web 服务日志，遇到浏览器空白检查显示服务、Xauthority、网关路径和认证；不要直接开放 VNC 端口。
