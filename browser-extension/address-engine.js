/* 复用已验证的结算 DOM 规则；内容脚本隔离执行，不加载远程代码。 */
globalThis.XssAddressEngine = function (target, expiresAt) {
    'use strict';
    const REMARK = '无需发票，请勿放任何店铺信息';
    document.addEventListener('click', event => {
        const button=event.target.closest('button,a,input[type=submit],.k-button');
        if (!button || !/提交订单/.test(button.innerText||button.value||'')) return;
        const match=findMatch().match;
        if (Date.now() >= expiresAt || !match?.classList.contains('selected')) {
            event.preventDefault();event.stopImmediatePropagation();
            alert('本单地址尚未核对通过或已过期，请从闲书书重新进入');
        }
    },true);
    let started=false;
    const timer=setInterval(async () => {
        if (Date.now() >= expiresAt) {
            clearInterval(timer);toast('本单地址已过期，请从闲书书重新进入',false);return;
        }
        if (started || location.hostname!=='order.kongfz.com' || !document.querySelector('.address-list')) return;
        started=true;
        try { await autoFill(); }
        catch (_) { fallback('未能完成本单地址核对'); }
        clearInterval(timer);
    },500);
    function waitEl(sel, ms) {
        return new Promise(resolve => {
            const t0 = Date.now();
            const tick = () => {
                if (document.querySelector(sel)) return resolve(true);
                if (Date.now() - t0 > ms) return resolve(false);
                setTimeout(tick, 300);
            };
            tick();
        });
    }

    const jsClick = el => el && el.dispatchEvent(
        new MouseEvent('click', { bubbles: true, cancelable: true }));
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const klog = m => { (window.__kfzLog = window.__kfzLog || []).push(m); };

    function toast(msg, ok = true) {
        const t = document.createElement('div');
        t.textContent = msg;
        t.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);'
            + `background:${ok ? '#1a7f37' : '#b22222'};color:#fff;padding:10px 20px;`
            + 'border-radius:8px;z-index:9999999;font-size:14px;box-shadow:0 2px 10px rgba(0,0,0,.3)';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 4000);
    }

    function setVal(el, v) {
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
        Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, v);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // ---------- 匹配：姓名、手机及完整地址一致才选择；不覆盖其他地址 ----------
    const norm = s => (s || '').replace(/\s+/g, '');
    const core = s => norm(s).replace(/(省|市|区|县|自治区|壮族|回族|维吾尔|特别行政区)/g, '');
    const addressKey = value => norm((value||'').normalize('NFKC')).replace(/[，,。:：;；()]/g,'')
        .replace(/广西壮族自治区/g,'广西').replace(/内蒙古自治区/g,'内蒙古')
        .replace(/宁夏回族自治区/g,'宁夏').replace(/新疆维吾尔自治区/g,'新疆').replace(/西藏自治区/g,'西藏');
    function findMatch() {
        const r=target.receiver;
        let match=null;
        for(const li of document.querySelectorAll('.address-list li')) {
            const name=li.querySelector('.address-info1 span:not(.phone)')?.textContent;
            const phone=li.querySelector('.address-info1 .phone')?.textContent;
            if(norm(name)!==norm(r.receiver_name)||norm(phone)!==norm(r.receiver_mobile)) continue;
            const address=li.querySelector('.address-info2')?.cloneNode(true);
            address?.querySelectorAll('.default').forEach(el=>el.remove());
            if(address && addressKey(address.textContent)===addressKey(fullAddress())) match ||= li;
        }
        return {match};
    }
    async function until(check,ms=8000) {
        const start=Date.now();
        while(Date.now()-start<ms) {if(check())return true;await sleep(200);}
        return false;
    }

    // ---------- 主流程（与 bots/kongfz_checkout.py 同套真实选择器） ----------
    async function autoFill() {
        const r=target.receiver;
        let {match}=findMatch();
        if(!match) {
            const add=[...document.querySelectorAll('button,a,.add-address,.address-add,.add-address-btn')]
                .find(el => el.offsetParent && /^[+＋]?\s*(新增|添加|使用新)(收货)?地址$/.test((el.textContent||'').trim()));
            if(!add) {fallback('未找到新增地址入口，请手动新增本单地址后从订单重新进入');return false;}
            jsClick(add);
            if(!await waitEl('.address-dialog .el-dialog textarea',5000)) {fallback('编辑地址弹窗未打开');return false;}
            const dialog=document.querySelector('.address-dialog .el-dialog');
            setVal(dialog.querySelector('textarea'),r.receiver_name+' '+r.receiver_mobile+' '+fullAddress());
            jsClick(dialog.querySelector('.btn-parse-w button'));
            await sleep(1600);
            if(!await ensureRegion()) {fallback('地区未识别正确，已停止保存');return false;}
            const fields=[...dialog.querySelectorAll('input')];
            const field=word=>fields.find(el=>(el.placeholder||'').includes(word))?.value||'';
            const region=addressKey(dialog.querySelector('.area-text')?.textContent);
            const detail=(r.town_name||'')+(r.address||'');
            if(field('收货人')!==r.receiver_name||field('手机')!==r.receiver_mobile
                ||addressKey(field('详细地址'))!==addressKey(detail)
                ||['prov_name','city_name','area_name'].some(k=>!region.includes(addressKey(r[k])))) {
                fallback('识别结果与本单地址不一致，已停止保存');return false;
            }
            const save=[...dialog.querySelectorAll('button')].find(el=>el.innerText.trim()==='保存并使用');
            if(!save) {fallback('没有找到保存按钮');return false;}
            if(Date.now() >= expiresAt) {fallback('本单地址信息已过期');return false;}
            jsClick(save);
            if(!await until(()=>findMatch().match && !dialog.offsetParent)) {
                fallback('保存后的地址未核对通过');return false;
            }
            match=findMatch().match;
        }
        if(Date.now() >= expiresAt) {fallback('本单地址信息已过期');return false;}
        jsClick(match.querySelector('.address-radio'));
        if(!await until(()=>match.classList.contains('selected'),3000)) {fallback('地址未选中');return false;}
        await fillRemark();
        toast('本单收货地址已填写并核对，请核对书籍和金额后自行提交付款');
        return true;
    }

    // 级联兜底：识别后地区没变对 → 点开 .area-text，逐 tab 点 省/市/区 的 <a>
    // 返回最终 .area-text 是否已含目标区县；false = 没改对，绝不能保存
    async function ensureRegion() {
        const r = target.receiver;
        const parts = [r.prov_name, r.city_name, r.area_name]
            .map(core).filter(c => c && c.length >= 2);
        const areaCore = parts[parts.length - 1];
        const txt = () => (document.querySelector(
            '.el-dialog .area-text, .k-area-select-wrap .area-text') || {}).innerText || '';
        klog(`region_now=${txt()}`);
        if (areaCore && txt().includes(areaCore)) { klog('region_ok'); return true; }
        jsClick(document.querySelector(
            '.el-dialog .k-area-select-wrap .area-text, .k-area-select-wrap .area-text'));
        const opened = await waitEl(
            '.k-area-select-wrap .area-wrap:not([style*="none"])', 3000);
        klog(`picker_open=${opened}`);
        if (!opened) return false;

        const clickLink = async part => {
            for (let i = 0; i < 10; i++) {
                const ls = [...document.querySelectorAll(
                    '.k-area-select-wrap .area-content a')].filter(a => a.offsetParent);
                const h = ls.find(a => norm(a.innerText).includes(part)
                    || (norm(a.innerText).length >= 2 && part.includes(norm(a.innerText))));
                if (h) { jsClick(h); return true; }
                await sleep(300);
            }
            return false;
        };
        // 逐 tab 激活，每个层级尝试剩余 parts 中任一匹配
        // （直辖市市级面板直接列区，city 部分会让位给 area 部分）
        const tabs = ['.tab-prov', '.tab-city', '.tab-area'];
        let pending = parts.slice();
        for (let i = 0; i < 3 && pending.length; i++) {
            jsClick(document.querySelector(
                `.k-area-select-wrap ${tabs[i]}`));       // 先激活对应层级
            await sleep(400);
            let hit = false, used = '';
            for (const part of pending.slice()) {
                if (await clickLink(part)) { hit = true; used = part; break; }
            }
            klog(`pick${i} ${used || pending[0]}=${hit}`);
            if (!hit) {
                if (i === 0) return false;                // 省都选不上 = 失败
                continue;                                 // 该层没有可点的就跳过
            }
            pending = pending.filter(p => p !== used);
            await sleep(500);
        }
        const ok = areaCore ? txt().includes(areaCore) : true;
        klog(`region_final=${txt()} ok=${ok}`);
        return ok;
    }

    function fullAddress() {
        const r = target.receiver;
        return [r.prov_name, r.city_name, r.area_name, r.town_name, r.address]
            .filter(Boolean).join('');
    }

    async function fillRemark() {
        const entry = document.querySelector('.remark-entry');
        if (!entry) return;
        jsClick(entry);
        if (!await waitEl('.remark-popup textarea, .remark-input textarea, .remark-input input', 4000)) return;
        setVal(document.querySelector(
            '.remark-popup textarea, .remark-input textarea, .remark-input input'), REMARK);
        // 必须点确认按钮提交，否则弹层一关备注就丢
        const okBtn = [...document.querySelectorAll('.remark-actions button')]
            .find(b => /确|保存|完成|提交/.test(b.innerText))
            || document.querySelector('.remark-actions .k-button--primary');
        if (okBtn) { jsClick(okBtn); klog('remark_done'); }
    }

    function fallback(why) {
        toast(why + '；已停止自动填写，请人工核对本单地址', false);
    }
};
