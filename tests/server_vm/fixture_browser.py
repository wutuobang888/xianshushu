from playwright.sync_api import sync_playwright
from pathlib import Path
import time
with sync_playwright() as runtime:
    browser=runtime.chromium.launch(headless=False,args=['--no-sandbox'])
    page=browser.new_page()
    page.set_content('<meta charset="utf-8"><h1>闲书书服务器验收</h1><p>这是隔离测试桌面，不包含真实账号或订单。</p>')
    page.wait_for_timeout(500)
    Path('/srv/xianshushu/live-data/fixture-browser-ready').write_text('ready')
    time.sleep(180)
    browser.close()
