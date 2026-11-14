# -*- coding: utf-8 -*-
"""孔夫子下单页自动填收货信息。

用法： python -m bots.kongfz_checkout <order_id>

流程：可见模式 Edge（登录态 profile）→ 商品页 → 立即购买 → 确认订单页
策略：
  1. 收货人已在地址列表 → 按姓名+手机直接点选
  2. 不在列表 → 点末位地址「修改」→ 智能填写粘贴整段地址自动识别 → 保存并使用
  3. 填买家留言"无需发票，请勿放任何店铺信息"
填完**停手**，浏览器留着给用户核对付款，绝不点提交。
"""
import json
import logging
import sys

import config
import models

log = logging.getLogger(__name__)

LISTING_NEED = "book.kongfz.com"
REMARK = "无需发票，请勿放任何店铺信息"

# 地址列表匹配：返回 {match, repair}。
# match  = 姓名+手机+地区三者一致（可直接点选）；
# repair = 姓名+手机一致但地区不符（存档坏条，应原地修复而不是误选）；
# 两者都 -1 = 真新买家（走末位地址修改）
_FIND_LI_JS = """(recv) => {
  const lis = [...document.querySelectorAll('.address-list li')];
  const norm = s => (s||'').replace(/\\s+/g,'');
  const core = s => norm(s).replace(/(省|市|区|县|自治区|壮族|回族|维吾尔|特别行政区)/g,'');
  const name = norm(recv.receiver_name), phone = norm(recv.receiver_mobile);
  const region = [recv.prov_name, recv.city_name, recv.area_name]
    .map(core).filter(c => c && c.length >= 2);
  let match = -1, repair = -1;
  const regionText = li => {   // 只看地区字段(.address-info2 除去 .address-text)，防详细地址里的地名误判
    const info2 = li.querySelector('.address-info2');
    if (!info2) return '';
    const c = info2.cloneNode(true);
    const at = c.querySelector('.address-text');
    if (at) at.remove();
    return norm(c.textContent);
  };
  lis.forEach((li, i) => {
    const t = norm(li.innerText);
    if (phone && !t.includes(phone)) return;      // 手机必须命中
    if (!phone && !(name && t.includes(name))) return;
    const rt = regionText(li);
    if (region.some(c => rt.includes(c))) { if (match < 0) match = i; }
    else if (repair < 0) repair = i;
  });
  return { match, repair };
}"""

def _ensure_region(page, receiver):
    """智能识别有时不更新地区级联——校验 .area-text，错了先激活 tab 再逐级点选。
    返回最终地区是否已含目标区县；False = 没改对，调用方绝不能保存。"""
    import re
    cores = [re.sub(r"(省|市|区|县|自治区|壮族|回族|维吾尔|特别行政区)", "",
                    receiver.get(k) or "")
             for k in ("prov_name", "city_name", "area_name")]
    cores = [c for c in cores if len(c) >= 2]
    area_core = cores[-1] if cores else ""
    get_txt = "()=>document.querySelector('.k-area-select-wrap .area-text')?.innerText||''"
    try:
        if area_core and area_core in page.evaluate(get_txt):
            return True
        page.evaluate(
            "()=>{const w=document.querySelector('.k-area-select-wrap .area-text');"
            "w&&w.dispatchEvent(new MouseEvent('click',{bubbles:true}))}")
        page.wait_for_selector(
            ".k-area-select-wrap .area-wrap:not([style*='none'])", timeout=3000)
        tabs = [".tab-prov", ".tab-city", ".tab-area"]
        pending = cores[:3]
        # 逐 tab 激活，每个层级尝试剩余目标中任一匹配
        # （直辖市市级面板直接列区，city 目标会让位给 area 目标）
        for i in range(3):
            if not pending:
                break
            page.evaluate(  # 先激活对应层级 tab（面板默认停在旧区县层）
                "(t)=>{const e=document.querySelector('.k-area-select-wrap '+t);"
                "e&&e.dispatchEvent(new MouseEvent('click',{bubbles:true}))}",
                tabs[i])
            page.wait_for_timeout(400)
            hit = None
            for _ in range(8):  # 面板渲染是异步的
                hit = page.evaluate("""(parts) => {
                  const ls=[...document.querySelectorAll(
                    '.k-area-select-wrap .area-content a')].filter(a=>a.offsetParent);
                  const n=s=>(s||'').replace(/\\s+/g,'');
                  for(const c of parts){
                    const hit=ls.find(a=>n(a.innerText).includes(c)
                      ||(n(a.innerText).length>=2&&c.includes(n(a.innerText))));
                    if(hit){hit.dispatchEvent(new MouseEvent('click',{bubbles:true}));
                      return c}
                  } return null;}""", pending)
                if hit:
                    break
                page.wait_for_timeout(300)
            if not hit:
                if i == 0:
                    return False  # 省都选不上就是失败；其余层没可点的就跳过
                continue
            pending.remove(hit)
            page.wait_for_timeout(500)
        final = page.evaluate(get_txt)
        return not area_core or area_core in final
    except Exception as e:
        log.warning("级联兜底失败: %s", e)
        return False


# 智能填写粘贴走 Playwright 原生 fill：JS 赋值不会触发 Vue v-model 更新，
# 原生 fill 模拟真实输入事件链，"粘贴并识别"才能读到内容。

_CLICK_SAVE_JS = """() => {
  const d = document.querySelector('.address-dialog .el-dialog');
  if (!d) return 'no_dialog';
  const btn = [...d.querySelectorAll('button')]
    .find(e => (e.innerText||'').trim() === '保存并使用');
  if (!btn) return 'no_btn';
  btn.click();
  return 'ok';
}"""

def _fill_remark(page):
    """点"添加备注"展开输入框填内容，再点确认按钮提交（不点会丢）。"""
    try:
        page.locator(".remark-entry").first.click()
        inp = page.locator(
            ".remark-popup textarea, .remark-input textarea, .remark-input input").first
        inp.wait_for(state="visible", timeout=4000)
        inp.fill(REMARK)
        page.evaluate("""() => {
          const bs=[...document.querySelectorAll('.remark-actions button')];
          const ok=bs.find(b=>/确|保存|完成|提交/.test(b.innerText))
            ||document.querySelector('.remark-actions .k-button--primary');
          ok&&ok.dispatchEvent(new MouseEvent('click',{bubbles:true}));}""")
        return True
    except Exception as e:
        log.warning("留言填写失败: %s", e)
        return False


def _full_addr(receiver):
    region = "".join(receiver.get(k) or "" for k in
                     ("prov_name", "city_name", "area_name", "town_name"))
    return f"{receiver.get('receiver_name','')} {receiver.get('receiver_mobile','')} {region}{receiver.get('address','')}".strip()


def _shot(page, order_id):
    try:
        folder = config.DATA_DIR / "logs"
        folder.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(folder / f"checkout_{order_id or 'x'}.png"))
    except Exception:
        pass


def fill_checkout(listing_url, receiver, order_id=None):
    """开商品页→下单页→选/填地址+留言，浏览器保持打开。"""
    from playwright.sync_api import sync_playwright

    res = {"ok": False, "mode": "", "msg": ""}
    with sync_playwright() as p:
        from bots.kongfz_bot import _launch
        ctx = _launch(p, headless=False)
        page = None
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.bring_to_front()
            page.goto(listing_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            before = set(ctx.pages)
            page.click("a.go-buy", timeout=8000)
            page.wait_for_timeout(5000)
            new_pages = [pg for pg in ctx.pages if pg not in before]
            page = new_pages[-1] if new_pages else page
            try:
                page.wait_for_selector(".address-list", timeout=12000)
            except Exception:
                pass

            if "login" in page.url or "用户登录" in (page.title() or ""):
                res["msg"] = "孔夫子未登录：请在打开的窗口里登录，然后重新点填单"
                _keep_open(page)
                return res

            # 1) 优先匹配已存地址（姓名+手机+地区三者一致才敢点选）
            found = page.evaluate(_FIND_LI_JS, receiver)
            if found["match"] >= 0:
                page.locator(".address-list li").nth(found["match"]) \
                    .locator(".address-radio").click()
                res["mode"] = "matched"
                res["msg"] = "已点选匹配的收货地址，请核对后提交付款"
            else:
                # 2) 地区不符的存档坏条原地修复；真新买家改末位地址
                #    （修改入口在滚动区外可能不可见，用 JS 点击绕过可见性检查）
                idx = found["repair"] if found["repair"] >= 0 else -1
                page.evaluate(
                    f"() => [...document.querySelectorAll('.address-list li .edit')]"
                    f"[{'at(-1)' if idx < 0 else idx}]?.click()")
                page.wait_for_selector(".address-dialog .el-dialog", timeout=8000)
                page.wait_for_timeout(1200)
                page.locator(".address-dialog textarea.el-textarea__inner").fill(
                    _full_addr(receiver))
                page.wait_for_timeout(400)
                page.locator(".address-dialog .btn-parse-w button").click()
                res["mode"] = "smart_paste"
                page.wait_for_timeout(2500)
                if not _ensure_region(page, receiver):
                    # 地区级联没改对——绝不能点保存（会把错误地区写回账号），
                    # 弹窗留着，请手动选省市区后自己保存
                    res["msg"] = ("地址已填入但地区级联没改对，"
                                  "请在弹窗里手动选省市区后保存")
                else:
                    r2 = page.evaluate(_CLICK_SAVE_JS)
                    page.wait_for_timeout(1500)
                    res["msg"] = ("已用智能填写写入新地址并保存，请核对后提交付款"
                                  if r2 == "ok" else f"保存按钮未点到({r2})，请手动保存")

            # 3) 留言
            if not _fill_remark(page):
                res["msg"] += "（留言未填上，手动补一下）"
            res["ok"] = True
            _shot(page, order_id)
        except Exception as e:
            res["msg"] = f"填单异常：{e}（浏览器留在当前页，可手动完成）"
            log.exception("fill_checkout failed")
            _shot(page, order_id)
        finally:
            _keep_open(page)
            ctx.close()
        return res


def _keep_open(page):
    """阻塞直到用户关掉浏览器窗口。"""
    if page is None:
        return
    try:
        while True:
            page.wait_for_timeout(2000)
    except Exception:
        pass


def main(order_id):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s checkout %(message)s")
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        print("order not found", order_id); return 2
    url = order["kongfz_url"]
    if not url or LISTING_NEED not in url:
        print("no listing url"); return 3
    receiver = json.loads(order["receiver_json"] or "{}")
    # 单字/掩码收货人名补齐（孔夫子要求2-30字）：先查历史全名，没有再"先生"
    from services import order_service
    receiver = order_service.normalize_receiver(receiver)
    r = fill_checkout(url, receiver, order_id)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
