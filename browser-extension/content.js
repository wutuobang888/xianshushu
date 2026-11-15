/* 隔离世界内容脚本：只通过本站页面的会话和 CSRF 读取本单地址。 */
(function () {
    'use strict';
    if (globalThis.__xssNativeAddressHelper) return;
    globalThis.__xssNativeAddressHelper = true;
    const policy = globalThis.XssPolicy;
    const ready = fn => document.readyState === 'loading'
        ? document.addEventListener('DOMContentLoaded', fn, {once:true}) : fn();
    if (policy.mediaSite(location.href)) {
        chrome.runtime.sendMessage({type:'GET_CONTEXT'}).then(result => {
            if (!result.ok) return;
            ready(() => {
                if (result.kind==='collect') return globalThis.XssImageCollector(result.target);
                globalThis.XssAddressEngine(result.target, result.expiresAt);
                if (location.hostname!=='order.kongfz.com') return;
                const report = async () => {
                    const text=document.body.innerText || '';
                    const amount=text.match(/(?:实\s*付\s*款|合\s*计)[^\d]{0,15}(\d+\.?\d{0,2})/);
                    const no=location.href.match(/[?&](?:order_?no|order_?id|orderNo|orderId|oid)=(\d{7,})/i)
                        || text.match(/订单\s*号[：: ]*\s*(\d{7,})/);
                    const response=await chrome.runtime.sendMessage({type:'UNPAID_REPORT',payload:{
                        amount:amount ? Number(amount[1]) : null,kf_no:no ? no[1] : ''}});
                    if (!response.ok) console.warn('闲书书：待付款提示回传未完成，请保持来源页面打开');
                };
                document.addEventListener('click',event => {
                    const button=event.target.closest('button,a,input[type=submit],.k-button');
                    if (button && (button.innerText || button.value || '').trim()==='提交订单') report().catch(()=>{});
                },true);
                if (result.submitted) report().catch(()=>{});
            });
        }).catch(() => {});
        return;
    }
    chrome.runtime.onMessage.addListener((message,sender,respond) => {
        if (sender.id!==chrome.runtime.id || message?.type!=='SITE_REQUEST') return;
        if (message.siteOrigin!==location.origin || !['/api/images/collect','/api/kongfz/unpaid_report'].includes(message.path)) {
            respond({ok:false,error:'回传来源无效'});return;
        }
        (async () => {
            const csrf=document.querySelector('script[data-csrf]')?.dataset.csrf;
            if (!csrf) throw new Error('请刷新闲书书页面后重试');
            const response=await fetch(location.origin+message.path,{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(message.body),signal:AbortSignal.timeout(30000)});
            const result=await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || '回传失败');
            return result;
        })().then(respond).catch(error=>respond({ok:false,error:error.message}));
        return true;
    });
    window.addEventListener('message',event => {
        if (event.source!==window || event.origin!==location.origin || event.data?.channel!=='xss-extension'
                || event.data.type!=='OPEN_COLLECT') return;
        chrome.runtime.sendMessage({type:'OPEN_COLLECT',url:event.data.url,target:event.data.target})
            .then(result=>{if(!result.ok)alert(result.error || '收图页面未能打开');}).catch(()=>alert('请重新加载扩展并刷新页面'));
    });
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
        document.documentElement.dataset.xssAddressHelper = 'extension-1.1.0';
        const indicator = document.createElement('p');
        indicator.id = 'xss-extension-status';
        indicator.textContent = '闲书书扩展已启用 · 地址、收图、待付款提示；不自动提交或付款';
        indicator.style.cssText = 'color:#1a7f37;padding:8px 16px';
        (document.querySelector('main') || document.body).prepend(indicator);
    });
})();
