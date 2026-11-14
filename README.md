# 闲书书

Flask + SQLite + Playwright 的闲鱼图书采购管理系统。一个仓库包含 Windows 本地与 Linux 服务器运行代码。

功能包括多店铺订单及退款同步、采购绑定、物流核验回填、选品上架、图片素材、热书榜、文案模板、客户和利润报表。采购提交及付款由人工完成。

## 先看这里

**可以在自己的 Windows 电脑上运行。首次安装依赖后，可先用演示数据体验；真实业务需要你自己的闲管家接口授权和孔夫子账号。源码不附带任何账号、客户资料或平台授权。**

| 你要做什么 | 阅读说明 |
| --- | --- |
| 在自己电脑安装、启动、配置账号 | [Windows 安装与启动](docs/windows-install.md) |
| 同步订单、采购、发货、选品与日常维护 | [使用说明](docs/user-guide.md) |
| 在常用浏览器自动填孔夫子地址 | [本地油猴地址助手安装](docs/local-userscript.md) |
| 部署到自己的 Linux 服务器 | [服务器部署说明](docs/server-deploy.md) |
| 修改代码、了解模块职责 | [架构说明](ARCHITECTURE.md) |

建议第一次使用者选择 Windows 本地版。可安装本地油猴，在常用 Chrome / Edge 从订单“找货源 / 去货源”进入孔夫子后自动核对填址；也可使用“填单”打开项目专用浏览器。两种方式见对应教程。

## Windows 本地快速体验

先安装 64 位 Python（源码使用 Python 3.10+ 语法）和 Microsoft Edge。下载仓库 ZIP 并解压，在包含 app.py 的文件夹打开 PowerShell。以下命令使用虚拟环境内的 Python，无需激活脚本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:XIANSHUSHU_CLOUD_MODE = "0"
$env:XIANSHUSHU_SCHEDULER = "0"
$env:DEMO_MODE = "1"
$env:FLASK_DEBUG = "0"
$env:XIANSHUSHU_DATA_DIR = "$PWD/data/demo"
New-Item -ItemType Directory -Force $env:XIANSHUSHU_DATA_DIR | Out-Null
.\.venv\Scripts\python.exe app.py
```

打开 http://127.0.0.1:5000/orders 。保留终端窗口；结束时按 Ctrl+C。演示目录里不要填入真实凭据：填入后部分操作会调用真实平台。正式使用请按完整安装文档切换到独立的 data/live 目录。

如果 Edge 未安装或准备改用 Chromium，浏览器安装与选择方法见完整安装说明。代码、文档与外部平台页面仍可能随环境不同出现兼容问题；不要把演示页面正常等同于真实采购与发货已经验证。

## Linux 服务器

安装 requirements-server.txt 和 Playwright Chromium；参考 deploy/ 下的环境变量与 systemd 配置模板。模板中的 /srv/xianshushu 路径需要按实际部署修改。

正式 Web 使用 production_wsgi:app，必须设置 XIANSHUSHU_CLOUD_MODE=1、DEMO_MODE=0、XIANSHUSHU_SCHEDULER=0、FLASK_DEBUG=0，并配置独立随机 FLASK_SECRET_KEY、站内登录凭据文件与 HTTPS 反向代理。后台调度单独运行 worker.py，避免启动多个业务任务实例。浏览器交互另需显示服务及受认证保护的网关。

部署模板来自既有环境，尚未在全新服务器完成从零安装验证。不要直接执行历史迁移脚本；仓库不提供这些脚本。

## 地址与采购限制

服务器同步默认地址成功后再进入普通孔夫子网页；提交前仍需人工核对收货人。共享账号、浏览器配送记忆与旧结算页快照可能影响选中结果。涉及地址簿轮换应由部署者明确启用，并保留备份及回读检查。

## 开源与验证

本仓库包含 Windows 本地和 Linux 服务器两种运行方式的源码。真实数据库、客户资料、登录态、密钥、日志和原 Git 历史均未收录。第三方前端资源保留其原文件中的许可证标注。项目代码采用 MIT 许可证，第三方资源见 THIRD_PARTY_NOTICES.md。

发布前检查：Python 源码语法检查通过；37 项地址同步回归和 7 项官方地区解析回归通过。尚未对全新电脑和服务器完成所有真实平台业务的安装验证。

2026-11-15 安装说明验证：在 Windows / Python 3.10.11 的全新虚拟环境安装 requirements.txt 成功，pip check 通过；使用独立演示数据目录、关闭调度后，八个业务页面均返回 HTTP 200。此验证不包含真实账号登录、采购提交或发货，也不代表完成了全新服务器部署验证。

本地油猴 2.1.0：支持默认 localhost / 127.0.0.1 订单入口，同源 CSRF 取址、每单独立上下文、结算选址及新增后回读。相关本地接口、浏览器 DOM、原地址同步及云端认证共 61 项隔离回归通过；尚未在使用者的真实 Tampermonkey 配置和账号完成端到端验证。安装后先检查绿色启用提示，再逐笔核对收货信息。
