// 云端表单与同源异步写请求携带会话校验令牌，不向其他站点发送令牌。
(() => {
    const token = document.currentScript.dataset.csrf;
    const addToken = form => {
        if ((form.method || '').toUpperCase() !== 'POST' || new URL(form.action,location.href).origin !== location.origin) return;
        let input = form.querySelector('input[name="csrf_token"]');
        if (!input) { input = document.createElement('input'); input.type='hidden'; input.name='csrf_token'; form.appendChild(input); }
        input.value = token;
    };
    document.querySelectorAll('form').forEach(addToken);
    document.addEventListener('submit',event => addToken(event.target),true);
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options = {}) => {
        const url = new URL(input instanceof Request ? input.url : input,location.href);
        const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        if (url.origin === location.origin && !['GET','HEAD','OPTIONS'].includes(method)) {
            const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
            headers.set('X-CSRF-Token',token);
            options = {...options,headers};
        }
        return originalFetch(input, options);
    };
    const originalBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url,data) => {
        if (new URL(url,location.href).origin === location.origin && !data) {
            data = new FormData(); data.set('csrf_token',token);
        }
        return originalBeacon(url,data);
    };
})();
