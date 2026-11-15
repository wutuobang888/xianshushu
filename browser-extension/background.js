/* MV3 后台：地址只存会话内存，按标签页绑定；不读浏览器 Cookie。 */
importScripts('policy.js');
const policy = globalThis.XssPolicy;
const recordKey = nonce => 'order:' + nonce;
const tabKey = id => 'tab:' + id;
const inheriting = new Map();

async function cleanup() {
    const values = await chrome.storage.session.get(null);
    const expired = Object.keys(values).filter(key => key.startsWith('order:') && !policy.active(values[key]));
    const valid = new Set(Object.keys(values).filter(key => key.startsWith('order:') && !expired.includes(key)).map(key => key.slice(6)));
    expired.push(...Object.keys(values).filter(key => key.startsWith('tab:') && !valid.has(values[key])));
    if (expired.length) await chrome.storage.session.remove(expired);
}

async function contextFor(tabId) {
    const values = await chrome.storage.session.get(tabKey(tabId));
    const nonce = values[tabKey(tabId)];
    if (nonce) {
        const records = await chrome.storage.session.get(recordKey(nonce));
        const record = records[recordKey(nonce)];
        return policy.active(record) ? {nonce, ...record} : null;
    }
    return null;
}

// 使用浏览器明确报告的导航发起标签，而不是可能受当前前台标签影响的 openerTabId。
chrome.webNavigation.onCreatedNavigationTarget.addListener(details => {
    if (details.sourceFrameId !== 0 || !policy.mediaSite(details.url)) return;
    const task = (async () => {
        const source = await chrome.tabs.get(details.sourceTabId);
        if (!policy.mediaSite(source.url || source.pendingUrl)) return;
        const record = await contextFor(details.sourceTabId);
        if (record) await chrome.storage.session.set({[tabKey(details.tabId)]:record.nonce});
    })().catch(() => {});
    inheriting.set(details.tabId, task);
    task.finally(() => inheriting.delete(details.tabId));
    return task;
}, {url:['book.kongfz.com','item.kongfz.com','search.kongfz.com','order.kongfz.com','shop.kongfz.com','www.xiaohongshu.com'].map(hostEquals => ({hostEquals,schemes:['https']}))});

async function handle(message, sender) {
    if (!sender.tab || sender.frameId !== 0) throw new Error('不支持此页面');
    const {siteOrigin} = await chrome.storage.local.get('siteOrigin');
    if (['OPEN_SOURCE','OPEN_COLLECT'].includes(message?.type)) {
        const collect = message.type === 'OPEN_COLLECT';
        const paths = collect ? ['/publish','/shop_products'] : ['/', '/orders'];
        if (!policy.site(sender.url, siteOrigin) || !paths.includes(new URL(sender.url).pathname)
                || !(collect ? policy.mediaSite(message.url) && policy.collection(message.target)
                             : policy.official(message.url) && policy.target(message.target)))
            throw new Error('来源或订单信息无效');
        await cleanup();
        const records = await chrome.storage.session.get(null);
        if (Object.keys(records).filter(key => key.startsWith('order:')).length >= 64)
            throw new Error('待处理订单过多，请先完成或等待缓存过期');
        const nonce = crypto.randomUUID();
        await chrome.storage.session.set({[recordKey(nonce)]: {
            kind:collect ? 'collect' : 'order', target:message.target,
            siteTabId:sender.tab.id, siteOrigin:new URL(sender.url).origin,
            expiresAt:Date.now() + (collect ? policy.COLLECT_TTL : policy.TTL)
        }});
        const tab = await chrome.tabs.create({url:'about:blank', openerTabId:sender.tab.id});
        try {
            await chrome.storage.session.set({[tabKey(tab.id)]:nonce});
            await chrome.tabs.update(tab.id, {url:message.url});
        } catch (error) {
            await chrome.storage.session.remove([recordKey(nonce),tabKey(tab.id)]);
            throw new Error('未能打开采购标签页，请重试');
        }
        return {ok:true};
    }
    if (message?.type === 'GET_CONTEXT') {
        if (!policy.mediaSite(sender.url)) throw new Error('不支持此页面');
        await inheriting.get(sender.tab.id);
        let record = await contextFor(sender.tab.id);
        // 内容脚本和导航事件可能同时到达，只等待自己的标签映射，不回退其他订单。
        for (let attempt = 0; !record && attempt < 4; attempt++) {
            await new Promise(resolve => setTimeout(resolve, 50));
            await inheriting.get(sender.tab.id);
            record = await contextFor(sender.tab.id);
        }
        return record ? {ok:true,kind:record.kind || 'order',target:record.target,
            submitted:!!record.submitted,expiresAt:record.expiresAt} : {ok:false};
    }
    if (['COLLECT_IMAGES','UNPAID_REPORT'].includes(message?.type)) {
        if (!policy.mediaSite(sender.url)) throw new Error('不支持此页面');
        const record = await contextFor(sender.tab.id);
        if (!record) throw new Error('关联已过期，请从闲书书重新进入');
        let path, body;
        if (message.type === 'COLLECT_IMAGES') {
            if (record.kind !== 'collect' || !Array.isArray(message.payload?.urls)
                    || !message.payload.urls.length || message.payload.urls.length > 100
                    || !message.payload.urls.every(url=>typeof url==='string' && url.length<=2048))
                throw new Error('收图目标或图片列表无效');
            path='/api/images/collect';
            body={...message.payload, entry_url:record.target.entry_url,
                note_url:sender.url.split(/[?#]/)[0],
                source:new URL(sender.url).hostname==='www.xiaohongshu.com' ? 'xhs' : 'kongfz'};
        } else {
            if ((record.kind || 'order') !== 'order' || new URL(sender.url).hostname!=='order.kongfz.com')
                throw new Error('缺少本单采购关联');
            const r=record.target.receiver;
            path='/api/kongfz/unpaid_report';
            body={phone:r.receiver_mobile,name:r.receiver_name,
                addr:[r.prov_name,r.city_name,r.area_name,r.town_name,r.address].filter(Boolean).join(''),
                title:record.target.product_title || '', order_id:record.target.order_id,
                amount:typeof message.payload?.amount==='number' && Number.isFinite(message.payload.amount) ? message.payload.amount : null,
                kf_no:String(message.payload?.kf_no || '').replace(/\D/g,'').slice(0,32),ts:new Date().toISOString()};
            await chrome.storage.session.set({[recordKey(record.nonce)]:{...record,submitted:true}});
        }
        const parent=await chrome.tabs.get(record.siteTabId);
        if (!policy.site(parent.url,siteOrigin) || new URL(parent.url).origin!==record.siteOrigin)
            throw new Error('请保留原闲书书页面并刷新后重试');
        const result=await chrome.tabs.sendMessage(record.siteTabId,
            {type:'SITE_REQUEST',path,body,siteOrigin:record.siteOrigin});
        if (!result?.ok) throw new Error(result?.error || '回传失败，请保持闲书书来源页面打开');
        return result;
    }
    throw new Error('不支持此消息');
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
    handle(message, sender).then(respond).catch(error => respond({ok:false,error:error.message}));
    return true;
});
chrome.tabs.onRemoved.addListener(id => chrome.storage.session.remove(tabKey(id)));
chrome.alarms.onAlarm.addListener(async alarm => { if (alarm.name === 'expire-orders') await cleanup(); });
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create('expire-orders', {periodInMinutes:1}));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create('expire-orders', {periodInMinutes:1}));
