# 闲书书

Flask + SQLite + Playwright 的闲鱼图书采购管理系统。一个仓库包含 Windows 本地与 Linux 服务器运行代码。

功能包括多店铺订单及退款同步、采购绑定、物流核验回填、选品上架、图片素材、热书榜、文案模板、客户和利润报表。采购提交及付款由人工完成。

## Windows 本地

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
pip install -r requirements.txt
python -m playwright install msedge
$env:XIANSHUSHU_SCHEDULER = "0"
python app.py
```

打开 http://127.0.0.1:5000/orders 。首次先以演示模式检查页面。默认不含任何真实数据或平台凭据；需要自行配置闲管家和孔夫子账号。

此候选版保留原本地窗口填单与服务器免插件地址同步代码。历史油猴脚本包含部署域名配置，已替换为 books.example.com 占位域名；本地油猴需适配同源地址及对应页面入口，尚不能视为开箱即用。

## Linux 服务器

安装 requirements-server.txt 和 Playwright Chromium；参考 deploy/ 下的环境变量与 systemd 配置模板。模板中的 /srv/xianshushu 路径需要按实际部署修改。

正式 Web 使用 production_wsgi:app，必须设置 XIANSHUSHU_CLOUD_MODE=1、DEMO_MODE=0、XIANSHUSHU_SCHEDULER=0、FLASK_DEBUG=0，并配置独立随机 FLASK_SECRET_KEY、站内登录凭据文件与 HTTPS 反向代理。后台调度单独运行 worker.py，避免启动多个业务任务实例。浏览器交互另需显示服务及受认证保护的网关。

部署模板来自既有环境，尚未在全新服务器完成从零安装验证。不要直接执行历史迁移脚本；本候选版不提供这些脚本。

## 地址与采购限制

服务器同步默认地址成功后再进入普通孔夫子网页；提交前仍需人工核对收货人。共享账号、浏览器配送记忆与旧结算页快照可能影响选中结果。涉及地址簿轮换应由部署者明确启用，并保留备份及回读检查。

## 开源与验证

本仓库包含 Windows 本地和 Linux 服务器两种运行方式的源码。真实数据库、客户资料、登录态、密钥、日志和原 Git 历史均未收录。第三方前端资源保留其原文件中的许可证标注。项目代码采用 MIT 许可证，第三方资源见 THIRD_PARTY_NOTICES.md。

发布前检查：Python 源码语法检查通过；37 项地址同步回归和 7 项官方地区解析回归通过。尚未对全新电脑和服务器完成所有真实平台业务的安装验证。
