# -*- coding: utf-8 -*-
"""选品上架：选品入库 + 调用闲管家创建/发布商品。"""
import logging
import json
import hashlib
from uuid import UUID

import models
import config
from services import xgj_client as xgj
from services import template_service, image_service, listing_service
from services.money import to_fen

log = logging.getLogger(__name__)


def cache_kongfz_results(items):
    """搜书结果自动入库：kongfz_url 为唯一键，命中只更新价格/时间。"""
    n = 0
    for b in items or []:
        url = (b.get("kongfz_url") or "").strip()
        if not url:
            continue
        models.execute(
            "INSERT INTO book_sources(isbn,book_title,author,publisher,pub_year,"
            "kongfz_url,kongfz_price,image_url) VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(kongfz_url) DO UPDATE SET"
            " kongfz_price=excluded.kongfz_price,"
            " isbn=COALESCE(NULLIF(excluded.isbn,''),book_sources.isbn),"
            " last_seen=CURRENT_TIMESTAMP",
            (b.get("isbn"), b.get("book_title"), b.get("author"),
             b.get("publisher"), b.get("pub_year"), url,
             b.get("kongfz_price"), b.get("image_url")),
        )
        n += 1
    return n


def sync_xianyu_accounts():
    """从闲管家授权列表同步真实店名 + 授权有效性到 xianyu_accounts。

    user_name = 闲鱼/淘宝登录名(表里当 seller_id 用)，user_nick = 真实店铺名。
    is_valid=false 的授权已过期，标记停用使其不在发布店铺下拉里出现。
    """
    client = xgj.XgjClient()
    try:
        shops = client.list_shops()
    except Exception as e:
        log.warning("同步店铺列表失败：%s", e)
        return 0
    if isinstance(shops, dict):
        shops = shops.get("list") or []
    n = 0
    for s in shops or []:
        sid = str(s.get("user_name") or "")
        nick = (s.get("user_nick") or "").strip()
        if not sid or not nick:
            continue
        valid = 1 if s.get("is_valid") else 0
        aid = str(s.get("authorize_id") or "")
        models.execute(
            "INSERT INTO xianyu_accounts(seller_id,authorize_id,shop_name,is_active)"
            " VALUES(?,?,?,?) ON CONFLICT(seller_id) DO UPDATE SET"
            " authorize_id=excluded.authorize_id,"
            " shop_name=excluded.shop_name, is_active=excluded.is_active",
            (sid, aid, nick, valid))
        n += 1
    return n


def sync_xianyu_products():
    """从闲管家拉在售商品入 products 表：详情接口补 ISBN/itemId，回填订单 isbn。"""
    sync_xianyu_accounts()
    client = xgj.XgjClient()
    n = 0
    page = 1
    while True:
        items = client.list_products(page=page, page_size=100)
        if not items:
            break
        for it in items:
            pid, iid = xgj.pick_product_ids(it)
            if not (iid or pid):
                continue
            if not pid:
                continue
            try:
                reconcile_remote_product(pid, client.product_detail(pid))
            except Exception as e:
                log.warning("商品 %s 同步失败，保留原状态：%s", pid, e)
                continue
            n += 1
        if len(items) < 100:
            break
        page += 1
    backfill_order_product_ids()
    resolve_entry_urls()
    return n


def reconcile_remote_product(remote_id, detail):
    """将已经读取的商品详情合并到本地，不调用平台写接口。"""
    isbn = xgj.pick_isbn(detail)
    pure = ((detail.get("book_data") or {}).get("title") or "")
    # 手动同步与定时同步可能重叠；身份检查和新建必须持有同一个写锁。
    with models.transaction():
        local_id = listing_service.find_product(remote_id, detail)
        if local_id is None:
            local_id = models.execute(
                "INSERT INTO products(book_title,pure_title,isbn,xianyu_product_id,"
                "xianyu_item_id,sale_price,status) VALUES(?,?,?,?,?,?,'draft')",
                (xgj.pick_title(detail) or "未命名商品", pure, isbn, str(remote_id),
                 xgj.pick_product_ids(detail)[1], xgj.pick_price(detail) or 0))
        else:
            models.execute("UPDATE products SET pure_title=COALESCE(NULLIF(?,''),pure_title),"
                           " isbn=COALESCE(NULLIF(?,''),isbn) WHERE id=?", (pure, isbn, local_id))
    listing_service.reconcile(local_id, remote_id, detail)
    return local_id


def backfill_order_product_ids():
    """回填订单的 item_id/product_id/isbn（按商品表匹配，书名精确兜底）。

    make独立函数：订单同步每 10 分钟跑一次也会调用，新订单入库即补齐 ISBN，
    保证「去货源」立刻能直达条目页。
    """
    for order in models.query("SELECT id,item_id,product_id,isbn,seller_id,product_title FROM orders"
                              " WHERE COALESCE(item_id,'')='' OR COALESCE(product_id,'')='' OR COALESCE(isbn,'')=''"):
        conds, params = [], []
        for col, remote in (("item_id", "remote_item_id"), ("product_id", "remote_product_id")):
            if order[col]:
                conds.append(f"l.{remote}=?")
                params.append(order[col])
        if order["seller_id"]:
            conds.append("l.seller_id=?")
            params.append(order["seller_id"])
        hits = []
        if order["item_id"] or order["product_id"]:
            hits = models.query("SELECT l.remote_item_id,l.remote_product_id,p.isbn FROM product_listings l"
                                " JOIN products p ON p.id=l.product_id WHERE " + " AND ".join(conds), params)
        if len(hits) == 1:
            r = hits[0]
            models.execute("UPDATE orders SET item_id=COALESCE(NULLIF(item_id,''),?),"
                           " product_id=COALESCE(NULLIF(product_id,''),?),"
                           " isbn=COALESCE(NULLIF(isbn,''),?) WHERE id=?",
                           (r["remote_item_id"], r["remote_product_id"], r["isbn"], order["id"]))
        elif not order["isbn"]:
            # 书名只能补唯一ISBN，不能猜另一个店铺的商品身份。
            books = models.query("SELECT DISTINCT isbn FROM products WHERE book_title=? AND COALESCE(isbn,'')!=''",
                                 (order["product_title"],))
            if len(books) == 1:
                models.execute("UPDATE orders SET isbn=? WHERE id=?", (books[0]["isbn"], order["id"]))


def resolve_entry_urls(limit=40):
    """给还没有条目页 URL 的商品解析一次（booklib 搜 ISBN/书名取第一条）。

    条目页 = 该版本全部在售卖家列表（item.kongfz.com/book/<id>.html），
    是订单「去货源」的优先直达目标。只处理缺失的，存一次终身复用。
    """
    from bots import kongfz_bot
    rows = models.query(
        "SELECT id, isbn, pure_title, book_title FROM products"
        " WHERE (entry_url IS NULL OR entry_url='')"
        " AND status NOT IN ('out_of_stock','sold')"
        " AND (isbn IS NOT NULL AND isbn!=''"
        "  OR pure_title IS NOT NULL AND pure_title!=''"
        "  OR book_title IS NOT NULL AND book_title!='')"
        " LIMIT ?", (limit,))
    for r in rows:
        kw = r["isbn"] or r["pure_title"] or r["book_title"]
        try:
            url = kongfz_bot.resolve_entry_url(kw)
            if url:
                models.execute("UPDATE products SET entry_url=? WHERE id=?",
                               (url, r["id"]))
                log.info("条目页解析成功 #%s %s -> %s", r["id"], kw, url)
        except Exception as e:
            log.warning("条目页解析失败 #%s %s：%s", r["id"], kw, e)


def save_product(book: dict, sale_price: float, title_tpl_id=None, desc_tpl_id=None,
                 submission_token=None, image_ids=None):
    sale_price = to_fen(sale_price) / 100
    values = (book.get("book_title"), book.get("author"), book.get("publisher"),
              book.get("pub_year"), book.get("isbn"), book.get("kongfz_url"),
              book.get("kongfz_price"), sale_price, book.get("image_url"),
              title_tpl_id, desc_tpl_id)
    key = "product_submission:" + str(UUID(submission_token)) if submission_token else None
    fingerprint = hashlib.sha256(json.dumps([values, image_ids], ensure_ascii=False).encode()).hexdigest()
    with models.transaction():
        if image_ids is not None:
            image_service.validate_image_ids(book.get("kongfz_url") or "", image_ids)
        saved = json.loads(models.get_setting(key) or "null") if key else None
        if saved:
            if saved["fingerprint"] != fingerprint:
                raise ValueError("该表单已提交且内容发生变化，请刷新页面后重试")
            return saved["product_id"]
        pid = models.execute(
        "INSERT INTO products(book_title,author,publisher,pub_year,isbn,kongfz_url,"
        "kongfz_price,sale_price,image_url,title_template_id,desc_template_id,status)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,'draft')",
        values)
        if image_ids is not None:
            models.set_setting(f"product_image_order:{pid}", json.dumps(image_ids))
        if key:
            models.set_setting(key, json.dumps({"product_id": pid, "fingerprint": fingerprint}))
        return pid


def list_products(status=None, shop=None):
    conds, params = [], []
    if status:
        conds.append("status=?"); params.append(status)
    if shop == "_none":
        conds.append("(seller_id IS NULL OR seller_id='')")
    elif shop:
        conds.append("EXISTS(SELECT 1 FROM product_listings l WHERE l.product_id=products.id AND l.seller_id=?)")
        params.append(shop)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return models.query(
        f"SELECT * FROM products{where} ORDER BY id DESC LIMIT 500", params)


def product_shop_counts():
    """本地选品按店计数：{seller_id or '': n}，多店商品按 LIKE 重复计入各店。"""
    out = {"": models.query(
        "SELECT COUNT(*) c FROM products"
        " WHERE seller_id IS NULL OR seller_id=''", one=True)["c"]}
    for a in models.query("SELECT seller_id FROM xianyu_accounts WHERE is_active=1"):
        out[a["seller_id"]] = models.query(
            "SELECT COUNT(*) c FROM product_listings WHERE seller_id=?",
            (a["seller_id"],), one=True)["c"]
    out["_total"] = models.query("SELECT COUNT(*) c FROM products", one=True)["c"]
    return out


def shop_product_groups():
    """店铺商品页数据：按店分组（seller_id 逗号串拆开归店）+ 未上架草稿。

    返回 (groups, drafts)；groups = {seller_id: [产品行,...]}。"""
    groups, drafts = {}, []
    for p in models.query("SELECT * FROM products ORDER BY id DESC LIMIT 500"):
        listings = models.query("SELECT * FROM product_listings WHERE product_id=?", (p["id"],))
        for listing in listings:
            display = dict(p)
            display.update(status=listing["status"], listing_error=listing["last_error"],
                           xianyu_product_id=listing["remote_product_id"],
                           xianyu_item_id=listing["remote_item_id"])
            if listing["sale_price_fen"] is not None:
                display["sale_price"] = listing["sale_price_fen"] / 100
            payload = json.loads(listing["payload_json"] or "{}")
            if payload.get("title"):
                display["book_title"] = payload["title"]
            groups.setdefault(listing["seller_id"], []).append(display)
        if not listings:
            drafts.append(p)
    return groups, drafts


def get_product(pid):
    return models.query("SELECT * FROM products WHERE id=?", (pid,), one=True)


def _shop_region(client, seller_id):
    """发货地（province/city/district 国标码）：从该店任一在售商品详情抄。
    拿不到时回退北京东城（xy594243396229 的实测值）。"""
    row = models.query(
        "SELECT remote_product_id AS xianyu_product_id FROM product_listings"
        " WHERE status='listed' AND seller_id=?"
        " AND COALESCE(remote_product_id,'')!='' LIMIT 1", (seller_id,), one=True)
    if row:
        try:
            d = client.call("/api/open/product/detail",
                            {"product_id": int(row["xianyu_product_id"])})
            d = d.get("data", d)
            for sh in (d.get("publish_shop") or []):
                if (sh.get("user_name") or "") == seller_id and sh.get("province"):
                    return {"province": sh["province"], "city": sh["city"],
                            "district": sh["district"]}
        except Exception as e:
            log.warning("读取店铺 %s 发货地失败: %s", seller_id, e)
    return {"province": 110000, "city": 110100, "district": 110101}


def _ensure_isbn(p):
    """图书类目强制要求 ISBN。库里没有时从图池任一在卖商品页 meta 里抓并回写。"""
    isbn = (p["isbn"] or "").strip()
    if isbn:
        return isbn
    row = models.query(
        "SELECT source_url FROM image_pool WHERE entry_url=? AND source='kongfz'"
        " AND source_url IS NOT NULL AND source_url!='' LIMIT 1",
        (p["kongfz_url"] or "",), one=True)
    url = (row["source_url"] if row else "") or p["kongfz_url"] or ""
    if not url:
        return ""
    try:
        import re, requests
        t = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"}).text
        m = re.search(r"ISBN[：:\s]*([0-9]{9,13}[0-9Xx]?)", t)
        if m:
            isbn = m.group(1)
            models.execute("UPDATE products SET isbn=? WHERE id=?",
                           (isbn, p["id"]))
            log.info("商品 #%s 补到 ISBN %s（来源 %s）", p["id"], isbn, url)
    except Exception as e:
        log.warning("抓 ISBN 失败 %s：%s", url, e)
    return isbn


def publish_product(pid, seller_id=None, title_template_id=None,
                    desc_template_id=None, sale_price=None, image_ids=None):
    """把本地选品发布到闲鱼：渲染模板 -> create -> publish。返回最终标题。

    四个可选覆盖用于"挂到另一店"：换模板/换价/换图，不动产品本体设置。"""
    p = get_product(pid)
    if not p:
        raise ValueError("选品不存在")
    if image_ids is None:
        saved_order = models.get_setting(f"product_image_order:{pid}")
        if saved_order:
            image_ids = json.loads(saved_order)
    accounts = list_xianyu_accounts()
    if not seller_id and len(accounts) == 1:
        seller_id = accounts[0]["seller_id"]
    if not seller_id or seller_id not in {a["seller_id"] for a in accounts}:
        raise ValueError("请选择一个授权有效的目标店铺")
    client = xgj.XgjClient()
    if config.DEMO_MODE and not client.configured:
        price = to_fen(sale_price if sale_price is not None else p["sale_price"])
        with models.transaction():
            models.execute("INSERT INTO product_listings(product_id,seller_id,remote_product_id,"
                           "remote_item_id,sale_price_fen,status) VALUES(?,?,?,?,?,'listed')"
                           " ON CONFLICT(product_id,seller_id) DO UPDATE SET status='listed',"
                           "sale_price_fen=excluded.sale_price_fen",
                           (pid, seller_id, f"demo-{pid}-{seller_id}", f"demo-item-{pid}-{seller_id}", price))
            listing_service._refresh_product(pid)
        return p["book_title"]
    return listing_service.publish(pid, seller_id, lambda: _prepare_publish_payload(
        p, client, seller_id, title_template_id, desc_template_id, sale_price, image_ids), client)


def _prepare_publish_payload(p, client, seller_id, title_template_id,
                             desc_template_id, sale_price, image_ids):
    book = dict(p)
    tpl_t = title_template_id or p["title_template_id"]
    tpl_d = desc_template_id or p["desc_template_id"]
    price = sale_price if sale_price is not None else p["sale_price"]
    price_fen = to_fen(price)
    title_tpl = template_service.get_template(tpl_t) if tpl_t else None
    desc_tpl = template_service.get_template(tpl_d) if tpl_d else None
    title = (template_service.render(title_tpl["content"], book)
             if title_tpl else p["book_title"])
    title = template_service.publish_title(title)
    if not title:
        raise ValueError("上架标题不能为空，请检查标题模板")
    desc = (template_service.render(desc_tpl["content"], book)
            if desc_tpl else p["book_title"])

    # 图池选中的实拍图优先（kongfz_url 即条目页 = 图池锚点）；没有则退回单张封面
    images = image_service.publish_image_urls(p["kongfz_url"] or "",
                                              image_ids=image_ids)
    if not images and p["image_url"]:
        images = [p["image_url"]]
    log.info("商品 #%s 上架图片 %d 张", p["id"], len(images))

    region = _shop_region(client, seller_id) if seller_id else {}
    shop = {"user_name": seller_id, "title": title, "content": desc,
            "price": price_fen, "stock": 1, "images": images,
            "express_way": 1, "is_self_pickup": False, **region}
    payload = {
        "title": title,
        "price": price_fen,
        "original_price": price_fen,
        "express_fee": 0,
        "images": images,
        "item_biz_type": 2,   # 2=二手实物商品（闲管家必填，缺了报100004）
        "sp_biz_type": 24,    # 24=图书类目（同为必填）
        "channel_cat_id": "c3c6e8d1d63c0618b108d382c4e6ea42",  # 图书频道类目ID
        "channel_pv": [{"property_id": "3b9f06b2bccead76", "property_name": "成色",
                        "value_id": "ace3677e06f0cf48f2c53534a136211b",
                        "value_name": "轻微使用痕迹"}],
        "stock": 1,           # 必填，二手书单件上架
        "spec_type": 1, "flash_sale_type": 0, "is_tax_included": False,
        "publish_shop": [shop] if seller_id else [],
    }
    isbn = _ensure_isbn(p)
    if isbn:
        payload["book_data"] = {"isbn": isbn, "title": p["pure_title"] or p["book_title"],
                                "author": p["author"] or "", "publisher": p["publisher"] or ""}

    return payload


def down_shelf(pid, seller_id=None):
    """下架。多店商品给 seller_id 时只下该店的挂售（按 publish_shop 拿它的 item_id）。"""
    client = xgj.XgjClient()
    if config.DEMO_MODE and not client.configured:
        rows = models.query("SELECT * FROM product_listings WHERE product_id=?", (pid,))
        for row in rows:
            if (not seller_id or row["seller_id"] == seller_id) and str(row["remote_product_id"]).startswith("demo-"):
                listing_service._state(row["id"], "out_of_stock")
        return
    listing_service.down(pid, seller_id, client)


def down_shelf_non_books():
    """批量下架「非图书」在售商品：无任何书目信息（isbn 和 pure_title 都为空）。

    返回 (下架数, [失败原因])。
    """
    rows = models.query(
        "SELECT id, book_title, xianyu_product_id FROM products"
        " WHERE status='listed'"
        " AND (isbn IS NULL OR isbn='')"
        " AND (pure_title IS NULL OR pure_title='')")
    client = xgj.XgjClient()
    ok, fails = 0, []
    for r in rows:
        try:
            listing_service.down(r["id"], None, client)
            ok += 1
            log.info("非图书商品已下架 #%s %s", r["id"], r["book_title"])
        except Exception as e:
            fails.append(f"{(r['book_title'] or '')[:20]}: {e}")
            log.warning("非图书下架失败 #%s：%s", r["id"], e)
    return ok, fails


def list_xianyu_accounts():
    return models.query(
        "SELECT * FROM xianyu_accounts WHERE is_active=1 ORDER BY id")


def add_xianyu_account(seller_id, shop_name=""):
    return models.execute(
        "INSERT OR IGNORE INTO xianyu_accounts(seller_id,shop_name) VALUES(?,?)",
        (seller_id, shop_name),
    )
