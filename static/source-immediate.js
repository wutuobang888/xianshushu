// 地址核对完成后直达孔夫子，避免结算提前读取上一笔地址；不提交采购或付款。
(() => {
    const statuses = new Map();
    let active = null;
    const official = value => {
        const url = new URL(value, location.href);
        if (!['book.kongfz.com','item.kongfz.com','search.kongfz.com'].includes(url.hostname)
                || !['https:','http:'].includes(url.protocol) || url.username || url.password
                || (url.port && url.port !== '443')) throw new Error('货源链接无效，请返回订单检查');
        url.protocol = 'https:';
        return url.href;
    };
    const show = (id, text, failed = false) => {
        const node = document.querySelector(`[data-source-sync-status="${id}"]`);
        if (!node) return;
        node.hidden = false;
        node.textContent = text;
        node.className = 'd-block mb-1 ' + (failed ? 'text-danger' : 'text-muted');
    };
    const synchronize = async (id, popup, source) => {
        statuses.set(id, 'pending');
        show(id, '正在核对本单地址，完成后自动打开孔夫子');
        try {
            const csrf = document.querySelector('script[data-csrf]')?.dataset.csrf;
            if (!csrf) throw new Error('网站登录已过期，请刷新订单页');
            const response = await fetch(`/orders/${id}/sync_address`, {
                method:'POST', credentials:'same-origin', keepalive:true,
                headers:{'X-CSRF-Token':csrf}
            });
            let result;
            try { result = await response.json(); }
            catch (_) { throw new Error('服务器未返回同步结果，请回到订单页重新检查'); }
            if (!response.ok || !result.ok || !result.verified) {
                throw new Error(result.error || '地址同步尚未确认，请重新检查');
            }
            const destination = official(result.source_url || source);
            if (popup && popup.closed) {
                statuses.set(id, 'previous');
                show(id, '地址已核对，货源标签页已关闭；需要时请重新点击');
                return;
            }
            statuses.forEach((status, other) => {
                if (other !== id && status === 'ready') {
                    statuses.set(other, 'previous');
                    show(other, '已切换其他订单，重新采购请再次点击货源');
                }
            });
            statuses.set(id, 'ready');
            show(id, '本单地址已同步并核对，已打开孔夫子');
            if (popup && !popup.closed) popup.location.replace(destination);
            else location.assign(destination);
        } catch (error) {
            statuses.set(id, 'failed');
            const message = error.message || '地址同步结果未确认，请返回订单页检查';
            show(id, '地址同步失败：' + message, true);
            if (popup && !popup.closed) popup.close();
            alert('本笔订单的收货地址未同步成功，尚未打开货源。\n' + message);
        } finally {
            active = null;
        }
    };
    const open = event => {
        const link = event.target.closest('a[data-xss-native-source]');
        if (!link || (event.type === 'click' && event.button !== 0)
                || (event.type === 'auxclick' && event.button !== 1)) return;
        const id = link.dataset.xssNativeSource;
        event.preventDefault();
        if (!/^\d+$/.test(id)) { event.preventDefault(); return; }
        if (active !== null) {
            if (active !== id) show(id, '正在核对另一笔地址，请完成后再点这笔订单');
            return;
        }
        let source;
        try { source = official(link.dataset.sourceUrl || link.href); }
        catch (error) { alert(error.message); return; }
        active = id;
        // 用户点击时预留标签页，核对成功前不加载孔夫子；避免异步打开被拦截。
        const popup = window.open('about:blank', '_blank');
        if (popup) popup.opener = null;
        void synchronize(id, popup, source);
    };
    document.addEventListener('click', open);
    document.addEventListener('auxclick', open);
    // 浏览器从后台恢复时补显结果；重新加载页面不会自动重复同步。
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        statuses.forEach((status,id) => {
            if (status === 'pending') show(id, '正在核对本单地址，完成后自动打开孔夫子');
        });
    });
})();
