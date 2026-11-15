# 服务器版部署验收

2026-11-15 在 GitHub Actions 标准 Ubuntu 24.04 运行器中，校验 Debian 官方镜像的 SHA512，启动具有独立内核的 QEMU Debian 13 虚拟机。当前 GitHub 提交的受版本控制源码通过 git archive 传入来宾，再按 [服务器部署说明](server-deploy.md) 安装依赖、创建账号、配置 nginx 与 systemd。没有连接生产服务器、挂载现有业务目录、复制真实数据或接入真实平台账号。

完整通过的运行：[Server installation acceptance #43706896798](https://github.com/wutuobang888/xianshushu/actions/runs/43706896798)。测试提交为 `02b372219897d646ded61a2622e38b4f3f739990`；后续发行说明更新不改变该次已测运行代码。

环境：Debian 13 amd64、Python 3.13.5、Playwright 1.64.0 及其 Chromium、Gunicorn 23、nginx、Xvfb、Openbox、x11vnc、noVNC/websockify。

## 实际通过的检查

| 检查 | 结果 |
| --- | --- |
| 全新系统安装 requirements-server.txt、浏览器系统依赖和 Chromium | 成功，pip check 无依赖冲突 |
| 交互初始化网站账号、密码哈希和孔夫子加密密钥 | 成功，重复初始化不覆盖旧文件 |
| systemd 显示、窗口管理、VNC、网关和正式 Web | 按模板启动，后端端口仅回环监听 |
| nginx HTTP 跳转 HTTPS、网站登录及八个业务页面 | 成功 |
| 匿名业务页/浏览器页/WebSocket、缺少 CSRF、错误 Origin | 按预期拦截 |
| 实际 VNC WebSocket 与 noVNC 客户端 | 能连接真实测试桌面及有头 Chromium |
| 设置页保存闲管家测试凭据、手动同步 | 正常接口客户端导入两笔合成订单 |
| HTTPS 网站逐单地址接口 | 两笔订单分别返回对应完整收货信息 |
| 设置页保存孔夫子测试凭据 | 密文可解密，文件权限 0600，不含明文密码 |
| 独立 worker | 调度实际执行同步，第二个 worker 被锁拦截 |
| Web 重启 | 数据和网站会话保留 |
| 整台虚拟机重新启动 | 启动编号变化；六个服务自动启动，网站登录和两笔合成订单保留 |
| Linux Python 回归 | 233 项测试逐模块执行通过，包含真实加载 MV3 扩展的隔离浏览器测试 |

## 本轮修复

- 原交互账号命令使用 heredoc 传入 Python 程序，导致 input() 无法继续读取终端。改为独立脚本 scripts/init_server_credentials.py。
- 新环境的 PrivateTmp 没有 X11 socket 目录。显示服务在同一沙箱内先创建 root 所有、1777 权限的目录，再以 www-data 运行 Xvfb，保留 Xauthority 验证。
- systemd 的 EnvironmentFile 会覆盖 Environment。Web/worker 改为在 ExecStart 明确固定各自的调度开关，避免 worker 读取共用环境文件的 0 后退出。
- 测试中的 Windows 专用浏览器和上传分支改为明确隔离，Linux 使用已安装的 Chromium，测试不依赖 Linux 另装 Edge。

## 验证边界

HTTPS 使用验收专用证书及受信任的测试证书链，未办理新公网域名或证书签发。生产部署仍须自行提供 DNS、有效证书、续期和适当的服务器访问控制。

闲管家接口、收货信息、浏览器测试中的平台页面均使用合成数据；没有登录真实平台账号、提交采购、付款或向真实闲鱼订单回填发货。真实账号的接口授权、验证码、最新平台页面及物流匹配仍须接收者按部署说明核对后再启用任务。MV3 浏览器回归不代表对每种浏览器的服务器权限授权弹窗都做过人工操作验收。

本次证明这套部署步骤在上述全新 Linux 环境可执行，不保证所有发行版、平台账号和网络条件完全一致。

## 复跑方式与隐私

仓库 Actions → Server installation acceptance → Run workflow。流程只使用 GitHub 标准托管 Linux 运行器，不使用 self-hosted 运行器，不接受服务器地址、密码或平台账号输入，也不引用仓库 Secrets。来宾 SSH 固定连接运行器自身的 127.0.0.1:32222；涉及重启前核对随机标记、主机名、虚拟化类型，重启后还须确认启动编号变化，没有远程服务器回退入口。

安装结束后，虚拟机外网出站被限制，仅保留回环、DHCP 和验收 SSH 通信。日志和报告公开可见；产物白名单只包含 JSON 验收结果、合成桌面截图和虚拟机控制台日志，保留 3 天。不会上传磁盘、SSH 私钥、账号文件、数据库或浏览器目录。运行完毕，测试虚拟机与 GitHub 临时运行器回收。
