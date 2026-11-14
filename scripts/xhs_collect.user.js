// ==UserScript==
// @name         闲鱼选品-小红书收图
// @namespace    xianshouself
// @version      2.7
// @description  从网站图池打开小红书笔记或孔夫子卖家商品页，点击收图回到当前书籍图池。
//               从网站图池打开，目标随链接绑定到标签页；不读取后台全局旧目标。
// @match        https://www.xiaohongshu.com/*
// @match        https://item.kongfz.com/*
// @match        https://book.kongfz.com/*
// @match        https://search.kongfz.com/*
// @match        https://shop.kongfz.com/*
// @updateURL    http://127.0.0.1:5000/scripts/xhs_collect.user.js
// @downloadURL  http://127.0.0.1:5000/scripts/xhs_collect.user.js
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    const API = 'http://127.0.0.1:5000/api/images/collect';
    const TARGET_KEY = 'xs_collect_target_v2';
    const isKongfz = /^https:\/\/(?:item|book|search|shop)\.kongfz\.com\//.test(location.href);
    let tabTarget = null;
    function currentTarget() {
        const params = new URLSearchParams(location.hash.slice(1));
        if (params.has('xs_collect') && params.get('entry_url')) {
            tabTarget = {entry_url: params.get('entry_url'), title: params.get('book_title') || '当前书籍'};
            try { sessionStorage.setItem(TARGET_KEY, JSON.stringify(tabTarget)); } catch (e) {}
        }
        if (!tabTarget) {
            try { tabTarget = JSON.parse(sessionStorage.getItem(TARGET_KEY) || 'null'); } catch (e) {}
        }
        return tabTarget && tabTarget.entry_url ? tabTarget : null;
    }
    if (currentTarget()) boot();
    window.addEventListener('hashchange', () => { if (currentTarget()) ensureButton(); });

    function boot() {
        new MutationObserver(ensureButton).observe(
            document.body, { childList: true, subtree: true });
        ensureButton();
        if (isKongfz) {
            // 跨子域/新标签页不能共享sessionStorage，把目标继续带到卖家链接。
            document.addEventListener('click', forwardTarget, true);
            document.addEventListener('auxclick', forwardTarget, true);
        }
        setTimeout(() => toast(`收图目标：《${currentTarget().title}》，${isKongfz ? '进入卖家商品详情页' : '打开笔记'}后点「收图」`), 1200);
    }

    function forwardTarget(event) {
        const anchor = event.target.closest?.('a[href]');
        const target = currentTarget();
        if (!anchor || !target) return;
        const url = new URL(anchor.href, location.href);
        if (!/^(item|book|search|shop)\.kongfz\.com$/.test(url.hostname)) return;
        url.hash = 'xs_collect&entry_url=' + encodeURIComponent(target.entry_url)
            + '&book_title=' + encodeURIComponent(target.title);
        anchor.href = url.href;
    }

    function isSellerDetail() {
        return /^https:\/\/book\.kongfz\.com\/\d+\/\d+(?:\/|[?#]|$)/.test(location.href);
    }

    function collectKongfzImages() {
        const urls = new Set();
        // 优先限定商品实拍区，避免把同页其他商品和书目封面收进来。
        const galleryImages = document.querySelectorAll('.exhibition .lg-list img, .exhibition .sm-list img, .link-openBigImg img');
        const images = galleryImages.length ? galleryImages : document.querySelectorAll('img');
        for (const image of images) {
            if (image.closest('[class*="recommend"], [class*="related"], [class*="advert"], .avatar')) continue;
            for (const raw of [image.getAttribute('data-imgurl'), image.getAttribute('_viewsrc'),
                image.getAttribute('data-original'), image.getAttribute('data-src'),
                image.getAttribute('_src'), image.currentSrc, image.src]) {
                if (!raw) continue;
                let url;
                try { url = new URL(raw, location.href); } catch (e) { continue; }
                if (!/^https?:$/.test(url.protocol) || !/(^|\.)kfzimg\.com$/.test(url.hostname)) continue;
                // 兼容旧实拍地址及新版 kfz-cos 图床；缺少商品图片区时保留严格旧规则。
                const photoPattern = galleryImages.length ? /_(s|n|b)\.jpg$/i : /-[A-Za-z0-9]{2}_(s|n|b)\.jpg$/i;
                if (!photoPattern.test(url.pathname)) continue;
                url.protocol = 'https:';
                url.pathname = url.pathname.replace(/_(s|n)\.jpg$/i, '_b.jpg');
                urls.add(url.href);
                break;
            }
        }
        return [...urls];
    }

    function toast(msg, ok = true) {
        let t = document.getElementById('xhs-collect-toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'xhs-collect-toast';
            t.style.cssText = 'position:fixed;top:70px;left:50%;transform:translateX(-50%);'
                + 'z-index:999999;padding:10px 22px;border-radius:8px;font-size:14px;'
                + 'color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.25);transition:opacity .3s';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.style.background = ok ? '#07c160' : '#ff4757';
        t.style.opacity = '1';
        clearTimeout(t._timer);
        t._timer = setTimeout(() => { t.style.opacity = '0'; }, 3000);
    }

    // 收集当前打开笔记的图片：限定在笔记媒体区，避免把推荐流封面误收进来
    function collectNoteImages() {
        if (isKongfz) return collectKongfzImages();
        const urls = new Set();
        const scopes = document.querySelectorAll(
            '#detail-container, .note-detail-mask, .note-detail, '
            + '[class*="swiper"], [class*="slider"], [class*="media-container"], '
            + '.note-scroller, .note-image, .carousel');
        scopes.forEach(box => {
            box.querySelectorAll('img').forEach(i => {
                const s = i.src || i.getAttribute('data-src') || '';
                if (/xhscdn\.com\//.test(s)
                    && !/avatar|emoji|icon|logo|search/i.test(s)
                    && i.naturalWidth > 200) {
                    urls.add(s);
                }
            });
        });
        const og = document.querySelector('meta[property="og:image"]');
        if (og && og.content) urls.add(og.content);
        return [...urls];
    }

    function noteTitle() {
        const t = document.querySelector(isKongfz ? 'h1, .detail-title, .book-title' : '#detail-title, .title, .desc');
        return (t && t.textContent.trim()) || document.title || '';
    }

    function post(urls) {
        const target = currentTarget();
        if (!target) { toast('缺少收图目标，请从网站图池重新打开找图网站', false); return; }
        GM_xmlhttpRequest({
            method: 'POST',
            url: API,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({
                urls: urls,
                note_url: location.href.split(/[?#]/)[0],
                note_title: noteTitle().slice(0, 80),
                entry_url: target.entry_url,
                source: isKongfz ? 'kongfz' : 'xhs',
            }),
            onload(r) {
                try {
                    const d = JSON.parse(r.responseText);
                    if (r.status >= 200 && r.status < 300 && d.ok && d.count > 0 && d.target === target.entry_url)
                        toast(`✓ 已收 ${d.count} 张图到《${d.title || target.title}》`);
                    else toast(d.error || '收图失败', false);
                } catch (e) { toast('回传失败: ' + e, false); }
            },
            onerror() { toast('连不上本地网站(127.0.0.1:5000)，网站开着吗？', false); },
        });
    }

    function doCollect() {
        if (isKongfz && !isSellerDetail()) { toast('请先进入某家卖家的商品详情页，再收实拍图', false); return; }
        const urls = collectNoteImages();
        if (!urls.length) { toast(isKongfz ? '未识别到商品实拍图，请刷新页面重试；仍失败请反馈该商品链接' : '没抓到图——先点开一篇笔记再收', false); return; }
        post(urls);
    }

    function ensureButton() {
        const label = isKongfz && !isSellerDetail() ? `请进入卖家商品页 ·《${currentTarget().title}》` : `📥 收图到《${currentTarget().title}》`;
        const existing = document.getElementById('xhs-collect-float');
        // 内容相同时不要写DOM，否则会再次触发MutationObserver形成循环。
        if (existing) { if (existing.textContent !== label) existing.textContent = label; return; }
        const b = document.createElement('button');
        b.id = 'xhs-collect-float';
        b.textContent = label;
        b.style.cssText = 'position:fixed;right:24px;bottom:96px;z-index:999999;'
            + 'padding:10px 18px;border:none;border-radius:24px;cursor:pointer;'
            + 'background:#ff2442;color:#fff;font-size:15px;font-weight:bold;'
            + 'box-shadow:0 3px 12px rgba(0,0,0,.3);font-family:sans-serif';
        b.onclick = doCollect;
        document.body.appendChild(b);
    }
})();
