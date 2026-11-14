// 浏览器扩展离线回归：模拟 Chrome 消息、存储和孔夫子页面，不访问真实网站。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const content = fs.readFileSync(path.join(root, 'extension/content.js'), 'utf8');
const worker = fs.readFileSync(path.join(root, 'extension/sw.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));

function page(storage = {}, bodyText = '实付款：6.00', defer = false) {
    const timers = [], reports = [], responses = [];
    let click, sequence = 0;
    const key = 'xsb_unpaid_draft_tab_1';
    const context = {
        window: {}, location: { href: 'https://order.kongfz.com/mock' },
        Date, crypto: { randomUUID: () => `draft-${++sequence}` },
        setTimeout: fn => timers.push(fn),
        document: {
            body: { innerText: bodyText, appendChild() {} }, title: '测试书',
            createElement: () => ({ style: {}, remove() {} }),
            addEventListener: (_, fn) => { click = fn; },
            querySelectorAll: selector => selector === '.address-list li'
                ? [{ innerText: '测试买家 13800000000 测试地址', className: 'active',
                    querySelector: () => ({ checked: true }) }]
                : [{ offsetParent: true, innerText: '测试书' }],
        },
        chrome: {
            storage: { local: {
                get: (k, cb) => cb(storage[k] ? { [k]: clone(storage[k]) } : {}),
                set: (value, cb) => { Object.assign(storage, clone(value)); if (cb) cb(); },
            } },
            runtime: { sendMessage: (message, cb) => {
                if (message.type === 'DRAFT_KEY') return cb({ ok: true, key });
                reports.push(clone(message.payload));
                const respond = () => cb({ ok: true, body: { ok: true, matched: [1] } });
                if (defer) responses.push(respond); else respond();
            } },
        },
    };
    vm.runInNewContext(content, context);
    return { storage, key, reports, responses, timers,
        submit: () => click({ composedPath: () => [{ innerText: '提交订单' }] }) };
}

test('首次提示成功仍允许跳转后补抓单号', () => {
    const first = page();
    first.submit();
    assert.equal(first.storage[first.key].done, false);
    assert.equal(first.reports[0].kf_no, '');
    const next = page(first.storage, '订单号：900000001');
    next.timers.shift()();
    assert.equal(next.reports[0].kf_no, '900000001');
    assert.equal(next.storage[next.key].done, true);
});

test('ISO时间草稿超过24小时不会补报', () => {
    const p = page({ xsb_unpaid_draft_tab_1: {
        ts: new Date(Date.now() - 25 * 3600 * 1000).toISOString(), done: false,
    } });
    assert.equal(p.timers.length, 0);
    assert.equal(p.reports.length, 0);
});

test('损坏的草稿时间不会补报', () => {
    const p = page({ xsb_unpaid_draft_tab_1: { ts: 'bad-date', done: false } });
    assert.equal(p.timers.length, 0);
});

test('旧请求返回不能覆盖新采购草稿', () => {
    const p = page({}, '实付款：6.00', true);
    p.submit();
    p.submit();
    p.responses[0]();
    assert.equal(p.storage[p.key].draft_id, 'draft-2');
});

function background(fetchImpl) {
    let listener;
    vm.runInNewContext(worker, {
        chrome: { runtime: { onMessage: { addListener: fn => { listener = fn; } } } },
        fetch: fetchImpl,
    });
    return (msg, tabId = 1) => new Promise(resolve => listener(msg, { tab: { id: tabId } }, resolve));
}

test('不同标签页使用不同草稿键', async () => {
    const send = background(() => { throw new Error('不应请求网络'); });
    const a = await send({ type: 'DRAFT_KEY' }, 1);
    const b = await send({ type: 'DRAFT_KEY' }, 2);
    assert.notEqual(a.key, b.key);
});

test('HTTP错误不报告成功', async () => {
    const send = background(async () => ({ ok: false, status: 500,
        json: async () => ({ ok: true }) }));
    assert.equal((await send({ type: 'UNPAID_REPORT', payload: {} })).ok, false);
});

test('业务错误不报告成功', async () => {
    const send = background(async () => ({ ok: true, status: 200,
        json: async () => ({ ok: false }) }));
    assert.equal((await send({ type: 'UNPAID_REPORT', payload: {} })).ok, false);
});

test('HTTP及业务结果成功才报告成功', async () => {
    const send = background(async () => ({ ok: true, status: 200,
        json: async () => ({ ok: true, matched: [1] }) }));
    assert.equal((await send({ type: 'UNPAID_REPORT', payload: {} })).ok, true);
});
