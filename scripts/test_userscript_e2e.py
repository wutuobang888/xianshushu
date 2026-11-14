# -*- coding: utf-8 -*-
"""端到端验证 kfz_address.user.js：模拟 Tampermonkey 环境跑完整流程。
落地页(xsb_oid) -> 商品页 -> 立即购买 -> 下单页自动填地址。不点提交订单。"""
import json
import logging
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
import config
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO)

ORDER_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 373
ITEM_URL = sys.argv[2] if len(sys.argv) > 2 else \
    "https://item.kongfz.com/book/32404568.html?sortType=7&quality=80~"
PRODUCT_URL = "https://book.kongfz.com/117832/10118585405"

SCRIPT = open(str(__import__('pathlib').Path(__file__).resolve().parent.parent
                / "scripts" / "kfz_address.user.js"), encoding="utf-8").read()

# GM API 桩：GM_setValue 走 Python dict（异步无妨）；
# GM_getValue 读页内快照 __gm_values（真实油猴是同步 API，
# expose_function 返回 Promise，直接桥接会 JSON.parse(Promise) 崩）
GM_STORE = {}
GM_STUB = """
window.GM_setValue = (k, v) => window.__gm_set(k, JSON.stringify(v));
window.GM_getValue = (k, d) => {
    const v = (window.__gm_values || {})[k];
    return v === undefined ? d : JSON.parse(v);
};
window.GM_setClipboard = t => { window.__gm_clip = t; };
window.GM_xmlhttpRequest = opts => {
    fetch(opts.url).then(r => r.text())
        .then(t => opts.onload({ responseText: t }))
        .catch(e => opts.onerror && opts.onerror(e));
};
"""


def sync_gm(pg):
    """把 Python 侧 GM_STORE 快照写进页面，供 GM_getValue 同步读取。"""
    pg.evaluate(f"window.__gm_values = {json.dumps(GM_STORE)}")


def main():
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(config.KONGFZ_USER_DATA_DIR), headless=True, channel="msedge",
        args=["--disable-blink-features=AutomationControlled"])

    # GM 存取桥接到 Python
    ctx.expose_function("__gm_set", lambda k, v: GM_STORE.__setitem__(k, v))
    ctx.expose_function("__gm_get", lambda k: GM_STORE.get(k))
    ctx.add_init_script(GM_STUB)

    # 跨域 fetch 会被 CORS 挡：劫持 source_target 请求，由 Python 侧代取真实数据
    def handle_route(route):
        if "/orders/source_target" in route.request.url:
            data = urllib.request.urlopen(
                "http://127.0.0.1:5000/orders/source_target", timeout=5).read()
            route.fulfill(status=200, content_type="application/json", body=data)
        else:
            route.continue_()
    ctx.route("**/*", handle_route)

    page = ctx.new_page()

    def inject(pg, tag):
        sync_gm(pg)
        pg.evaluate(SCRIPT)
        print(f"[{tag}] 脚本已注入")

    # ============ 1) 落地页：带 xsb_oid ============
    url = ITEM_URL + f"&xsb_oid={ORDER_ID}"
    page.goto(url, timeout=25000)
    page.wait_for_timeout(3000)
    inject(page, "item页")
    page.wait_for_timeout(800)
    print("旗子:", GM_STORE)

    # ============ 2) 商品页 ============
    page.goto(PRODUCT_URL, timeout=25000)
    page.wait_for_timeout(2000)
    inject(page, "商品页")

    # ============ 3) 立即购买 -> 下单页 ============
    before = set(ctx.pages)
    page.click("a.go-buy")
    page.wait_for_timeout(6000)
    new_pages = [pg for pg in ctx.pages if pg not in before]
    co = new_pages[-1] if new_pages else page
    print("下单页:", co.url[:80], "|", co.title()[:30])

    co.wait_for_selector(".address-list", timeout=15000)
    co.wait_for_timeout(1500)
    inject(co, "下单页")
    co.wait_for_timeout(20000)   # 等自动填全流程（级联兜底最坏约15s）

    # ============ 4) 验收 ============
    res = co.evaluate("""() => {
        const sel = document.querySelector('.address-list li.selected');
        const dialog = document.querySelector('.el-dialog');
        const remark = document.querySelector('.remark-input input, .remark-input textarea');
        return {
            selected: sel ? sel.innerText.slice(0, 80) : null,
            dialog_open: dialog ? (dialog.offsetHeight > 0) : false,
            remark_val: remark ? remark.value : null,
            remark_entry: (document.querySelector(
                '.remark-entry-text') || {}).innerText || null,
            klog: window.__kfzLog || null,
            clip: window.__gm_clip || null,
            btn: !!document.getElementById('kfz-addr-btn'),
            region_txt: (document.querySelector(
                '.k-area-select-wrap .area-text') || {}).innerText || null,
            toasts: [...document.querySelectorAll('div')].filter(
                d => d.style.position === 'fixed' && d.style.top === '12px'
            ).map(d => d.innerText)
        };
    }""")
    print("验收:", json.dumps(res, ensure_ascii=False, indent=1))
    shot = config.BASE_DIR / "logs" / "e2e_checkout.png"
    co.screenshot(path=str(shot))
    print("截图:", shot)

    ctx.close()
    pw.stop()


if __name__ == "__main__":
    main()
