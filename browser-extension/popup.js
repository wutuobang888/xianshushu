'use strict';
const status = document.getElementById('status');
chrome.storage.local.get('siteOrigin').then(({siteOrigin}) => {document.getElementById('site').value = siteOrigin || '';});
document.getElementById('open').addEventListener('click',() => chrome.tabs.create({url:'http://127.0.0.1:5000/orders'}));
document.getElementById('save').addEventListener('click',async () => {
    try {
        const url = new URL(document.getElementById('site').value);
        if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash)
            throw new Error('请输入不带账号、参数的 HTTPS 网站地址');
        const granted = await chrome.permissions.request({origins:[url.origin+'/*']});
        if (!granted) throw new Error('未授予网站访问权限');
        const {siteOrigin: previous} = await chrome.storage.local.get('siteOrigin');
        await chrome.scripting.unregisterContentScripts({ids:['xss-site']}).catch(() => {});
        await chrome.scripting.registerContentScripts([{
            id:'xss-site',matches:[url.origin+'/*'],js:['policy.js','address-engine.js','content.js'],runAt:'document_start'
        }]);
        await chrome.storage.local.set({siteOrigin:url.origin});
        if (previous && previous !== url.origin)
            await chrome.permissions.remove({origins:[previous+'/*']});
        status.textContent = '已连接，请刷新该网站订单页。';
    } catch (error) { status.textContent = error.message || '连接失败，请重试'; }
});
