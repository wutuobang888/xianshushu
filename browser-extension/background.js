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
    if (details.sourceFrameId !== 0 || !policy.official(details.url)) return;
    const task = (async () => {
        const source = await chrome.tabs.get(details.sourceTabId);
        if (!policy.official(source.url || source.pendingUrl)) return;
        const record = await contextFor(details.sourceTabId);
        if (record) await chrome.storage.session.set({[tabKey(details.tabId)]:record.nonce});
    })().catch(() => {});
    inheriting.set(details.tabId, task);
    task.finally(() => inheriting.delete(details.tabId));
    return task;
}, {url:['book.kongfz.com','item.kongfz.com','search.kongfz.com','order.kongfz.com'].map(hostEquals => ({hostEquals,schemes:['https']}))});

async function handle(message, sender) {
    if (!sender.tab || sender.frameId !== 0) throw new Error('不支持此页面');
    const {siteOrigin} = await chrome.storage.local.get('siteOrigin');
    if (message?.type === 'OPEN_SOURCE') {
        if (!policy.site(sender.url, siteOrigin) || !['/', '/orders'].includes(new URL(sender.url).pathname)
                || !policy.official(message.url) || !policy.target(message.target))
            throw new Error('来源或订单信息无效');
        await cleanup();
        const records = await chrome.storage.session.get(null);
        if (Object.keys(records).filter(key => key.startsWith('order:')).length >= 64)
            throw new Error('待处理订单过多，请先完成或等待缓存过期');
        const nonce = crypto.randomUUID();
        await chrome.storage.session.set({[recordKey(nonce)]: {target:message.target, expiresAt:Date.now() + policy.TTL}});
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
        if (!policy.official(sender.url)) throw new Error('不支持此页面');
        await inheriting.get(sender.tab.id);
        let record = await contextFor(sender.tab.id);
        // 内容脚本和导航事件可能同时到达，只等待自己的标签映射，不回退其他订单。
        for (let attempt = 0; !record && attempt < 4; attempt++) {
            await new Promise(resolve => setTimeout(resolve, 50));
            await inheriting.get(sender.tab.id);
            record = await contextFor(sender.tab.id);
        }
        return record ? {ok:true,target:record.target,expiresAt:record.expiresAt} : {ok:false};
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
