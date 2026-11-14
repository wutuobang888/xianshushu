// 上架表单图片预览与排序；提交顺序由隐藏字段明确保存。
class PublishImageOrder {
    constructor(container, hidden, status) {
        this.container = container;
        this.hidden = hidden;
        this.status = status;
        this.images = [];
        this.entry = '';
        this.cover = '';
        this.ready = false;
    }
    setBook(entry, cover) {
        if (entry !== this.entry) this.images = [];
        this.entry = entry;
        this.cover = cover;
        this.ready = false;
        this.status.textContent = '正在加载已选图片…';
        this.render();
    }
    load(images) {
        const available = new Map(images.map(image => [image.id, image]));
        const order = this.images.map(image => image.id).filter(id => available.has(id));
        for (const image of images) if (!order.includes(image.id)) order.push(image.id);
        this.images = order.map(id => available.get(id));
        this.ready = true;
        this.status.textContent = this.images.length
            ? `已选 ${this.images.length} 张，拖动或点击左右箭头调整顺序，第1张为封面`
            : this.cover ? '未选图池图片，将使用图书封面' : '还没有图片，请先到图池选图';
        this.render();
    }
    move(from, to) {
        if (from === to || from < 0 || to < 0 || from >= this.images.length || to >= this.images.length) return;
        const [image] = this.images.splice(from, 1);
        this.images.splice(to, 0, image);
        this.render();
    }
    render() {
        this.hidden.value = JSON.stringify(this.images.map(image => image.id));
        this.container.replaceChildren();
        const display = this.images.length ? this.images : this.ready && this.cover ? [{image_url: this.cover}] : [];
        display.forEach((image, index) => {
            const tile = document.createElement('div');
            tile.className = 'border rounded bg-white p-1';
            tile.style.width = '112px';
            tile.dataset.imageId = image.id || 'cover';
            tile.draggable = this.images.length > 1;
            const img = document.createElement('img');
            img.src = image.final_url || image.image_url;
            img.alt = `第${index + 1}张图片`;
            img.referrerPolicy = 'no-referrer';
            img.draggable = false;
            img.style.cssText = 'width:102px;height:102px;object-fit:contain;display:block;background:#f8f9fa;';
            tile.appendChild(img);
            const row = document.createElement('div');
            row.className = 'd-flex align-items-center justify-content-between mt-1';
            const label = document.createElement('span');
            label.className = 'small fw-bold';
            label.textContent = index === 0 ? '1 · 封面' : String(index + 1);
            row.appendChild(label);
            if (image.id) for (const [text, offset] of [['←', -1], ['→', 1]]) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-sm btn-outline-secondary py-0 px-1';
                button.textContent = text;
                button.title = offset < 0 ? '前移一位' : '后移一位';
                button.disabled = index + offset < 0 || index + offset >= this.images.length;
                button.onclick = () => this.move(index, index + offset);
                row.appendChild(button);
            }
            tile.appendChild(row);
            tile.ondragstart = event => {
                event.dataTransfer.setData('text/plain', String(image.id));
                event.dataTransfer.effectAllowed = 'move';
            };
            tile.ondragover = event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; };
            tile.ondrop = event => {
                event.preventDefault();
                const from = this.images.findIndex(candidate => candidate.id === Number(event.dataTransfer.getData('text/plain')));
                this.move(from, index);
            };
            this.container.appendChild(tile);
        });
    }
}
