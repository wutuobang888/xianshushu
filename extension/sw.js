// 后台 service worker：替 content script 发跨域 POST 到本地站点
// （content script 里的 fetch 受页面同源限制，走扩展权限就没限制）

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.type === 'DRAFT_KEY') {
        const id = sender.tab && sender.tab.id;
        sendResponse(id === undefined ? { ok: false }
            : { ok: true, key: `xsb_unpaid_draft_tab_${id}` });
        return;
    }
    if (!msg || msg.type !== 'UNPAID_REPORT') return;
    fetch('http://127.0.0.1:5000/api/kongfz/unpaid_report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(msg.payload)
    })
        .then(async r => {
            const j = await r.json();
            if (!r.ok || j.ok !== true) throw new Error(`登记失败：HTTP ${r.status}`);
            return j;
        })
        .then(j => sendResponse({ ok: true, body: j }))
        .catch(e => sendResponse({ ok: false, error: String(e) }));
    return true;   // 异步 sendResponse
});
