# Windows 安装与启动

适用于第一次下载本项目、希望在自己电脑上运行的人。先完成演示体验，再接入真实店铺。本项目是源码程序，需要安装运行环境，不是双击即可安装的成品软件。

2026-11-15 已在 Windows / Python 3.10.11 的全新虚拟环境完成依赖安装、pip check 和演示模式八个页面检查。真实平台账号和采购流程需在自己的环境继续验证。

## 1. 准备电脑

- Windows 64 位电脑；安装 64 位 Python 3.10 或更高版本。验证环境以仓库实际测试结果为准。
- Microsoft Edge，用于孔夫子登录、查询和本地填单。
- 能访问 GitHub、Python 包源、闲管家和孔夫子的网络。
- 接入真实业务时，需要自己的闲管家 AppKey/AppSecret，且已授权自己的闲鱼店铺；还需要自己的孔夫子账号。这些凭据不是闲鱼登录密码，也不由本项目提供。

Python 官方下载入口：https://www.python.org/downloads/windows/ 。安装时选择添加 Python 到 PATH。安装完成后新开 PowerShell，输入：

```powershell
python --version
```

如果打不开或提示找不到命令，先修复 Python 安装；如果 `py -3 --version` 可用，下面创建虚拟环境的第一条命令可改用 `py -3 -m venv .venv`。

## 2. 下载源码并打开终端

打开 https://github.com/wutuobang888/xianshushu ，点击绿色 Code → Download ZIP，然后解压。建议放在自己有写入权限的短路径，例如 `C:/Projects/xianshushu`，不要放进 Program Files。

进入解压后的文件夹，确认能看到 `app.py`、`config.py`、`requirements.txt`。在资源管理器地址栏输入 `powershell` 并回车。下面所有命令都在这个文件夹运行；不要在 Python 的 `>>>` 提示符中输入。

也可使用已安装的 Git：

```powershell
git clone https://github.com/wutuobang888/xianshushu.git
cd "xianshushu"
```

## 3. 安装项目依赖（首次执行）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

看到 `No broken requirements found` 表示依赖关系检查通过。下载较慢时请等待完成；失败后保留错误信息再排查，不要直接跳过。

这些命令使用项目自己的虚拟环境，不用执行 Activate.ps1，也不用修改 PowerShell 执行策略。不要用管理员身份运行网站。

## 4. 先启动演示版

在同一个 PowerShell 中完整执行：

```powershell
$env:XIANSHUSHU_CLOUD_MODE = "0"
$env:XIANSHUSHU_SCHEDULER = "0"
$env:DEMO_MODE = "1"
$env:FLASK_DEBUG = "0"
$env:XIANSHUSHU_DATA_DIR = "$PWD/data/demo"
New-Item -ItemType Directory -Force $env:XIANSHUSHU_DATA_DIR | Out-Null
.\.venv\Scripts\python.exe app.py
```

看到 `Running on http://127.0.0.1:5000` 后，打开 http://127.0.0.1:5000/orders 。不需要网站账号密码。本地仅监听本机回环地址，不要把这个开发入口直接暴露到公网。

应能看到演示订单及八个业务页面。此时保持默认的空闲管家凭据，只浏览页面；浏览器搜索、抓图等功能仍可能访问外部网站。`DEMO_MODE=1` 不是对所有真实接口写入的全局开关，配置真实凭据后部分功能会使用真实接口。

终端需要一直开着；按 Ctrl+C 停止网站。关掉网页不会停止程序。新开 PowerShell 后，`$env:...` 不再保留，需要重新执行对应启动代码块。

## 5. 切换到真实业务（与演示数据分开）

先在演示终端按 Ctrl+C。仍在源码根目录执行下面命令：

```powershell
$env:XIANSHUSHU_CLOUD_MODE = "0"
$env:XIANSHUSHU_SCHEDULER = "0"
$env:DEMO_MODE = "0"
$env:FLASK_DEBUG = "0"
$env:XIANSHUSHU_DATA_DIR = "$PWD/data/live"
$env:KONGFZ_BROWSER_CHANNEL = "msedge"
New-Item -ItemType Directory -Force $env:XIANSHUSHU_DATA_DIR | Out-Null
.\.venv\Scripts\python.exe app.py
```

`data/live` 会自动创建自己的数据库；不需要手工建表，也不需要复制演示数据库。首次正式启动没有订单是正常的。

打开“系统设置”：

1. 填写自己的闲管家 AppKey 与 AppSecret，点击“保存凭证”。
2. 进入“订单”点击“同步订单”，核对真实订单、店铺和收货信息。同步过程会尝试刷新店铺映射；店铺信息缺失时，在设置里按平台返回的 seller_id 添加对应备注，不能随意编造编号。
3. 保持调度关闭，先完成下文孔夫子登录和使用说明中的人工核对，再决定是否开启自动任务。

设置页保存的闲管家凭据存放在该数据目录的 SQLite 数据库里，并优先于同名环境变量；当前不是数据库级加密存储。不要发送数据库给其他人、上传 GitHub 或放入公共共享文件夹。

## 6. 登录孔夫子

**推荐独立扩展采购：**先按[浏览器扩展安装](browser-extension.md)安装本项目扩展，无需油猴；在同一个常用 Chrome / Edge 登录孔夫子，从订单货源入口采购。仅使用扩展填址时，不需要下面的独立浏览器登录；需要后台采购监控或原“填单”功能时，仍须完成下面的登录。

在保持网站运行的情况下，另开一个位于同一源码根目录的 PowerShell。登录命令也必须指向同一数据目录：

```powershell
$env:XIANSHUSHU_CLOUD_MODE = "0"
$env:XIANSHUSHU_DATA_DIR = "$PWD/data/live"
$env:KONGFZ_BROWSER_CHANNEL = "msedge"
.\.venv\Scripts\python.exe -m bots.kongfz_bot login
```

程序会打开独立的 Edge 窗口。用自己的孔夫子账号登录；出现验证码时亲自完成。等待终端显示“检测到孔夫子登录态，已保存”。登录成功后由程序关闭窗口并保存登录态，后续查询复用它。

这是项目专用浏览器目录，与你平时 Edge/Chrome 的登录状态分开。在普通浏览器已登录孔夫子，不代表项目浏览器已登录。

若没有 Edge，可以在两个终端都改用：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:KONGFZ_BROWSER_CHANNEL = "chromium"
```

安装浏览器会下载较大的文件。切换 channel 后需重新运行网站及登录命令。不要同时开多个登录、填单或监控窗口；提示浏览器占用时先正常结束前一个操作，不要删除 profile 锁。

## 7. 日常启动与自动任务

日常先按第 5 节启动代码运行，默认 `XIANSHUSHU_SCHEDULER=0`，由页面手动同步。若确认订单匹配、采购绑定和发货规则符合自己的业务，可停止程序，将该行改为：

```powershell
$env:XIANSHUSHU_SCHEDULER = "1"
```

然后在同一终端运行 `.\.venv\Scripts\python.exe app.py`。**启用调度意味着采购监控可能自动绑定采购单，并在核验通过后向闲鱼回填发货，不只是自动刷新页面。** 配好凭据后须重启才能注册订单同步任务。

默认订单和退款同步每 10 分钟、商品同步每 60 分钟、孔夫子检查每 25 分钟，热书榜每天 08:05 执行。电脑需保持开机、联网，终端不能关闭。Windows 本地不要另外启动 worker.py，也不要重复开多个网站进程。

本项目不自动读取 `.env` 文件。仓库 `.env.example` 只是变量说明，复制成 `.env` 不会自动生效。

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| No module named flask / cv2 / cryptography | 用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`，并使用同一个 Python 启动 |
| pip 超时、TLS 或连接错误 | 检查网络和代理，重试安装；不要关闭证书校验 |
| Executable doesn't exist / 找不到 msedge | 安装 Edge，或按第 6 节安装 Chromium 并设置 channel |
| 网页打不开 | 确认终端仍运行、没有异常退出，地址使用 http://127.0.0.1:5000/orders |
| 5000 端口已占用 | 回到之前启动该程序的终端 Ctrl+C；不确定来源时不要随意结束别的进程 |
| 正式环境仍有假订单 | 检查是否使用 data/live；演示与正式目录不要混用，不必删除旧数据库 |
| 同步提示凭据/权限错误 | 核对闲管家 AppKey、AppSecret、店铺授权和接口权限，不能用闲鱼密码替代 |
| 孔夫子登录失效 | 按第 6 节重新登录同一数据目录，人工处理验证码 |
| 页面提示“本实例已暂停” | 当前数据目录有 automation.paused；先确认这是你新装的实例。不要移除其他运行实例的暂停标记 |
| 找货源后普通网页没有自动填地址 | 按独立扩展教程安装，确认订单页显示“闲书书扩展已启用”，从该订单重新打开；也可用“填单”独立浏览器 |

遇到不能确认的问题先停止重复操作，保留终端错误；求助时遮住密钥、手机号、客户地址和登录态。

下一步阅读：[日常使用说明](user-guide.md)。
