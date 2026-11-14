// 与后台标题规则保持一致：最多60个UTF-16字符，不截断emoji。
function limitPublishTitle(title) {
    let value = title.trim().slice(0, 60);
    if (/[\uD800-\uDBFF]$/.test(value)) value = value.slice(0, -1);
    return value.trimEnd();
}

function updatePublishTitlePreviews() {
    document.querySelectorAll('form').forEach(form => {
        const select = form.querySelector('[name="title_template_id"]');
        if (!select || !select.selectedOptions[0]?.hasAttribute('data-title-content')) return;
        let book;
        if (form.dataset.titleBook) book = JSON.parse(form.dataset.titleBook);
        else {
            const value = name => form.querySelector(`[name="${name}"]`)?.value || '';
            book = {book_title: value('book_title'), author: value('author'),
                publisher: value('publisher'), pub_year: value('pub_year'),
                isbn: value('isbn'), sale_price: value('sale_price')};
        }
        const mapping = {'书名': book.book_title || '', '标题': book.book_title || '',
            '作者': book.author || '', '出版社': book.publisher || '', '年份': book.pub_year || '',
            'ISBN': book.isbn || '', 'isbn': book.isbn || '',
            '售价': form.querySelector('[name="sale_price"]')?.value || String(book.sale_price || '')};
        const raw = select.selectedOptions[0].dataset.titleContent.replace(/\{([^{}]+)\}/g,
            (match, key) => Object.hasOwn(mapping, key.trim()) ? mapping[key.trim()] : match);
        const title = limitPublishTitle(raw);
        let preview = form.querySelector('[data-title-preview]');
        if (!preview) {
            preview = document.createElement('div');
            preview.dataset.titlePreview = '';
            preview.className = 'col-12 small text-muted';
            form.appendChild(preview);
        }
        preview.textContent = `上架标题（${title.length}/60）：${title}`
            + (raw.trim().length > 60 ? '（模板内容超长，已截短）' : '');
    });
}

document.addEventListener('change', updatePublishTitlePreviews);
document.addEventListener('input', updatePublishTitlePreviews);
updatePublishTitlePreviews();
