"""云端结算地址填写：完整匹配、识别前后核对，失败停手。"""
from bots.kongfz_checkout import _ensure_region, _full_addr, _fill_remark, _CLICK_SAVE_JS

FIND_JS = r"""recv => {
  const norm = value => (value||'').normalize('NFKC').replace(/[\s,，。:：;；()]+/g,'')
    .replace(/广西壮族自治区/g,'广西').replace(/内蒙古自治区/g,'内蒙古')
    .replace(/宁夏回族自治区/g,'宁夏').replace(/新疆维吾尔自治区/g,'新疆')
    .replace(/西藏自治区/g,'西藏');
  const full = norm(['prov_name','city_name','area_name','town_name','address']
    .map(k => recv[k]||'').join(''));
  let match = -1, repair = -1, selected = -1;
  [...document.querySelectorAll('.address-list li')].forEach((li, i) => {
    if (li.classList.contains('selected')) selected = i;
    if (!recv.receiver_name || !recv.receiver_mobile || !full) return;
    const name = li.querySelector('.address-info1 span:not(.phone)');
    const phone = li.querySelector('.address-info1 .phone');
    if (!name || !phone || norm(name.innerText) !== norm(recv.receiver_name)
        || norm(phone.innerText) !== norm(recv.receiver_mobile)) return;
    const address = li.querySelector('.address-info2');
    const copy = address ? address.cloneNode(true) : null;
    if (copy) copy.querySelectorAll('.default').forEach(node => node.remove());
    if (copy && norm(copy.textContent) === full) { if (match < 0) match = i; }
    else if (repair < 0) repair = i;
  });
  return {match, repair, selected};
}"""


def fill_address(page, receiver):
    """填当前结算页并回读完整地址；识别错误时不保存、不宣称成功。"""
    found = page.evaluate(FIND_JS, receiver)
    if found['match'] < 0:
        # 沿用原填单的地址修改入口，每次从本单快照取值，不修改默认地址开关。
        index = found['repair']
        edits = page.locator('.address-list li .edit')
        if edits.count():
            (edits.nth(index) if index >= 0 else edits.last).evaluate('element => element.click()')
        else:
            page.get_by_text('新增收货地址', exact=True).click()
        dialog = page.locator('.address-dialog .el-dialog')
        dialog.wait_for(state='visible', timeout=8000)
        dialog.locator('textarea.el-textarea__inner').fill(_full_addr(receiver))
        dialog.locator('.btn-parse-w button').click()
        page.wait_for_timeout(1800)
        if not _ensure_region(page, receiver):
            return {'ok':False, 'msg':'地区未识别正确，已停止保存。请结束采购后重试或联系检查'}
        # 不仅核对地区，还检查智能识别后的姓名、电话和详细地址。
        inputs = dialog.locator('input').evaluate_all(
            'nodes => nodes.map(n => ({placeholder:n.placeholder,value:n.value}))')
        def field(phrase):
            return next((v['value'] for v in inputs if phrase in v['placeholder']), '')
        from services.kongfz_monitor import _address_key
        region_text = _address_key(dialog.locator('.k-area-select-wrap .area-text').inner_text())
        if any(_address_key(receiver[k]) not in region_text for k in ('prov_name','city_name','area_name')):
            return {'ok':False, 'msg':'省市区核对不一致，已停止保存，请结束采购后重试或联系检查'}
        name = field('收货人')
        phone = field('手机') or field('电话')
        detail = field('详细地址')
        expected_detail = (receiver.get('town_name') or '') + receiver['address']
        if not detail:
            values = dialog.locator('textarea').evaluate_all('nodes => nodes.map(n => n.value)')
            detail = next((v for v in values if _address_key(v) == _address_key(expected_detail)), '')
        if (name != receiver['receiver_name'] or phone != receiver['receiver_mobile']
                or _address_key(detail) != _address_key(expected_detail)):
            return {'ok':False, 'msg':'智能识别结果与本单收货信息不一致，已停止保存，请结束采购后重试或联系检查'}
        if page.evaluate(_CLICK_SAVE_JS) != 'ok':
            return {'ok':False, 'msg':'未能保存地址，已停止填单'}
        dialog.wait_for(state='hidden', timeout=8000)
        page.wait_for_timeout(500)
        found = page.evaluate(FIND_JS, receiver)
    if found['match'] < 0:
        return {'ok':False, 'msg':'保存后完整收货信息核对未通过，不能自动提交'}
    page.locator('.address-list li').nth(found['match']).locator('.address-radio').click()
    page.wait_for_function('(index) => document.querySelectorAll(".address-list li")[index]?.classList.contains("selected")',
                           arg=found['match'], timeout=5000)
    remark_ok = _fill_remark(page)
    return {'ok':True, 'msg':'地址已填写并核对，请手动提交付款'
            + ('' if remark_ok else '（留言未填上，请手动补充）')}


def enable_manual_submit(page, receiver):
    # 用户点击提交的当刻再查选中的卡片，防止填好后误切到其他地址。
    page.evaluate('(receiver) => { const find = '+FIND_JS+'; '
                  'window.__xssAddressCheck = () => { const r=find(receiver); '
                  'return r.match >= 0 && r.match === r.selected; }; '
                  'window.__xssAddressReady = true; }', receiver)
