# -*- coding: utf-8 -*-
"""探测：booklib（图书条目）页的条目卡片结构。"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(__file__).rsplit("\\", 2)[0])

from playwright.sync_api import sync_playwright
from bots import kongfz_bot

JS = r"""() => {
    const cards = document.querySelectorAll('.comp-entry-card');
    const out = {count: cards.length, items: []};
    for (const c of Array.from(cards).slice(0, 6)) {
        const titleLink = c.querySelector('.base-info a[href], .base-info__title a[href]');
        const otherLinks = Array.from(c.querySelectorAll('.other-info a[href]'))
            .map(a => ({href: a.href, txt: a.innerText.trim()}));
        out.items.push({
            cls: c.className,
            href: titleLink ? titleLink.href : '',
            titleTxt: titleLink ? titleLink.innerText.trim() : '',
            baseText: (c.querySelector('.base-info') || {}).innerText || '',
            others: otherLinks,
            full: c.innerText.slice(0, 200),
        });
    }
    return out;
}"""

with sync_playwright() as p:
    ctx = kongfz_bot._launch(p)
    page = ctx.new_page()
    page.goto("https://search.kongfz.com/booklib/?keyword=%E6%88%91%E4%B8%8E%E5%9C%B0%E5%9D%9B",
              timeout=45000)
    page.wait_for_timeout(4000)
    print("URL:", page.url)
    print("TITLE:", page.title())
    info = page.evaluate(JS)
    print("count:", info["count"])
    for it in info["items"]:
        print("---")
        for k, v in it.items():
            print(" ", k, ":", v)
    ctx.close()
