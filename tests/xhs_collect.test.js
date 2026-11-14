// 收图脚本离线回归：模拟标签页、笔记DOM和油猴请求，不访问小红书。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../scripts/xhs_collect.user.js'), 'utf8');
function tab(target, storage = {}) {
    const elements = {}, requests = [];
    const context = {
        URLSearchParams,
        location: {hash: target ? '#xs_collect&entry_url=' + encodeURIComponent(target) + '&book_title=测试书' : '',
                   href: 'https://www.xiaohongshu.com/explore/offline'},
        window: {addEventListener() {}},
        sessionStorage: {getItem: key => storage[key] || null, setItem: (key, value) => storage[key] = value},
        MutationObserver: class {observe() {}}, setTimeout() {}, clearTimeout() {},
        document: {title: '离线笔记',
            body: {appendChild: element => elements[element.id] = element},
            getElementById: id => elements[id] || null,
            createElement: () => ({style: {}}),
            querySelector: selector => selector.startsWith('#detail-title') ? {textContent: '笔记标题'} : null,
            querySelectorAll: () => [{querySelectorAll: () => [{src: 'https://test.xhscdn.com/image', naturalWidth: 1000}]}]},
        GM_xmlhttpRequest: request => requests.push(request),
    };
    vm.runInNewContext(source, context);
    return {context, storage, requests, elements, collect: () => elements['xhs-collect-float'].onclick()};
}
test('两个标签页分别提交自己的目标', () => {
    const a = tab('book-a'), b = tab('book-b');
    a.collect(); b.collect();
    assert.equal(JSON.parse(a.requests[0].data).entry_url, 'book-a');
    assert.equal(JSON.parse(b.requests[0].data).entry_url, 'book-b');
});
test('刷新或跳笔记保留本标签页目标', () => {
    const first = tab('book-a');
    const second = tab(null, first.storage);
    second.collect();
    assert.equal(JSON.parse(second.requests[0].data).entry_url, 'book-a');
});
test('新链接覆盖同标签页的旧目标', () => {
    const first = tab('book-a');
    const second = tab('book-b', first.storage);
    second.collect();
    assert.equal(JSON.parse(second.requests[0].data).entry_url, 'book-b');
});
test('没有明确目标不会借用后台全局目标', () => {
    const page = tab(null, {xs_collect: '1'});
    assert.equal(page.requests.length, 0);
    assert.equal(page.elements['xhs-collect-float'], undefined);
});
test('返回目标不一致或零张图片不提示成功', () => {
    for (const data of [{ok: true, count: 1, target: 'book-b'}, {ok: true, count: 0, target: 'book-a'}]) {
        const page = tab('book-a'); page.collect();
        page.requests[0].onload({status: 200, responseText: JSON.stringify(data)});
        assert.equal(page.elements['xhs-collect-toast'].textContent, '收图失败');
    }
});
test('成功提示明确展示书籍目标', () => {
    const page = tab('book-a'); page.collect();
    page.requests[0].onload({status: 200, responseText: JSON.stringify({ok: true, count: 4, target: 'book-a', title: '书A'})});
    assert.match(page.elements['xhs-collect-toast'].textContent, /4 张图到《书A》/);
});
