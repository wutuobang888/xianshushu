"""服务器人工选书、自动填地址；不提交订单，不付款。"""
import json
import sys
import time
from urllib.parse import urlsplit

import models
from services import checkout_task as tasks

# 抢在页面脚本前保护结算按钮，地址核对完成后才允许用户手动提交。
GUARD_JS = """(() => {
  window.__xssAddressReady = false;
  document.addEventListener('click', event => {
    const button = event.target.closest('button,a,input[type=submit],.k-button');
    if (button && /提交订单/.test(button.innerText || button.value || '')
        && !(window.__xssAddressReady && window.__xssAddressCheck && window.__xssAddressCheck())) {
      event.preventDefault(); event.stopImmediatePropagation();
      alert('闲书书尚未核对好本单地址，请等待填单完成或返回网站查看提示。');
    }
  }, true);
})();"""

BANNER_JS = r"""data => {
  let node = document.getElementById('xss-checkout-banner');
  if (!node) {
    node = document.createElement('div'); node.id = 'xss-checkout-banner';
    node.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:2147483647;'
      +'background:#fff3cd;color:#332701;padding:12px;font:16px sans-serif;white-space:pre-wrap';
    document.body.appendChild(node);
  }
  node.textContent = '闲书书 · 闲鱼订单 '+data.order_no+'\n'+data.title+'\n'+data.message;
}"""


def _eligible(task):
    order = models.query('SELECT status,receiver_json FROM orders WHERE id=?', (task['order_id'],), one=True)
    if not order or order['status'] not in ('pending', 'purchasing'):
        return False
    from services.order_service import normalize_receiver
    return normalize_receiver(json.loads(order['receiver_json'] or '{}')) == task['receiver']


def run(task_id):
    from playwright.sync_api import sync_playwright
    from bots.kongfz_bot import _launch
    from bots.checkout_address import fill_address, enable_manual_submit
    # 父进程落盘任务后才读取，跨进程启动不使用全局“当前收货人”。
    task = tasks.current(private=True)
    if not task or task['id'] != task_id or task['state'] == 'closing':
        return
    context = None
    try:
        with sync_playwright() as playwright:
            context = _launch(playwright, headless=False)
            context.add_init_script(GUARD_JS)
            page = context.pages[0] if context.pages else context.new_page()
            # 给浏览器标题栏留空间，避免提示条落在服务器屏幕下方。
            page.set_viewport_size({'width':1280, 'height':740})
            page.goto(task['source_url'], wait_until='domcontentloaded', timeout=45000)
            page.bring_to_front()
            tasks.update(task_id, state='browsing', message='请选书并点击立即购买，进入结算后自动填写本单地址')
            if task['buy_now'] and urlsplit(page.url).hostname == 'book.kongfz.com':
                try:
                    page.locator('a.go-buy').click(timeout=8000)
                except Exception:
                    tasks.update(task_id, message='请在商品页手动点击立即购买')
            started = time.monotonic()
            attempted = set()
            while time.monotonic() - started < 1800:
                current = tasks.current(private=True)
                if not current or current['id'] != task_id or current['state'] not in tasks.ACTIVE or current['state'] == 'closing':
                    break
                pages = [p for p in context.pages if not p.is_closed()]
                if not pages:
                    break
                checkout_pages = []
                for candidate in pages:
                    try:
                        if (urlsplit(candidate.url).hostname == 'order.kongfz.com'
                                and candidate.locator('.address-list').is_visible()):
                            checkout_pages.append(candidate)
                    except Exception:
                        pass
                if len(checkout_pages) > 1 or not _eligible(task):
                    for candidate in checkout_pages:
                        candidate.evaluate('() => { window.__xssAddressReady = false; }')
                    tasks.update(task_id, state='attention', message='存在多个结算页，或订单状态、地址已变化。请结束采购后重新打开')
                    # 不在多个结算页之间切换自动填单，避免误交另一件商品。
                    pages[-1].wait_for_timeout(700)
                    continue
                for page in pages:
                    if page.viewport_size != {'width':1280, 'height':740}:
                        page.set_viewport_size({'width':1280, 'height':740})
                    host = urlsplit(page.url).hostname or ''
                    if host != 'kongfz.com' and not host.endswith('.kongfz.com'):
                        continue
                    try:
                        page.evaluate(BANNER_JS, dict(task, message=current['message']))
                        if host != 'order.kongfz.com' or not page.locator('.address-list').is_visible():
                            continue
                        # 同一页刷新后重新核对；失败时停手，不循环修改账户地址。
                        stamp = page.evaluate("() => performance.timeOrigin")
                        document = (id(page), stamp)
                        if document in attempted:
                            continue
                        attempted.add(document)
                        if not _eligible(task):
                            tasks.update(task_id, state='attention', message='销售订单状态或地址已变化，请结束采购后重新打开')
                            continue
                        latest_order = models.query('SELECT * FROM orders WHERE id=?', (task['order_id'],), one=True)
                        if tasks.fresh_receiver(latest_order) != task['receiver']:
                            tasks.update(task_id, state='attention', message='闲鱼收货地址已变化，请结束采购后重新打开')
                            continue
                        tasks.update(task_id, state='filling', message='正在填写并核对本单收货地址，请稍候')
                        result = fill_address(page, task['receiver'])
                        if result['ok']:
                            enable_manual_submit(page, task['receiver'])
                            tasks.update(task_id, state='ready', message='本单地址已填写并核对，请检查书籍、金额后手动提交付款')
                        else:
                            tasks.update(task_id, state='attention', message=result['msg'])
                    except Exception:
                        # 导航、验证码或DOM变化不会触发反复填单，也不记录页面隐私。
                        tasks.update(task_id, state='attention', message='页面发生变化，自动填写未确认完成；请结束采购后重试，或联系检查')
                pages[-1].wait_for_timeout(700)
            tasks.update(task_id, state='closed', message='采购窗口已结束；已提交的订单由后台继续检查')
            context.close()
            context = None
    except Exception:
        tasks.update(task_id, state='failed', message='采购窗口未能打开，可能正在后台查单或登录，请稍后重试')
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass


if __name__ == '__main__':
    run(sys.argv[1])
