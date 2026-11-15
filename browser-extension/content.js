/* 隔离世界内容脚本：只通过本站页面的会话和 CSRF 读取本单地址。 */
(function () {
    'use strict';
    if (globalThis.__xssNativeAddressHelper) return;
    globalThis.__xssNativeAddressHelper = true;
    const policy = globalThis.XssPolicy;
    const ready = fn => document.readyState === 'loading'
        ? document.addEventListener('DOMContentLoaded', fn, {once:true}) : fn();
    if (policy.official(location.href)) {
        chrome.runtime.sendMessage({type:'GET_CONTEXT'}).then(result => {
            if (result.ok) ready(() => globalThis.XssAddressEngine(result.target, result.expiresAt));
        }).catch(() => {});
        return;
    }
    let pending = false;
    // 在页面自己的采购监听器之前注册；不使用页面的“最后一单”状态。
    const openSource = async event => {
        const link = event.target.closest('a[data-xss-order],a[data-xss-native-source]');
        if (!link || ![0,1].includes(event.button)) return;
        const source = policy.source(link.dataset.sourceUrl || link.href);
        if (!source) return;
        event.preventDefault(); event.stopImmediatePropagation();
        if (pending) { alert('上一笔订单正在准备，请稍候'); return; }
        pending = true;
        try {
            const csrf = document.querySelector('script[data-csrf]')?.dataset.csrf;
            const id = Number(link.dataset.xssOrder || link.dataset.xssNativeSource);
            if (!csrf || !Number.isSafeInteger(id) || id < 1) throw new Error('请刷新订单页后重试');
            const response = await fetch(location.origin + '/orders/' + id + '/browser_target', {
                method:'POST', credentials:'same-origin', headers:{'X-CSRF-Token':csrf}, signal:AbortSignal.timeout(20000)
            });
            const result = await response.json();
            if (!response.ok || !result.ok || result.target?.order_id !== id)
                throw new Error(result.error || '未能核对本单收货信息');
            const opened = await chrome.runtime.sendMessage({type:'OPEN_SOURCE',url:source,target:result.target});
            if (!opened.ok) throw new Error(opened.error || '未能打开采购页');
        } catch (error) { alert(error.message || '地址助手未能准备本单信息'); }
        finally { pending = false; }
    };
    document.addEventListener('click',openSource,true);
    document.addEventListener('auxclick',openSource,true);
    ready(async () => {
        const {siteOrigin} = await chrome.storage.local.get('siteOrigin');
        if (!policy.site(location.href,siteOrigin)) return;
        document.documentElement.dataset.xssAddressHelper = 'extension-1.0.0';
        const indicator = document.createElement('p');
        indicator.id = 'xss-extension-status';
        indicator.textContent = '闲书书扩展已启用 · 每笔订单独立核对地址，不自动提交或付款';
        indicator.style.cssText = 'color:#1a7f37;padding:8px 16px';
        (document.querySelector('main') || document.body).prepend(indicator);
    });
})();
