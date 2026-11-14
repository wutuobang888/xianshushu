const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/publish.html'), 'utf8');
const source = template.slice(template.indexOf('async function doSearch()'), template.indexOf('function renderCard('));

async function search(response, failure) {
    const tip = {textContent: ''};
    const cards = [];
    const sandbox = {
        kw: {value: '耶路撒冷三千年'}, tip,
        document: {getElementById: id => id === 'results'
            ? {innerHTML: '', appendChild: card => cards.push(card)}
            : {classList: {remove() {}}}},
        renderCard: book => book,
        fetch: async () => {
            if (failure) throw failure;
            return {ok: response.status < 400, json: async () => response.body};
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(source, sandbox);
    await sandbox.doSearch();
    return {message: tip.textContent, cards};
}

test('后台失败且没有items时保留原始错误', async () => {
    const result = await search({status: 500, body: {ok: false, error: '浏览器启动失败'}});
    assert.equal(result.message, '搜索失败：浏览器启动失败');
    assert.equal(result.cards.length, 0);
});

test('成功时显示并渲染搜索结果', async () => {
    const book = {book_title: '耶路撒冷三千年'};
    const result = await search({status: 200, body: {ok: true, items: [book]}});
    assert.equal(result.message, '找到 1 条（按孔夫子条目销量排序）');
    assert.deepEqual(result.cards, [book]);
});

test('空结果提示缩短书名或使用ISBN', async () => {
    const result = await search({status: 200, body: {ok: true, items: []}});
    assert.match(result.message, /没有找到.*ISBN/);
});

test('成功响应缺少结果数组时提示格式异常', async () => {
    const result = await search({status: 200, body: {ok: true}});
    assert.match(result.message, /结果格式异常/);
    assert.equal(result.cards.length, 0);
});

test('网络错误显示失败原因', async () => {
    const result = await search(null, new Error('连接断开'));
    assert.match(result.message, /搜索失败.*连接断开/);
});
