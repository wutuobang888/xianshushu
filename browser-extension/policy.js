/* 消息和网址校验在后台、内容脚本及离线测试之间共用。 */
(function (root) {
    'use strict';
    const TTL = 10 * 60 * 1000;
    const officialHosts = ['book.kongfz.com', 'item.kongfz.com', 'search.kongfz.com', 'order.kongfz.com'];
    function parse(value) { try { return new URL(value); } catch (_) { return null; } }
    function official(value) {
        const url = parse(value);
        return !!url && url.protocol === 'https:' && !url.username && !url.password
            && !url.port && officialHosts.includes(url.hostname);
    }
    function source(value) {
        const url = parse(value);
        if (!url || !['http:', 'https:'].includes(url.protocol)) return null;
        url.protocol = 'https:';
        return official(url.href) ? url.href : null;
    }
    function site(value, configured) {
        const url = parse(value);
        return !!url && !url.username && !url.password && (
            ['http://127.0.0.1:5000', 'http://localhost:5000'].includes(url.origin)
            || (!!configured && url.origin === configured && url.protocol === 'https:'));
    }
    function target(value) {
        const receiver = value?.receiver;
        return Number.isSafeInteger(value?.order_id) && value.order_id > 0 && receiver
            && ['receiver_name','receiver_mobile','prov_name','city_name','area_name','address']
                .every(key => typeof receiver[key] === 'string' && receiver[key].trim() && receiver[key].length <= 512)
            && /^\d{7,20}$/.test(receiver.receiver_mobile);
    }
    function active(record, now = Date.now()) {
        return !!record && target(record.target) && Number.isFinite(record.expiresAt)
            && record.expiresAt > now && record.expiresAt <= now + TTL;
    }
    const policy = { TTL, official, source, site, target, active };
    root.XssPolicy = policy;
    if (typeof module !== 'undefined') module.exports = policy;
})(globalThis);
