# -*- coding: utf-8 -*-
"""热书榜：多源榜单抓取 + 与在售选品交叉比对。

数据源（均为服务端渲染静态页，urllib 即可）：
- 孔夫子近7天畅销榜（总榜 3 页）
- 孔夫子当月畅销榜（总榜 2 页，补"持续在卖"的常销书）
- 孔夫子分类近7天榜（文学/小说/童书/历史/哲学心理学/社会文化/生活/教育）
- 豆瓣月度热门图书榜（风向标）
- 豆瓣图书 TOP250（常销经典清单）

孔夫子榜单 URL：book_{分类}_{时间窗}_0_{页}.html
  时间窗：2=近7天，3~14=1~12月，15+=年度榜；分类：0=总榜，其余见 _KFZ_CATS。
"""
import logging
import re
import time
import urllib.request
from datetime import datetime

import models
from services import order_service

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
       " Chrome/120.0 Safari/537.36")

_KFZ_URL = "https://item.kongfz.com/top/book_{}_{}_0_{}.html"
_DOUBAN_CHART = "https://book.douban.com/chart"
_DOUBAN_TOP250 = "https://book.douban.com/top250?start={}"

# 二手书主营类目（7天榜，每类1页20条）
_KFZ_CATS = {
    1: "文学", 43: "小说", 27: "童书", 3: "历史",
    44: "哲学心理学", 7: "社会文化", 26: "生活", 28: "教育",
}


def _fetch(url, referer=None, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _UA,
                              "Referer": referer or url})
            return urllib.request.urlopen(req, timeout=20).read().decode(
                "utf-8", "ignore")
        except Exception as e:
            log.warning("抓取 %s 失败(%d/%d)：%s", url, i + 1, retries, e)
            time.sleep(3)
    return ""


def _parse_kfz_page(html, rank_start):
    """解析一页孔夫子榜单。返回 (items, next_rank_start)。"""
    items, rank = [], rank_start
    segs = re.split(r'class="book-title"', html)[1:]
    for seg in segs:
        m = re.search(r'href="(https://item\.kongfz\.com/book/\d+\.html)"'
                      r'[^>]*>([^<]+)</a>', seg)
        if not m:
            continue
        book_url, title = m.group(1), m.group(2).strip()
        author = ""
        ma = re.search(
            r'<div class="book-info-content author">(.*?)</div>',
            seg, re.S)
        if ma:
            author = " ".join(
                re.sub(r"<[^>]+>", " ", ma.group(1)).split())
        price = ""
        mp = re.search(
            r'<div class="book-info-label">旧书</div>\s*'
            r'<div class="book-info-price">\s*￥([\d.]+)', seg)
        if not mp:
            mp = re.search(r'book-info-price">\s*￥([\d.]+)', seg)
        if mp:
            price = mp.group(1)
        press = ""
        mpr = re.search(r'class="book-info-content press"[^>]*>\s*'
                        r'<a[^>]*>([^<]+)</a>', seg)
        if mpr:
            press = mpr.group(1).strip()
        rank += 1
        items.append({
            "rank": rank, "title": title, "author": author,
            "extra": f"旧书￥{price}" if price else (press or ""),
            "book_url": book_url,
        })
    return items, rank


def fetch_kongfz(cat=0, window=2, pages=3):
    """孔夫子畅销榜。cat=分类(0总榜)，window=2近7天/月+2/年，每页20条。"""
    items, rank = [], 0
    for i in range(pages):
        html = _fetch(_KFZ_URL.format(cat, window, i + 1))
        if not html:
            break
        page_items, rank = _parse_kfz_page(html, rank)
        if not page_items:
            break
        items += page_items
        time.sleep(1)
    return items


def fetch_douban_chart():
    """豆瓣月度热门图书榜。"""
    html = _fetch(_DOUBAN_CHART, referer="https://book.douban.com/")
    if not html:
        return []
    items = []
    blocks = re.findall(
        r'<a class="fleft" href="(https://book\.douban\.com/subject/\d+/)"[^>]*>'
        r'([^<]+)</a>.*?subject-abstract[^>]*>(.*?)</p>',
        html, re.S)
    for i, (url, title, abstract) in enumerate(blocks):
        parts = [p.strip() for p in abstract.split("/")]
        author = parts[0] if parts else ""
        extra = " / ".join(parts[1:3]) if len(parts) > 2 else ""
        items.append({
            "rank": i + 1, "title": title.strip(), "author": author,
            "extra": extra, "book_url": url,
        })
    return items


def fetch_douban_top250(pages=10):
    """豆瓣图书 TOP250，每页25条。"""
    items, rank = [], 0
    for i in range(pages):
        html = _fetch(_DOUBAN_TOP250.format(i * 25),
                      referer="https://book.douban.com/")
        if not html:
            break
        blocks = re.split(r'class="item"', html)[1:]
        for seg in blocks:
            m = re.search(
                r'<a href="(https://book\.douban\.com/subject/\d+/)"[^>]*'
                r'\s+title="([^"]+)"', seg)
            if not m:
                m = re.search(
                    r'<a href="(https://book\.douban\.com/subject/\d+/)"[^>]*>'
                    r'\s*([^<]+)', seg)
            if not m:
                continue
            url, title = m.group(1), m.group(2).strip()
            info = ""
            mi = re.search(r'<p class="pl">(.*?)</p>', seg, re.S)
            parts = []
            if mi:
                parts = [p.strip() for p in mi.group(1).split("/")]
                info = " / ".join(parts[1:3]) if len(parts) > 2 else ""
            score = ""
            ms = re.search(r'rating_nums">([\d.]+)', seg)
            if ms:
                score = f" 评分{ms.group(1)}"
            rank += 1
            items.append({
                "rank": rank, "title": title,
                "author": parts[0] if parts else "",
                "extra": info + score, "book_url": url,
            })
        if not blocks:
            break
        time.sleep(1)
    return items


def fetch_all():
    """返回 [(source_label, items)]，按源组织。"""
    month_win = datetime.now().month + 2          # 时间窗：月 = 月份+2
    out = [("孔夫子7天榜", fetch_kongfz(0, 2, 3))]
    # 当月榜月初常为空，回退上月（完整月份数据更适合作"持续在卖"信号）
    month_items = fetch_kongfz(0, month_win, 2)
    if not month_items:
        month_items = fetch_kongfz(
            0, month_win - 1 if month_win > 3 else 14, 2)
    if month_items:
        out.append(("孔夫子月榜", month_items))
    for cid, cname in _KFZ_CATS.items():
        cat_items = fetch_kongfz(cid, 2, 1)
        if cat_items:
            out.append((f"孔夫子·{cname}", cat_items))
    out.append(("豆瓣月度", fetch_douban_chart()))
    out.append(("豆瓣TOP250", fetch_douban_top250()))
    return out


def refresh():
    """抓全部榜写入 hot_books（当天重抓覆盖式 upsert）。返回 [(源,条数)]。"""
    today = datetime.now().strftime("%Y-%m-%d")
    result = []
    for source, rows in fetch_all():
        for r in rows:
            models.execute(
                "INSERT INTO hot_books(source,rank_no,title,author,extra,"
                " book_url,fetched_date) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(source,fetched_date,title) DO UPDATE SET"
                " rank_no=excluded.rank_no, author=excluded.author,"
                " extra=excluded.extra, book_url=excluded.book_url",
                (source, r["rank"], r["title"], r["author"], r["extra"],
                 r["book_url"], today))
        result.append((source, len(rows)))
    log.info("热书榜刷新：%s",
             " / ".join(f"{s}{n}条" for s, n in result))
    return result


def list_latest(source=None, only_unlisted=False):
    """最新一天的榜单，按书名跨源去重合并，逐条标注在售状态。

    返回 [{title,author,extra,book_url,fetched_date,
          sources:[{label,rank}], products:[...]}]，按最好名次排序。"""
    row = models.query("SELECT MAX(fetched_date) d FROM hot_books", one=True)
    if not row or not row["d"]:
        return [], []
    sql = "SELECT * FROM hot_books WHERE fetched_date=?"
    params = [row["d"]]
    if source:
        sql += " AND source=?"; params.append(source)
    raw = models.query(sql + " ORDER BY rank_no", params)
    all_sources = [r["source"] for r in models.query(
        "SELECT DISTINCT source FROM hot_books WHERE fetched_date=?",
        (row["d"],))]
    products = models.query(
        "SELECT id,book_title,pure_title,status,seller_id"
        " FROM products WHERE status IN ('listed','draft')")
    merged = {}
    order = []
    for b in raw:
        key = order_service._norm(b["title"]) or b["title"]
        if key not in merged:
            d = dict(b)
            d["sources"] = []
            merged[key] = d
            order.append(key)
        d = merged[key]
        if not d["extra"] and b["extra"]:
            d["extra"] = b["extra"]
            d["author"] = d["author"] or b["author"]
        if source:
            d["sources"] = [{"label": b["source"], "rank": b["rank_no"]}]
        else:
            d["sources"].append({"label": b["source"], "rank": b["rank_no"]})
    out = []
    for key in order:
        d = merged[key]
        d["best_rank"] = min(s["rank"] for s in d["sources"]) \
            if d["sources"] else 999
        d["products"] = [
            {"id": p["id"], "seller_id": p["seller_id"],
             "status": p["status"], "title": p["book_title"]}
            for p in products
            if order_service.title_hit(d["title"],
                                       p["pure_title"] or p["book_title"] or "")]
        if only_unlisted and d["products"]:
            continue
        out.append(d)
    if not source:
        out.sort(key=lambda d: d["best_rank"])
    return out, all_sources
