// ==UserScript==
// @name         闲书书-孔夫子下单上报
// @namespace    xianshou.kfz.unpaid
// @version      1.0
// @description  在孔夫子点"提交订单"（未付款）瞬间上报本地站点，订单页秒出"已下单待付款"灰标；跳到成功/付款页时补抓订单号再报一次
// @match        *://*.kongfz.com/*
// @match        *://kongfz.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      127.0.0.1
// @connect      localhost
// @updateURL    http://127.0.0.1:5000/scripts/kfz_unpaid_report.user.js
// @downloadURL  http://127.0.0.1:5000/scripts/kfz_unpaid_report.user.js
// @run-at       document-idle
// ==/UserScript==
(function () {
    'use strict';
    const API = 'http://127.0.0.1:5000';
    const DRAFT = 'kfz_unpaid_draft';  // {name,phone,addr,title,amount,kf_no,ts,done}
    const TTL = 24 * 3600 * 1000;
    const norm = s => (s || '').replace(/\s+/g, '');

    // ---- 任意孔夫子页：有待上报草稿 → 等页面稳一稳，补抓单号后 POST ----
    // （提交订单会跳成功/收银台页，单号一般在那个页面才出现）
    const draft = GM_getValue(DRAFT, null);
    if (draft && !draft.done && Date.now() - draft.ts < TTL) {
        setTimeout(() => {
            const no = extractOrderNo();
            if (no) draft.kf_no = no;
            report(draft);
        }, 1500);
    }

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
        GM_xmlhttpRequest({
            method: 'POST', url: API + '/api/kongfz/unpaid_report',
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify(d), timeout: 8000,
            onload: r => {
                let j = null;
                try { j = JSON.parse(r.responseText); } catch (e) {}
                if (!j || !j.ok) return;
                GM_setValue(DRAFT, { ...d, done: true });
                toast(j.matched && j.matched.length
                    ? `已通知本站：待付款单已登记（匹配订单 #${j.matched.join(',')}）`
                    : '已通知本站，但没匹配到待采购订单（手机号对不上？）',
                    !!(j.matched && j.matched.length));
            },
            onerror: () => {}, ontimeout: () => {}
        });
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

    // ---- 下单页：监听"提交订单"点击，抓收货人/书名/实付存草稿并立刻上报 ----
    const isCheckout = /order-views\/create|order-views\/.*order/i.test(location.href);
    if (!isCheckout) return;

    document.addEventListener('click', e => {
        const path = e.composedPath ? e.composedPath() : [e.target];
        const hit = path.find(el => el && el.innerText !== undefined
            && (el.innerText || '').trim() === '提交订单');
        if (!hit) return;
        const d = collect();
        if (!d.phone) return;
        // 本地时间 ISO（后端 kongfz_last_scan 用的也是本地时间，不能给 UTC）
        d.ts = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
            .toISOString().slice(0, 19);
        GM_setValue(DRAFT, { ...d, done: false });
        report(d);   // 立即发一份；跳转后页面上的草稿逻辑会补抓单号再发
    }, true);

    function collect() {
        // 选中的地址行（勾中 radio/active 优先，否则第一条含手机号的）
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
        } else {   // 兜底：取页面上最后一个 ¥ 金额（一般是底部合计）
            const all = bt.match(/¥\s*\d+\.?\d{0,2}/g);
            if (all && all.length)
                amount = parseFloat(all[all.length - 1].replace(/[^\d.]/g, ''));
        }
        return { name, phone, addr: t.slice(0, 120),
                 title: (title || '').trim().slice(0, 80), amount, kf_no: '' };
    }
})();
