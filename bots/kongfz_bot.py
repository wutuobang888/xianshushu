# -*- coding: utf-8 -*-
"""孔夫子自动化（Playwright）。

只做三件事：
1. search_books   搜索图书，返回 [书名/作者/出版社/年份/价格/链接/图片]
2. check_order    查询孔夫子订单是否已发货，返回 {shipped, waybill_no, express_name}
3. login_once     打开浏览器让你手动登录一次，登录态保存在 browser_data/

孔夫子是 Vue3 SPA（search.kongfz.com/product_result/?key=...），列表项选择器基于
当前版本的 DOM 结构：.product-item-box / a.item-link / .zl-info-item。页面改版时
优先调整 _extract_items_js 里的 JS 提取脚本。
"""
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import config

log = logging.getLogger(__name__)

KONGFZ_ORIGIN = "https://www.kongfz.com"
SEARCH_URL = "https://search.kongfz.com/product_result/?key={key}"
BOOKLIB_URL = "https://search.kongfz.com/booklib/?keyword={key}&sortType=20&page=1"

# 在页面里执行的提取脚本：遍历商品卡片，把字段抓成 dict 数组。
# 2026-10 孔夫子改版：每条结果 = .product-item-wrap；span.zl-info 无键名，
# 按位置依次为 作者/出版社/出版时间/装帧；价格在 .product-info-right。
_EXTRACT_ITEMS_JS = """() => {
    const wraps = document.querySelectorAll('.product-item-wrap');
    const out = [];
    for (const w of wraps) {
        const link = w.querySelector('a.item-link:not(.shop-name):not(.shop-message)')
                  || w.querySelector('a.img-box');
        const img = w.querySelector('a.img-box img') || w.querySelector('img');
        const zls = Array.from(w.querySelectorAll('span.zl-info'))
            .map(e => e.innerText.trim()).filter(Boolean);
        const attrs = {};
        if (zls[0]) attrs['作者'] = zls[0];
        if (zls[1]) attrs['出版社'] = zls[1];
        if (zls[2]) attrs['出版时间'] = zls[2];
        if (zls[3]) attrs['装帧'] = zls[3];
        const right = w.querySelector('.product-info-right');
        out.push({
            title: link ? link.innerText.trim() : '',
            url: link ? link.href : '',
            img: img ? (img.src || '') : '',
            price_text: right ? right.innerText.trim() : '',
            attrs
        });
    }
    return out;
}"""


def _channel_options():
    return {"channel": config.KONGFZ_BROWSER_CHANNEL} if config.KONGFZ_BROWSER_CHANNEL != "chromium" else {}


def _launch(playwright, headless=None):
    from services.browser_session import launch_persistent
    hl = config.KONGFZ_HEADLESS if headless is None else headless
    args = ["--disable-blink-features=AutomationControlled"]
    if not hl and not config.CLOUD_MODE:
        # 可见窗口：挂下单上报扩展（用户在窗口里提交订单时秒报本地）。
        # 无头模式不挂：MV3 扩展在老 headless 下不生效，且监控不需要它。
        args.append(f"--load-extension={config.BASE_DIR / 'extension'}")
    return launch_persistent(
        playwright.chromium, config.KONGFZ_USER_DATA_DIR,
        headless=hl,
        viewport={"width": 1366, "height": 900},
        args=args,
        **_channel_options(),
    )


def _norm_book(raw: dict) -> dict:
    attrs = raw.get("attrs") or {}
    price = None
    pt = (raw.get("price_text") or "").replace(",", "")
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", pt) or re.search(r"(\d+(?:\.\d+)?)", pt)
    if m:
        try:
            price = float(m.group(1))
        except ValueError:
            price = None
    return {
        "book_title": raw.get("title") or "",
        "author": attrs.get("作者") or attrs.get("author") or "",
        "publisher": attrs.get("出版社") or attrs.get("press") or "",
        "pub_year": attrs.get("出版时间") or attrs.get("年代") or attrs.get("年份") or "",
        "isbn": attrs.get("ISBN") or attrs.get("isbn") or "",
        "kongfz_price": price,
        "kongfz_url": raw.get("url") or "",
        "image_url": raw.get("img") or "",
        "raw_attrs": attrs,
    }


def search_books(keyword: str, limit: int = 20, timeout_ms: int = 45000) -> list[dict]:
    """搜索孔夫子图书。keyword 支持书名或 ISBN。"""
    from playwright.sync_api import sync_playwright

    kw = (keyword or "").strip()
    if not kw:
        return []
    results = []
    with sync_playwright() as p:
        ctx = _launch(p)
        try:
            page = ctx.new_page()
            page.goto(SEARCH_URL.format(key=_quote(kw)), timeout=timeout_ms)
            page.wait_for_selector(".product-item-wrap, .product-item-box,"
                                   " .empty-wrap, .list-loading-wrap",
                                   timeout=timeout_ms)
            page.wait_for_timeout(1500)  # 等列表稳定
            raw_items = page.evaluate(_EXTRACT_ITEMS_JS)
            for raw in raw_items[:limit]:
                book = _norm_book(raw)
                if book["book_title"]:
                    results.append(book)
        finally:
            ctx.close()
    return results


# 图书条目页提取脚本：每条 = .comp-entry-card，代表一个版本（书目条目）。
# base-info 第二行格式：作者 / 出版社 / 出版时间 / 装帧 / 定价；
# other-info 里有「新书￥X」「N 书店有售」「旧书￥Y」。
_EXTRACT_ENTRIES_JS = """() => {
    const cards = document.querySelectorAll('.comp-entry-card');
    const out = [];
    for (const c of cards) {
        const base = c.querySelector('.base-info');
        const a = (base && base.querySelector('a[href]')) || c.querySelector('a[href]');
        const img = c.querySelector('img');
        const baseText = base ? base.innerText.trim() : '';
        const lines = baseText.split('\\n').map(s => s.trim()).filter(Boolean);
        const meta = (lines[1] || '').split('/').map(s => s.trim());
        const others = Array.from(c.querySelectorAll('.other-info a'))
            .map(x => x.innerText.trim()).filter(Boolean);
        out.push({
            title: lines[0] || (a ? a.innerText.trim() : ''),
            url: a ? a.href : '',
            img: img ? (img.src || '') : '',
            meta, others
        });
    }
    return out;
}"""


def _norm_entry(raw: dict) -> dict:
    """把图书条目卡片规范化成与 _norm_book 一致的字段结构。"""
    meta = raw.get("meta") or []
    others = raw.get("others") or []
    price = None
    stock_text = ""
    for t in others:
        m = re.search(r"[旧古][书|本]?\s*[¥￥]\s*(\d+(?:\.\d+)?)", t)
        if m:
            price = float(m.group(1))
        if "书店有售" in t or "有售" in t:
            stock_text = t
    if price is None:
        for t in others:
            m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", t)
            if m:
                price = float(m.group(1))
                break
    return {
        "book_title": raw.get("title") or "",
        "author": meta[0] if len(meta) > 0 else "",
        "publisher": meta[1] if len(meta) > 1 else "",
        "pub_year": meta[2] if len(meta) > 2 else "",
        "binding": meta[3] if len(meta) > 3 else "",
        "list_price": meta[4] if len(meta) > 4 else "",
        "isbn": "",
        "kongfz_price": price,
        "kongfz_url": (raw.get("url") or "").split("?")[0],
        "image_url": raw.get("img") or "",
        "stock_text": stock_text,
    }


def search_entries(keyword: str, limit: int = 30, timeout_ms: int = 45000) -> list[dict]:
    """按孔夫子图书条目销量排序搜索，每个结果代表一个版本。"""
    from playwright.sync_api import sync_playwright

    kw = (keyword or "").strip()
    if not kw:
        return []
    results = []
    with sync_playwright() as p:
        # 公开书目搜索不需要登录，使用独立上下文，避免争用采购/监控的登录目录。
        browser = p.chromium.launch(headless=True, **_channel_options(),
                                     args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            page.goto(BOOKLIB_URL.format(key=_quote(kw)), timeout=timeout_ms,
                      wait_until="domcontentloaded")
            page.wait_for_selector(".comp-entry-card, .empty-wrap, .error-title:has-text('暂无结果')",
                                   timeout=timeout_ms)
            page.wait_for_timeout(1500)
            for raw in page.evaluate(_EXTRACT_ENTRIES_JS)[:limit]:
                e = _norm_entry(raw)
                if e["book_title"] and e["kongfz_url"]:
                    results.append(e)
        finally:
            browser.close()
    return results


def resolve_entry_url(keyword: str, timeout_ms: int = 45000) -> str:
    """搜图书条目取第一条的条目页 URL；找不到返回 ''。"""
    entries = search_entries(keyword, limit=1, timeout_ms=timeout_ms)
    return entries[0]["kongfz_url"] if entries else ""


# ---------- 卖家实拍图抓取 ----------

# 条目页在售卖家列表：每项 = .book-detail-list-item，含店名/品相/价格/发货速度。
_EXTRACT_SELLERS_JS = """() => {
    const out = [];
    for (const it of document.querySelectorAll('.book-detail-list-item')) {
        const a = Array.from(it.querySelectorAll('a[href]'))
            .find(x => /book\\.kongfz\\.com\\/\\d+\\/\\d+/.test(x.href));
        if (!a) continue;
        const shopA = it.querySelector('a[href*="shop.kongfz.com"]');
        let shop = shopA ? shopA.textContent.trim() : '';
        const text = it.textContent.replace(/\\s+/g, ' ');
        if (!shop) {
            const m = text.match(/([^\\s￥]{2,25}?)\\s*成功完成/);
            shop = m ? m[1] : '';
        }
        const pm = text.match(/￥\\s*(\\d+(?:\\.\\d+)?)/);
        out.push({url: a.href.split('?')[0], shop_name: shop,
                  shop_price: pm ? parseFloat(pm[1]) : null});
    }
    return out;
}"""

# 商品详情页实拍图：图床 URL 形如 ...-XX_s.jpg，_s/_n/_b 是尺寸后缀，取 _b 大图。
_EXTRACT_ITEM_IMAGES_JS = """() => {
    const urls = [];
    for (const i of document.querySelectorAll('img')) {
        const s = i.src || i.getAttribute('data-src')
               || i.getAttribute('data-original') || '';
        if (/kfzimg\\.com\\/.*-[A-Za-z0-9]{2}_(?:s|n|b)\\.jpg/.test(s))
            urls.push(s);
    }
    return urls;
}"""


def fetch_listing_images(entry_url: str, max_shops: int = 10,
                         skip_urls=None, timeout_ms: int = 45000) -> list[dict]:
    """条目页 -> 跳过已抓过的店，抓下 max_shops 家在售卖家的实拍图集。

    skip_urls: 图池里已有来源商品页 URL（换一批时跳过旧店）
    返回 [{shop_name, url(商品页), shop_price, images:[大图URL,...]}]。
    """
    from playwright.sync_api import sync_playwright

    # 与订单"去货源"一致：价格升序 + 八品以上，差品卖家的糙图不进池
    if "sortType=" not in entry_url:
        entry_url += ("&" if "?" in entry_url else "?") + "sortType=7&quality=80~"
    skip_urls = set(skip_urls or [])

    shops, seen = [], set()
    with sync_playwright() as p:
        ctx = _launch(p)
        try:
            page = ctx.new_page()
            # 卖家列表可能分页：翻页直到攒够新店或没有更多卖家
            for pg in range(1, 4):
                page.goto(entry_url + f"&page={pg}", timeout=timeout_ms)
                page.wait_for_selector(".book-detail-list-item",
                                       timeout=timeout_ms)
                page.wait_for_timeout(1500)
                found_new = False
                for s in page.evaluate(_EXTRACT_SELLERS_JS):
                    u = s.get("url") or ""
                    if not u or u in seen or u in skip_urls:
                        continue
                    seen.add(u)
                    shops.append(s)
                    found_new = True
                    if len(shops) >= max_shops:
                        break
                if len(shops) >= max_shops or not found_new:
                    break
            log.info("条目页 %s 抓 %d 家新店（跳过已抓 %d 家）",
                     entry_url, len(shops), len(skip_urls))
            for s in shops:
                p2 = ctx.new_page()
                try:
                    p2.goto(s["url"], timeout=timeout_ms)
                    p2.wait_for_timeout(2000)
                    raw = p2.evaluate(_EXTRACT_ITEM_IMAGES_JS)
                except Exception as e:
                    log.warning("抓商品页图片失败 %s：%s", s["url"], e)
                    raw = []
                finally:
                    p2.close()
                bigs, seen_img = [], set()
                for u in raw:
                    b = re.sub(r"_(?:s|n)\.jpg", "_b.jpg", u)
                    if b not in seen_img:
                        seen_img.add(b)
                        bigs.append(b)
                s["images"] = bigs
        finally:
            ctx.close()
    return [s for s in shops if s.get("images")]


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


# ---------- 订单发货查询 ----------

# 在“我的订单”列表页内按订单号定位并提取物流信息。
_CHECK_ORDER_JS = """(orderNo) => {
    const text = document.body.innerText;
    if (!text.includes(orderNo)) return {found: false};
    const nodes = Array.from(document.querySelectorAll('*'));
    let box = null;
    for (const el of nodes) {
        if (el.children.length === 0 && el.innerText && el.innerText.includes(orderNo)) {
            box = el;
            break;
        }
    }
    // 向上找包含整块订单的卡片区
    let card = box;
    for (let i = 0; i < 8 && card; i++) {
        if (card.innerText && card.innerText.length > orderNo.length + 30) break;
        card = card.parentElement;
    }
    const cardText = (card && card.innerText) || '';
    const shipped = /已发货|卖家已发货|物流|运单/.test(cardText);
    // 尝试在卡片里找物流单号（连续 8 位以上字母数字）
    let waybill = '';
    const m = cardText.match(/(?:运单|物流|快递)[:：\\s]*([A-Za-z0-9]{8,})/);
    if (m) waybill = m[1];
    let express = '';
    const me = cardText.match(/(顺丰|圆通|中通|申通|韵达|邮政|EMS|京东|极兔|百世|德邦)[^\\s]{0,10}/);
    if (me) express = me[0];
    return {found: true, shipped, waybill_no: waybill, express_name: express,
            card_text: cardText.slice(0, 500)};
}"""

ORDER_LIST_URLS = [
    "https://order.kongfz.com/order-views/management/buyer/list",
]

# 买家订单列表卡片里已含：单号/状态/书名/订单总额/收货人信息——
# 一次页面全拿到，自动绑定不用再开详情页。
_LIST_CARDS_JS = """() => {
  const out=[];
  for(const card of document.querySelectorAll('.table-item')){
    const t=card.innerText||'';
    const mno=t.match(/订单号[：:]\\s*(\\d+)/);
    if(!mno)continue;
    const mrecv=t.match(/收货人信息[：:]\\s*([^\\n]+)/);
    const mtot=t.match(/订单总额[\\s¥￥]*([\\d.]+)/);
    const mdate=t.match(/(\\d{4}-\\d{2}-\\d{2})/);
    const st=['等待卖家发货','待收货','待付款','成功完成','买家取消',
              '卖家取消','退款完成','退货'].find(s=>t.includes(s))||'';
    let title='';
    for(const a of card.querySelectorAll('a')){          // 书名链接 href 都含 book.kongfz.com
      const x=(a.innerText||'').trim();
      if((a.href||'').includes('book.kongfz.com')&&x.length>title.length)title=x;
    }
    if(!title){                                        // 兜底：最长的非操作类链接文本
      for(const a of card.querySelectorAll('a')){
        const x=(a.innerText||'').trim();
        if(x.length>title.length
           &&!/详情|消息|付款|取消|删除|收货|重新购买|退款|退货|评价/.test(x))
          title=x;
      }
    }
    out.push({no:mno[1],status:st,title,
      total:mtot?parseFloat(mtot[1]):null,
      date:mdate?mdate[1]:'',
      receiver:mrecv?mrecv[1].trim():''});
  }
  return out;
}"""


_LIST_READY_JS = """() =>
  [...document.querySelectorAll('.table-item')].some(card =>
    /订单号[：:]\\s*\\d+/.test(card.innerText || ''))
  || /暂无订单|没有相关订单|没有符合条件的订单/.test(document.body.innerText)
"""


def _next_order_page(page):
    """定位下一页；禁用表示末页，未知分页结构报错，避免误报完整扫描。"""
    controls = page.locator(
        ".el-pagination .btn-next, .ant-pagination-next, .ivu-page-next,"
        " .pagination .next, .pager .next, a[rel='next'],"
        " [aria-label='下一页'], [aria-label='Next page']")
    for candidates in (controls, page.get_by_text(
            re.compile(r"^(下一页|下页|Next)$", re.I))):
        for i in range(candidates.count()):
            control = candidates.nth(i)
            if not control.is_visible():
                continue
            disabled = control.evaluate("""el => Boolean(el.closest(
              '[disabled], [aria-disabled="true"], .disabled, .is-disabled,'
              + ' .ant-pagination-disabled, .ivu-page-disabled'))""")
            return None if disabled or not control.is_enabled() else control
    pagers = page.locator(
        ".el-pagination, .ant-pagination, .ivu-page, .pagination, .pager,"
        " [class*='pagination'], [class*='pager']")
    if any(pagers.nth(i).is_visible() for i in range(pagers.count())):
        raise ValueError("订单列表存在分页，但无法识别下一页控件")
    return None


def _scan_buyer_order_pages(page, timeout_ms=45000, max_pages=1000):
    """逐页扫描并去重；失败保留诊断信息，不把部分结果当成完整扫描。"""
    orders, seen_pages = {}, set()
    pages = 0
    try:
        for _ in range(max_pages):
            page.wait_for_function(_LIST_READY_JS, timeout=timeout_ms)
            batch = page.evaluate(_LIST_CARDS_JS)
            signature = tuple(s["no"] for s in batch)
            if signature in seen_pages:
                raise ValueError("翻页后订单列表重复，扫描中止")
            seen_pages.add(signature)
            pages += 1
            for s in batch:
                orders[s["no"]] = s
            next_page = _next_order_page(page)
            if next_page is None:
                return {"orders": list(orders.values()), "complete": True,
                        "pages": pages}
            if not batch:
                raise ValueError("订单列表为空但仍存在下一页，扫描中止")
            if pages == max_pages:
                raise ValueError(f"订单扫描达到 {max_pages} 页上限，尚未扫完")
            next_page.click(timeout=timeout_ms)
            # 等数据真正换页，不能用固定延时抓到上一页的旧卡片。
            page.wait_for_function(
                "previous => { const current = (" + _LIST_CARDS_JS + """)();
                  return current.length > 0 && JSON.stringify(
                    current.map(s => s.no)) !== JSON.stringify(previous);
                }""",
                arg=list(signature),
                timeout=timeout_ms)
        raise ValueError("订单扫描页数上限必须大于零")
    except Exception as e:
        return {"orders": list(orders.values()), "complete": False,
                "pages": pages, "error": f"订单分页扫描失败: {e}"}


def _select_trading_orders(page, timeout_ms):
    """选择交易中订单，并等筛选请求完成，不能把旧列表当成筛选结果。"""
    tabs = page.get_by_text("交易中订单", exact=True)
    visible = [tabs.nth(i) for i in range(tabs.count()) if tabs.nth(i).is_visible()]
    if len(visible) != 1:
        raise ValueError("无法唯一定位交易中订单筛选")
    tab = visible[0]
    if not tab.evaluate("el => Boolean(el.closest('.tab.active'))"):
        with page.expect_response(
                lambda r: r.url.split("?", 1)[0].endswith("/buyer/order/list"),
                timeout=timeout_ms) as pending:
            tab.click(timeout=timeout_ms)
        response = pending.value
        if response.status != 200:
            raise ValueError(f"交易中订单筛选失败：HTTP {response.status}")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") is not True:
            raise ValueError("交易中订单筛选未得到成功结果")
    page.wait_for_function("""() => [...document.querySelectorAll('.tab.active')]
        .some(el => el.innerText.trim() === '交易中订单')""", timeout=timeout_ms)
    page.wait_for_function(_LIST_READY_JS, timeout=timeout_ms)


def list_buyer_orders(timeout_ms=45000, active_only=False):
    """完整扫描选定范围；active_only 跳过历史已结束订单，默认仍支持全量核对。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _launch(p)
        try:
            page = ctx.new_page()
            try:
                _goto_order_page(page,ORDER_LIST_URLS[0],timeout_ms)
            except Exception as e:
                return {"orders": [], "error": f"打开订单列表失败: {e}"}
            if ("login" in page.url.lower()
                    or "用户登录" in (page.title() or "")) \
                    and not _looks_logged_in(page):
                from services.kongfz_auth import try_login
                if not try_login(page):
                    return {"orders": [], "error": "孔夫子未登录"}
            if active_only:
                try:
                    _select_trading_orders(page, timeout_ms)
                except Exception as e:
                    return {"orders": [], "complete": False,
                            "error": f"交易中订单筛选失败: {e}"}
            result = _scan_buyer_order_pages(page, timeout_ms)
            result["scope"] = "active" if active_only else "all"
            return result
        finally:
            ctx.close()


def _parse_shipping_info(text: str) -> dict:
    """兼容订单操作记录及新版物流卡片，不从收货人或电话字段猜运单。"""
    result = {"shipped": False, "waybill_no": "", "express_name": "", "detail": ""}
    match = re.search(
        r"卖家发货[，,]?\s*快递【([^】]+)】[：:]\s*([A-Za-z0-9]{6,30})(?![A-Za-z0-9])",
        text,
    )
    if not match and "物流信息" in text:
        section = text.split("物流信息", 1)[1].split("收货信息", 1)[0][:400]
        match = re.search(
            r"\A[\s：:]*(?:快递公司[\s：:]*)?"
            r"((?:中国)?(?:顺丰|圆通|中通|申通|韵达|邮政|EMS|京东|极兔|百世|德邦|丰网|快递包裹)"
            r"[^\d\r\n：:]{0,12}?)\s*[：:]?\s*"
            r"([A-Za-z0-9]{8,30})(?![A-Za-z0-9])",
            section,
        )
    if match and any(character.isdigit() for character in match.group(2)):
        result.update(shipped=True, express_name=match.group(1).strip(),
                      waybill_no=match.group(2).strip())
    elif "物流信息" in text or "待收货" in text:
        result["detail"] = "订单有物流栏目或待收货状态，但未识别到可靠运单，不能据此判断未发货"
    else:
        result["detail"] = "订单详情中未发现发货记录或运单"
    return result


def check_order(kongfz_order_no: str, timeout_ms: int = 45000) -> dict:
    """查询孔夫子订单发货状态。

    返回 {found, shipped, waybill_no, express_name, detail}。
    直接打开订单详情页 /buyer/detail/{单号}（URL 规律固定），
    物流信息区有结构化的"快递【公司】：运单号"，比列表页卡片可靠。
    """
    return check_orders([kongfz_order_no], timeout_ms=timeout_ms)[0]


def _goto_order_page(page, url, timeout_ms):
    """订单 SPA 的接口未登录跳转不会切换主页面，须识别后进入官方登录页。"""
    login_redirect = False

    def observe(response):
        nonlocal login_redirect
        if response.status in (301,302,303,307,308):
            destination = urlparse(urljoin(response.url,response.headers.get('location','')))
            if destination.hostname == 'login.kongfz.com':
                login_redirect = True

    page.on('response',observe)
    try:
        page.goto(url,timeout=timeout_ms,wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        if login_redirect and urlparse(page.url).hostname != 'login.kongfz.com':
            page.goto('https://login.kongfz.com/',timeout=timeout_ms,wait_until='domcontentloaded')
            page.wait_for_timeout(1000)
    finally:
        page.remove_listener('response',observe)


def _read_shipping_page(page, kongfz_order_no, timeout_ms):
    result = {"found": False, "shipped": False, "waybill_no": "",
              "express_name": "", "detail": ""}
    detail_url = ("https://order.kongfz.com/order-views/management/buyer/"
                  f"detail/{kongfz_order_no}")
    try:
        _goto_order_page(page,detail_url,timeout_ms)
        if ("login.kongfz.com" in page.url.lower()
                or "用户登录" in (page.title() or "")):
            from services.kongfz_auth import try_login
            if try_login(page):
                _goto_order_page(page,detail_url,timeout_ms)
        if ("login.kongfz.com" in page.url.lower()
                or "用户登录" in (page.title() or "")):
            result["detail"] = "孔夫子未登录，请先运行 kongfz_login.bat 登录"
            return result
        text = page.inner_text("body")
        if not re.search(r"(?<!\d)" + re.escape(kongfz_order_no) + r"(?!\d)", text):
            result["detail"] = "订单详情页未找到该订单号"
            return result
        result["found"] = True
        result.update(_parse_shipping_info(text))
        if config.CLOUD_MODE:
            from services.kongfz_auth import set_login_status
            set_login_status("logged_in")
    except Exception as e:
        result["detail"] = f"打开订单详情页失败: {e}"
    return result


def check_orders(order_nos, timeout_ms=45000, login_timeout_sec=0):
    """在同一会话只读查单；交互模式登录后直接查询，避免关闭会话丢失登录态。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=False if login_timeout_sec else None)
        try:
            page = ctx.new_page()
            results = []
            for no in order_nos:
                result = _read_shipping_page(page, no, timeout_ms)
                if login_timeout_sec and "未登录" in result["detail"]:
                    print("请在打开的孔夫子窗口登录；本次会保留同一会话直接核对订单。",
                          file=sys.stderr, flush=True)
                    deadline = time.monotonic() + login_timeout_sec
                    while time.monotonic() < deadline:
                        if any(_looks_logged_in(pg) for pg in ctx.pages):
                            result = _read_shipping_page(page, no, timeout_ms)
                            break
                        page.wait_for_timeout(1000)
                    login_timeout_sec = 0
                results.append(result)
            return results
        finally:
            ctx.close()


def _looks_logged_in(page) -> bool:
    try:
        text = page.inner_text("body")
        title = page.title() or ""
        from urllib.parse import urlparse
        host = urlparse(page.url).hostname or ""
    except Exception:
        return False
    if host == "login.kongfz.com" or "用户登录" in title:
        return False
    if host == "order.kongfz.com" and "订单号" in text:
        return True
    return (host == "kongfz.com" or host.endswith(".kongfz.com")) and (
        "退出登录" in text or "安全退出" in text)


def _open_order_detail(page, order_no, timeout_ms):
    """在订单列表里点击包含订单号的链接进入详情页提取运单号。"""
    try:
        link = page.locator(f"a:has-text('{order_no}')").first
        if not link.count():
            return None
        href = link.get_attribute("href")
        if href:
            page.goto(href if href.startswith("http") else KONGFZ_ORIGIN + href,
                      timeout=timeout_ms)
        else:
            link.click()
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
        m = re.search(r"(?:运单|物流单号|快递单号)[:：\s]*([A-Za-z0-9]{8,})", text)
        me = re.search(r"(顺丰|圆通|中通|申通|韵达|邮政|EMS|京东|极兔|百世|德邦)\S{0,10}",
                       text)
        return {
            "waybill_no": m.group(1) if m else "",
            "express_name": me.group(0) if me else "",
        }
    except Exception as e:
        log.debug("进入订单详情失败：%s", e)
        return None


def login_once(timeout_sec: int = 600) -> bool:
    """打开浏览器让你手动登录孔夫子，登录态会保存到 browser_data/。

    每 3 秒静默读取当前已打开页面；仅凭订单页或退出入口确认登录成功，
    不能把缺少登录提示的空白页当作已登录。
    超时（默认 10 分钟）返回 False。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.new_page()
        page.goto("https://login.kongfz.com/" if config.CLOUD_MODE else "https://order.kongfz.com/order-views/management/buyer/list")
        from services.kongfz_auth import try_login, set_login_status
        if "login.kongfz.com" in page.url:
            try_login(page)
        print("已打开孔夫子。请在窗口中完成登录；检测到登录后窗口会自动关闭。")
        deadline = time.time() + timeout_sec
        ok = False
        while time.time() < deadline:
            for pg in ctx.pages:
                try:
                    if not pg.url.startswith("http"):
                        continue
                    if len(pg.inner_text("body") or "") < 100:
                        continue
                    if _looks_logged_in(pg):
                        ok = True
                        break
                except Exception:
                    continue
            if ok:
                break
            time.sleep(3)
        ctx.close()
        if config.CLOUD_MODE:
            set_login_status("logged_in" if ok else "verification_required",login_pid=None)
        print("检测到孔夫子登录态，已保存。" if ok else "超时未检测到登录。")
        return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"
    if cmd == "login":
        login_once()
    elif cmd == "search":
        kw = sys.argv[2] if len(sys.argv) > 2 else "活着"
        print(json.dumps(search_books(kw), ensure_ascii=False, indent=2))
    elif cmd == "check":
        nos = [arg for arg in sys.argv[2:] if arg != "--wait-login"]
        if not nos or any(not no.isdigit() for no in nos):
            raise SystemExit("用法：check 订单号 [订单号 ...] [--wait-login]")
        results = check_orders(nos, login_timeout_sec=600 if "--wait-login" in sys.argv else 0)
        output = results[0] if len(nos) == 1 else dict(zip(nos, results))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif cmd == "export-state":
        if len(sys.argv) != 3:
            raise SystemExit("用法：export-state 保存路径（包含登录凭据，请勿公开）")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            context = _launch(runtime)
            try:
                context.storage_state(path=sys.argv[2])
            finally:
                context.close()
        print("登录状态已保存，未输出任何凭据。")
