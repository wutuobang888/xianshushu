// 闲书书-孔夫子下单上报 content script
// 1) 任意孔夫子页：有未上报草稿 → 补抓订单号再报（提交后跳成功/收银页才出单号）
// 2) 监听"提交订单"点击：抓收货人/书名/实付 → 存草稿 + 立即上报
(function () {
    'use strict';
    if (window.__xsbReportLoaded) return;
    window.__xsbReportLoaded = true;

    // 后台按标签页分配草稿键，避免同时下单互相覆盖收货信息。
    chrome.runtime.sendMessage({ type: 'DRAFT_KEY' }, resp => {
        if (resp && resp.ok && resp.key) init(resp.key);
    });

    function init(KEY) {
    const TTL = 24 * 3600 * 1000;
    const norm = s => (s || '').replace(/\s+/g, '');
    const localIso = () =>
        new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
            .toISOString().slice(0, 19);

    // ---- 草稿补报 ----
    chrome.storage.local.get(KEY, res => {
        const d = res[KEY];
        if (!d || d.done) return;
        const stamp = d.created_ms || Date.parse(d.ts);
        if (!Number.isFinite(stamp) || Date.now() - stamp > TTL) return;
        setTimeout(() => {
            const no = extractOrderNo();
            if (no) d.kf_no = no;
            report(d);
        }, 1500);
    });

    function extractOrderNo() {
        const m = location.href.match(
            /[?&](?:order_?no|order_?id|orderNo|orderId|oid)=(\d{7,})/i);
        if (m) return m[1];
        const t = (document.body && document.body.innerText) || '';
        const m2 = t.match(/订单\s*号[：: ]*\s*(\d{7,})/);
        if (m2) return m2[1];
        return '';
    }

    function report(d) {
        try {
            chrome.runtime.sendMessage(
                { type: 'UNPAID_REPORT', payload: d }, resp => {
                    if (!resp || !resp.ok) {
                        toast('待付款登记未完成，请检查网站是否运行', false);
                        return;
                    }
                    const j = resp.body || {};
                    d.done = !!d.kf_no && !!(j.matched && j.matched.length);
                    chrome.storage.local.get(KEY, current => {
                        // 旧请求返回时，不能覆盖本标签页刚创建的新采购草稿。
                        if (current[KEY] && current[KEY].draft_id === d.draft_id)
                            chrome.storage.local.set({ [KEY]: d });
                    });
                    const n = j.matched && j.matched.length;
                    toast(n ? `已通知本站：待付款单已登记（匹配订单 #${j.matched.join(',')}）`
                            : '已通知本站，但没匹配到待采购订单（手机号对不上？）', !!n);
                });
        } catch (e) { /* 扩展重载时静默 */ }
    }

    function toast(msg, ok = true) {
        const t = document.createElement('div');
        t.textContent = msg;
        t.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);'
            + `background:${ok ? '#1a7f37' : '#b22222'};color:#fff;padding:10px 20px;`
            + 'border-radius:8px;z-index:9999999;font-size:14px;box-shadow:0 2px 10px rgba(0,0,0,.3)';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    // ---- "提交订单"点击监听（不限下单页：按钮文案就是唯一判据） ----
    document.addEventListener('click', e => {
        const path = e.composedPath ? e.composedPath() : [e.target];
        const hit = path.find(el => el && el.innerText !== undefined
            && (el.innerText || '').trim() === '提交订单');
        if (!hit) return;
        const d = collect();
        if (!d.phone) return;
        d.ts = localIso();
        d.created_ms = Date.now();
        d.draft_id = crypto.randomUUID();
        d.done = false;
        chrome.storage.local.set({ [KEY]: d }, () => report(d));
    }, true);

    function collect() {
        const lis = [...document.querySelectorAll('.address-list li')];
        const li = lis.find(l =>
            (l.querySelector('.address-radio input') || {}).checked
            || /active|cur|selected|\bon\b/i.test(l.className))
            || lis.find(l => /1\d{10}/.test(norm(l.innerText))) || lis[0];
        const t = norm(li ? li.innerText : '');
        const pm = t.match(/1\d{10}|\d{7,8}/);
        const phone = pm ? pm[0] : '';
        const name = pm ? t.slice(0, pm.index)
            .replace(/^(收货人|收件人|姓名)/, '').slice(0, 10) : '';
        const title = ([...document.querySelectorAll(
            'a[href*="book.kongfz.com"],.goods-name,.item-title,.book-name,.goods-title')]
            .find(e => e.offsetParent && (e.innerText || '').trim()) || {})
            .innerText || document.title;
        const bt = (document.body && document.body.innerText) || '';
        const am = bt.match(/实\s*付\s*款[^\d]{0,15}(\d+\.?\d{0,2})/)
            || bt.match(/合\s*计[^\d]{0,15}(\d+\.?\d{0,2})/);
        let amount = null;
        if (am) {
            amount = parseFloat(am[1]);
        } else {
            const all = bt.match(/¥\s*\d+\.?\d{0,2}/g);
            if (all && all.length)
                amount = parseFloat(all[all.length - 1].replace(/[^\d.]/g, ''));
        }
        return { name, phone, addr: t.slice(0, 120),
                 title: (title || '').trim().slice(0, 80), amount, kf_no: '' };
    }
    }
})();
