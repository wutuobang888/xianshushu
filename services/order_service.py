# -*- coding: utf-8 -*-
"""订单业务：同步闲管家订单、绑定孔夫子单号、发货回填、退款同步。"""
import json
import logging
import time
from urllib.parse import quote

import models
import config
from services import xgj_client as xgj
from services.money import to_fen

log = logging.getLogger(__name__)

LOCAL_STATUS = ("pending", "purchasing", "shipped", "completed",
                "refunding", "refunded", "closed")


def _upsert_order(remote: dict):
    no = xgj.pick_order_no(remote)
    if not no:
        return None
    fields = dict(
        seller_id=xgj.pick_seller_id(remote),
        buyer_name=xgj.pick_buyer_name(remote),
        buyer_address=xgj.pick_buyer_address(remote),
        product_title=xgj.pick_title(remote),
        sale_price=xgj.pick_price(remote),
        order_time=xgj.pick_order_time(remote) or None,
        item_id=xgj.pick_product_ids(remote)[1],
        product_id=xgj.pick_product_ids(remote)[0],
        isbn=xgj.pick_isbn(remote),
    )
    existing = models.query("SELECT * FROM orders WHERE order_no=?", (no,), one=True)
    if existing:
        # 未变化的订单不重复写库；空商品标识仍保留此前的回填结果。
        sets, params = [], []
        for k, v in fields.items():
            if k in ("item_id", "product_id", "isbn") and not v:
                continue
            if v == existing[k]:
                continue
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return existing["id"]
        models.execute(
            f"UPDATE orders SET {', '.join(sets)},"
            " updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (*params, no),
        )
        return existing["id"]
    return models.execute(
        "INSERT INTO orders(order_no,seller_id,buyer_name,buyer_address,product_title,"
        "sale_price,order_time,item_id,product_id,isbn,status)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,'pending')",
        (no, fields["seller_id"], fields["buyer_name"], fields["buyer_address"],
         fields["product_title"], fields["sale_price"], fields["order_time"],
         fields["item_id"], fields["product_id"], fields["isbn"]),
    )


def _apply_remote_status(order_no, remote_status):
    row = models.query("SELECT status, waybill_no FROM orders WHERE order_no=?",
                       (order_no,), one=True)
    if not row:
        return
    local = row["status"]
    if remote_status == "refunded" and local != "refunded":
        models.execute(
            "UPDATE orders SET status='refunded', updated_at=CURRENT_TIMESTAMP"
            " WHERE order_no=?", (order_no,),
        )
    elif remote_status == "refunding" and local not in ("refunding", "refunded"):
        models.execute(
            "UPDATE orders SET status='refunding', updated_at=CURRENT_TIMESTAMP"
            " WHERE order_no=?", (order_no,),
        )
    elif remote_status == "completed_remote" and local in (
            "pending", "purchasing", "shipped"):
        # 远端交易完成（买家已确认收货）→ 本地升级 completed
        models.execute(
            "UPDATE orders SET status='completed', updated_at=CURRENT_TIMESTAMP"
            " WHERE order_no=?", (order_no,),
        )
    elif remote_status == "shipped_remote" and local in ("pending", "purchasing"):
        models.execute(
            "UPDATE orders SET status='shipped', ship_time=COALESCE(ship_time,"
            " CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP WHERE order_no=?",
            (order_no,),
        )
    elif remote_status == "closed" and (
            local in ("pending", "purchasing")
            or (local == "shipped" and not row["waybill_no"])):
        # 远端已关闭；顺带纠正此前被错标成已发货的无运单订单
        models.execute(
            "UPDATE orders SET status='closed', updated_at=CURRENT_TIMESTAMP"
            " WHERE order_no=?", (order_no,),
        )


def _refund_map(client):
    """order_no -> 退款是否已完成。"""
    try:
        refunds = client.list_refunds(page_size=100)
    except Exception as e:
        log.warning("refund/list 拉取失败：%s", e)
        return {}
    out = {}
    for r in refunds:
        if no := xgj.pick_order_no(r):
            done = (str(r.get("refund_status", "")) == "5"
                    or bool(r.get("refund_time")))
            out[no] = out.get(no, False) or done
    return out


def sync_orders():
    """轮询 order/list 入库；返回新增数量。"""
    try:
        from services import product_service
        product_service.sync_xianyu_accounts()
    except Exception:
        pass  # 店名同步失败不影响订单同步
    client = xgj.XgjClient()
    remote = _fetch_all_shops_orders(client)
    refund_map = _refund_map(client)
    added = 0
    new_pending = []
    for item in remote:
        no = xgj.pick_order_no(item)
        if not no:
            continue
        rs = xgj.map_remote_status(item, refund_map)
        existed = models.query("SELECT id FROM orders WHERE order_no=?", (no,), one=True)
        _upsert_order(item)
        if not existed:
            added += 1
            if rs == "pending" and len(new_pending) < 150:
                new_pending.append(no)
        _apply_remote_status(no, rs)
    # 新待采购订单顺带拉收货信息：偏远省份标红 + 给填单预热缓存
    for no in new_pending:
        _fetch_receiver(no, client)
        time.sleep(0.15)
    try:
        from services import product_service
        product_service.backfill_order_product_ids()
    except Exception:
        pass  # 回填失败不影响订单同步
    try:
        sync_customers()
    except Exception as e:
        log.warning("客户沉淀失败：%s", e)
    return added


def sync_customers():
    """把订单窗口期可见的买家手机号沉淀进 customers 表。

    闲鱼只在订单 pending→shipped 期间返回收货手机号，交易结束即收回；
    趁可见时把 买家→买了什么/花了多少 攒下来，熟客/私域运营都用它。
    """
    rows = models.query(
        "SELECT receiver_json,product_title,sale_price,"
        " order_time,created_at FROM orders"
        " WHERE receiver_json LIKE '%receiver_mobile%'")
    agg = {}
    for r in rows:
        try:
            j = json.loads(r["receiver_json"] or "{}")
        except Exception:
            continue
        ph = j.get("receiver_mobile") or ""
        if not (ph.startswith("1") and len(ph) == 11 and ph.isdigit()):
            continue
        c = agg.setdefault(ph, {"name": "", "city": "", "n": 0,
                                "spend": 0.0, "titles": [],
                                "first": "", "last": ""})
        c["name"] = c["name"] or j.get("receiver_name") or ""
        c["city"] = c["city"] or j.get("city_name") or ""
        c["n"] += 1
        c["spend"] += r["sale_price"] or 0
        t = str(r["order_time"] or r["created_at"] or "")
        if not c["first"] or t < c["first"]:
            c["first"] = t
        if not c["last"] or t > c["last"]:
            c["last"] = t
        t_ = r["product_title"] or ""
        if t_ and t_ not in c["titles"]:
            c["titles"].append(t_)
    for ph, c in agg.items():
        models.execute(
            "INSERT INTO customers(phone,name,city,order_count,total_spend,"
            " titles,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(phone) DO UPDATE SET name=excluded.name,"
            " city=excluded.city, order_count=excluded.order_count,"
            " total_spend=excluded.total_spend, titles=excluded.titles,"
            " first_seen=excluded.first_seen, last_seen=excluded.last_seen",
            (ph, c["name"], c["city"], c["n"], round(c["spend"], 2),
             "；".join(c["titles"][-5:]), c["first"], c["last"]))


def _fetch_all_shops_orders(client):
    """按 authorize_id 逐店铺拉订单并集（聚合列表实际只覆盖主店）。

    优先走启用店铺各自的 authorize_id；都没配才退回聚合查询兜底。
    """
    accs = models.query(
        "SELECT authorize_id FROM xianyu_accounts"
        " WHERE is_active=1 AND authorize_id IS NOT NULL AND authorize_id!=''")
    remote, seen = [], set()
    for a in accs:
        page = 1
        while True:
            items = client.list_orders(authorize_id=a["authorize_id"],
                                       page=page, page_size=100)
            for it in items or []:
                no = xgj.pick_order_no(it)
                if no and no not in seen:
                    seen.add(no)
                    remote.append(it)
            if not items or len(items) < 100:
                break
            page += 1
    if not accs:
        remote = client.list_orders(page_size=100)
    return remote


def sync_refunds():
    """退款同步：refund_status=5 或已有 refund_time 视为退款完成 -> refunded。"""
    client = xgj.XgjClient()
    refunds = client.list_refunds(page_size=100)
    n = 0
    for r in refunds:
        no = xgj.pick_order_no(r)
        if not no:
            continue
        done = (str(r.get("refund_status", "")) == "5"
                or bool(r.get("refund_time")))
        if done:
            models.execute(
                "UPDATE orders SET status='refunded', updated_at=CURRENT_TIMESTAMP"
                " WHERE order_no=? AND status!='refunded'", (no,))
        else:
            models.execute(
                "UPDATE orders SET status='refunding', updated_at=CURRENT_TIMESTAMP"
                " WHERE order_no=? AND status NOT IN ('refunding','refunded','closed')",
                (no,))
        n += 1
    return n


def _fetch_receiver(order_no, client=None):
    """拉订单详情的收货人信息写 receiver_json（按订单号）。返回 recv dict 或 None。"""
    try:
        detail = (client or xgj.XgjClient()).order_detail(order_no)
        recv = {k: detail.get(k) or "" for k in
                ("receiver_name", "receiver_mobile", "prov_name",
                 "city_name", "area_name", "town_name", "address")}
    except Exception as e:
        log.warning("拉取订单收货信息失败 %s: %s", order_no, e)
        return None
    if not (recv["receiver_name"] or recv["address"]):
        return None
    models.execute("UPDATE orders SET receiver_json=? WHERE order_no=?",
                   (json.dumps(recv, ensure_ascii=False), order_no))
    return recv


def prepare_source(order_id):
    """点「找货源」时调用：拉订单详情的收货信息缓存到 receiver_json，
    并把它设为孔夫子填单的当前目标（油猴脚本读这个）。"""
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return None
    if not order["receiver_json"]:
        recv = _fetch_receiver(order["order_no"])
        if recv:
            order = dict(order)
            order["receiver_json"] = json.dumps(recv, ensure_ascii=False)
    models.set_setting("source_order_id", str(order_id))
    return order


def source_target():
    """油猴脚本查询：当前待填单的订单 + 收货信息。没有或已绑定过就返回空。"""
    oid = models.get_setting("source_order_id")
    if not oid:
        return None
    order = models.query("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not order or order["status"] not in ("pending", "purchasing"):
        return None
    recv = normalize_receiver(json.loads(order["receiver_json"] or "{}"))
    return {"order_id": order["id"], "order_no": order["order_no"],
            "product_title": order["product_title"], "receiver": recv}


def start_fill_checkout(order_id, kongfz_url=None):
    """拉起独立的填单进程：可见 Edge 打开下单页填好收货信息。

    kongfz_url 传入时顺手记到订单上（=用户选定的货源）。进程在后台独立
    存活，浏览器窗口等用户手动关闭，不阻塞网站。
    """
    import subprocess, sys
    order = prepare_source(order_id)  # 确保 receiver_json 已缓存
    if not order:
        raise ValueError("订单不存在")
    if kongfz_url and "book.kongfz.com" in kongfz_url:
        models.execute("UPDATE orders SET kongfz_url=? WHERE id=?",
                       (kongfz_url.strip(), order_id))
        if order["item_id"]:
            # 回写商品库：同书后续订单直达该货源页（与 bind 行为一致）
            models.execute("UPDATE products SET kongfz_url=? WHERE xianyu_item_id=?",
                           (kongfz_url.strip(), order["item_id"]))
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order["kongfz_url"] or "book.kongfz.com" not in order["kongfz_url"]:
        raise ValueError("缺少孔夫子商品链接：先复制想买的那家商品页链接")
    if not json.loads(order["receiver_json"] or "{}"):
        raise ValueError("收货信息未拉到：请稍后重试")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-m", "bots.kongfz_checkout", str(order_id)],
        cwd=str(config.BASE_DIR), creationflags=creationflags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def bind_kongfz_order(order_id, kongfz_order_no, kongfz_price=None, kongfz_url=None,
                      amount_basis="manual"):
    """绑定孔夫子订单号，订单进入采购中，并加入监控表。

    kongfz_url 为可选的货源商品链接：存到本订单，并按 item_id 回写到
    products 记录——同书以后所有订单都能直达该详情页。
    一单多书（套装拆买）时可重复绑定：单号逗号拼接、成本累加。
    """
    kongfz_order_no = (kongfz_order_no or "").strip()
    if not kongfz_order_no or "," in kongfz_order_no or "，" in kongfz_order_no:
        raise ValueError("请提供一个完整的孔夫子单号，多个单号需要逐笔绑定")
    with models.transaction():
        _bind_kongfz_order(order_id, kongfz_order_no, kongfz_price, kongfz_url, amount_basis)


def _bind_kongfz_order(order_id, kongfz_order_no, kongfz_price, kongfz_url, amount_basis):
    """只在绑定事务内调用：全局归属检查、成本和监控记录一起保存。"""
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        raise ValueError("订单不存在")
    existing = models.query(
        "SELECT order_id FROM kongfz_orders WHERE kongfz_order_no=?",
        (kongfz_order_no,))
    if any(r["order_id"] != order_id for r in existing):
        raise ValueError("该孔夫子单号已关联其他订单，请先核对，不能重复绑定")
    displayed = kongfz_order_no in (order["kongfz_order_no"] or "").split(",")
    if existing and displayed:
        return  # 已发货后的重复请求也保持幂等，不重新计入成本
    if existing or displayed:
        raise ValueError("采购记录与订单单号不一致，请先核对历史数据")
    if order["status"] not in ("pending", "purchasing"):
        raise ValueError(f"当前状态 {order['status']} 不能绑定采购单号")
    paid_fen = to_fen(kongfz_price, allow_none=True)
    kongfz_price = paid_fen / 100 if paid_fen is not None else None
    display_no = kongfz_order_no
    if order["kongfz_order_no"]:
        display_no = order["kongfz_order_no"] + "," + kongfz_order_no
        kongfz_price = (to_fen(order["kongfz_price"] or 0) + (paid_fen or 0)) / 100
    models.execute(
        "UPDATE orders SET kongfz_order_no=?, kongfz_price=?,"
        " kongfz_url=COALESCE(NULLIF(?,''), kongfz_url), status='purchasing',"
        " unpaid_hint=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (display_no, kongfz_price, kongfz_url or "", order_id),
    )
    if kongfz_url and order["item_id"]:
        models.execute(
            "UPDATE products SET kongfz_url=? WHERE id IN"
            " (SELECT product_id FROM product_listings WHERE remote_item_id=? AND seller_id=?)",
            (kongfz_url, order["item_id"], order["seller_id"]),
        )
    models.execute(
        "INSERT INTO kongfz_orders(order_id,kongfz_order_no,status,paid_amount_fen,amount_basis)"
        " VALUES(?,?,'monitoring',?,?)",
        (order_id, kongfz_order_no, paid_fen, amount_basis if paid_fen is not None else None),
    )
    _recalc_profit(order_id)


def _norm_express_name(waybill_no, express_name):
    """快递名归一化：孔夫子卖家常把"邮政快递包裹"错标成 EMS，
    98 系纯数字运单 + express_code=ems 会被闲鱼风控（号段对不上）拦下。
    纯数字运单的 EMS 一律改报"中国邮政"（同一邮政网可查），
    字母开头的真·EMS 国际单号保留。"""
    if "EMS" in (express_name or "").upper() and (waybill_no or "").isdigit():
        return "中国邮政"
    return express_name


def mark_shipped(order_id, waybill_no, express_name="", auto_fill_xianyu=True,
                 kongfz_order_no=None):
    from services.process_lock import acquire
    lock = acquire(config.DATA_DIR / "locks" / f"ship-{order_id}.lock", "这笔订单正在处理发货，请稍后核对结果")
    try:
        return _mark_shipped(order_id,waybill_no,express_name,auto_fill_xianyu,kongfz_order_no)
    finally:
        lock.close()


def _mark_shipped(order_id, waybill_no, express_name="", auto_fill_xianyu=True,
                  kongfz_order_no=None):
    """本地标记已发货，并按需回填闲鱼。

    一单多包裹（套装拆买）：kongfz_order_no 指定只标对应孔夫子单；
    闲鱼只回填第一个包裹，后续发货不再覆盖 orders 上的运单号。
    """
    express_name = _norm_express_name(waybill_no, express_name)
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        raise ValueError("订单不存在")
    if order["status"] not in ("pending", "purchasing", "shipped", "completed"):
        raise ValueError(f"当前状态 {order['status']} 不能发货")
    waybill_no = (waybill_no or "").strip()
    if not waybill_no:
        raise ValueError("运单号不能为空")
    if kongfz_order_no:
        owners = models.query(
            "SELECT order_id FROM kongfz_orders WHERE kongfz_order_no=?",
            (kongfz_order_no,))
        if not owners or any(r["order_id"] != order_id for r in owners):
            raise ValueError("采购单归属不唯一或与本订单不符，已暂停发货")
    first_ship = order["status"] in ("pending", "purchasing")
    if auto_fill_xianyu and first_ship:
        client = xgj.XgjClient()
        try:
            remote = client.order_detail(order["order_no"],seller_id=order["seller_id"] or None)
            if xgj.pick_order_no(remote) != order["order_no"]:
                raise ValueError("闲鱼订单身份核对失败，已暂停发货")
            remote_status = xgj.map_remote_status(remote)
            already_shipped = remote_status in ("shipped_remote","completed_remote") and xgj.pick_waybill(remote) == waybill_no
            if not already_shipped:
                if xgj.pick_ship_status(remote) != "12" or remote_status != "pending" or xgj.pick_waybill(remote):
                    raise ValueError("闲鱼订单已退款、关闭或已发货，请先核对平台状态，未重复发货")
                client.ship_order(order["order_no"], waybill_no, express_name,
                                  seller_id=order["seller_id"] or None)
        except xgj.XgjError as e:
            if config.DEMO_MODE and not client.configured:
                log.warning("演示模式：跳过真实发货回填（%s）", e)
            else:
                raise
    # 网络调用不持有数据库写锁；返回后重读状态，避免覆盖并发的退款同步。
    with models.transaction():
        current = models.query("SELECT status FROM orders WHERE id=?",
                               (order_id,), one=True)
        if not current or current["status"] not in (
                "pending", "purchasing", "shipped", "completed"):
            raise ValueError("发货期间订单状态已变化，请核对平台结果，未覆盖本地状态")
        if first_ship and current["status"] in ("pending", "purchasing"):
            models.execute(
                "UPDATE orders SET status='shipped', waybill_no=?, express_name=?,"
                " ship_time=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (waybill_no, express_name, order_id),
            )
        if kongfz_order_no:
            models.execute(
                "UPDATE kongfz_orders SET status='shipped', waybill_no=?,"
                " express_name=?, updated_at=CURRENT_TIMESTAMP"
                " WHERE order_id=? AND kongfz_order_no=? AND status='monitoring'",
                (waybill_no, express_name, order_id, kongfz_order_no),
            )
        else:
            models.execute(
                "UPDATE kongfz_orders SET status='shipped', waybill_no=?,"
                " express_name=?, updated_at=CURRENT_TIMESTAMP"
                " WHERE order_id=? AND status='monitoring'",
                (waybill_no, express_name, order_id),
            )
        _recalc_profit(order_id)


def _recalc_profit(order_id):
    models.execute(
        "UPDATE orders SET profit = ROUND(COALESCE(sale_price,0)"
        " - COALESCE(kongfz_price,0), 2) WHERE id=?",
        (order_id,),
    )


def _isbn_source_map():
    """ISBN -> 已绑定的孔夫子货源链接（历史订单 + 商品库）。"""
    src = {}
    for table in ("orders", "products"):
        for r in models.query(
                f"SELECT isbn, kongfz_url FROM {table}"
                " WHERE kongfz_url IS NOT NULL AND kongfz_url!=''"
                " AND isbn IS NOT NULL AND isbn!=''"):
            src.setdefault(r["isbn"], r["kongfz_url"])
    return src


def _product_maps():
    """ISBN -> 商品条目页URL / 干净书名，来自 products 表。"""
    entry, title = {}, {}
    for r in models.query(
            "SELECT isbn, entry_url, pure_title, book_title FROM products"):
        if r["isbn"]:
            if r["entry_url"]:
                entry.setdefault(r["isbn"], r["entry_url"])
            t = r["pure_title"] or r["book_title"]
            if t:
                title.setdefault(r["isbn"], t)
    return entry, title


def _clean_title(t):
    """闲鱼标题去 【xx】营销前缀，作为书名兜底。"""
    import re
    return re.sub(r"^【[^】]*】", "", t or "").strip() or (t or "")


def list_orders(status=None, keyword=None, page=1, size=50, sort="desc"):
    """订单列表，返回 (rows, total)。sort: 'desc'最新在前 / 'asc'最早在前。"""
    if status == "need_confirm":
        # 虚拟状态：待采购且孔夫子侧已有同手机号在途单（待勾选确认），
        # 先全量筛出来再做内存分页
        pending, _ = list_orders("pending", keyword, page=1, size=500,
                                 sort=sort)
        ids = set(unbound_candidates_for(pending))
        rows = [o for o in pending if o["id"] in ids]
        return rows[(page - 1) * size: page * size], len(rows)
    select = ("SELECT o.*, a.shop_name FROM orders o"
              " LEFT JOIN xianyu_accounts a ON a.seller_id = o.seller_id")
    conds, params = [], []
    if status:
        conds.append("o.status=?")
        params.append(status)
    if keyword:
        kw = f"%{keyword}%"
        ors = ["o.order_no LIKE ?", "o.product_title LIKE ?",
               "o.buyer_name LIKE ?", "o.kongfz_order_no LIKE ?",
               "EXISTS (SELECT 1 FROM kongfz_orders k"
               " WHERE k.order_id=o.id AND k.kongfz_order_no LIKE ?)"]
        gparams = [kw] * 5
        # 孔夫子单号反查：未绑定扫描单按收货手机号找对应的本地订单
        scan = models.query(
            "SELECT recv_phone FROM kongfz_scanned WHERE kongfz_order_no=?",
            (keyword.strip(),), one=True)
        if scan and scan["recv_phone"]:
            ors.append("o.receiver_json LIKE ?")
            gparams.append(f"%{scan['recv_phone']}%")
        conds.append("(" + " OR ".join(ors) + ")")
        params += gparams
    where = " WHERE " + " AND ".join(conds) if conds else ""
    total = models.query(f"SELECT COUNT(*) c FROM orders o{where}",
                         params, one=True)["c"]
    direction = "ASC" if sort == "asc" else "DESC"
    sql = (select + where
           + f" ORDER BY COALESCE(o.order_time,o.created_at) {direction},"
             f" o.id {direction} LIMIT ? OFFSET ?")
    rows = models.query(sql, params + [size, (page - 1) * size])

    # 「去孔夫子」跳转优先级：已绑货源 > 同ISBN绑定 > 条目页 > ISBN条目搜索 > 书名条目搜索
    src = _isbn_source_map()
    entry_map, title_map = _product_maps()
    repeat_phones = {r["phone"] for r in models.query(
        "SELECT phone FROM customers WHERE order_count>1")}
    out = []
    for o in rows:
        d = dict(o)
        d["remote_area"] = _is_remote_area(o)
        d["repeat_customer"] = _receiver_phone(o) in repeat_phones
        if o["kongfz_url"]:
            d["jump_url"], d["jump_direct"] = _with_list_filters(o["kongfz_url"]), True
        elif o["isbn"] and o["isbn"] in src:
            d["jump_url"], d["jump_direct"] = _with_list_filters(src[o["isbn"]]), True
        elif o["isbn"] and o["isbn"] in entry_map:
            d["jump_url"], d["jump_direct"] = _with_list_filters(entry_map[o["isbn"]]), True
        else:
            key = (o["isbn"] or title_map.get(o["isbn"])
                   or _clean_title(o["product_title"]))
            d["jump_url"] = ("https://search.kongfz.com/booklib/?keyword="
                             + quote(key))
            d["jump_direct"] = False
        d["jump_url"] = _with_order_mark(d["jump_url"], o["id"])
        out.append(d)
    return out, total


REMOTE_PROVS = ("新疆", "西藏", "青海", "内蒙", "甘肃", "宁夏", "海南")


def _is_remote_area(order):
    """收货地是偏远省份（运费高、总价排序按默认地址会失真）。"""
    try:
        recv = json.loads(order["receiver_json"] or "{}")
    except Exception:
        recv = {}
    text = (recv.get("prov_name") or "") + (order["buyer_address"] or "")
    return any(p in text for p in REMOTE_PROVS)


def _with_list_filters(url):
    """条目列表页带上默认筛选：总价从低到高 + 八品以上（商品页不适用）。"""
    if "item.kongfz.com" in url and "sortType=" not in url:
        return url + ("&" if "?" in url else "?") + "sortType=7&quality=80~"
    return url


def _with_order_mark(url, order_id):
    """跳转链接带订单号标记：油猴落地页据此识别"这单是网站跳来的"（kongfz 忽略未知参数）。"""
    if "kongfz.com" in url:
        return url + ("&" if "?" in url else "?") + f"xsb_oid={order_id}"
    return url


def unbind_kongfz_order(order_id):
    """取消绑定：清掉单号/成本/利润回到待采购，保留货源链接（便于重新绑定）。"""
    order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        raise ValueError("订单不存在")
    if order["status"] != "purchasing":
        raise ValueError(f"当前状态 {order['status']} 不能取消绑定")
    models.execute(
        "UPDATE orders SET kongfz_order_no=NULL, kongfz_price=NULL, profit=NULL,"
        " status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (order_id,),
    )
    models.execute(
        "DELETE FROM kongfz_orders WHERE order_id=? AND status='monitoring'",
        (order_id,),
    )


def count_by_status():
    rows = models.query("SELECT status, COUNT(*) c FROM orders GROUP BY status")
    counts = {r["status"]: r["c"] for r in rows}
    pending = models.query("SELECT * FROM orders WHERE status='pending'")
    counts["need_confirm"] = len(unbound_candidates_for(pending))
    return counts


def monitoring_kongfz_orders():
    return models.query(
        "SELECT k.*, o.order_no AS xianyu_order_no FROM kongfz_orders k"
        " JOIN orders o ON o.id = k.order_id WHERE k.status='monitoring'"
        " AND o.status IN ('pending','purchasing','shipped','completed')"
    )


def on_kongfz_shipped(kongfz_order_no, waybill_no, express_name):
    """孔夫子监控发现发货 -> 回填闲鱼。返回关联的本地订单 id。

    发货前硬闸：孔夫子单的收货手机号必须与闲鱼订单收货人一致，
    不一致就停手（绝不把别人的运单回填给这个买家），订单标红警。
    """
    rows = models.query("SELECT * FROM kongfz_orders WHERE kongfz_order_no=?",
                        (kongfz_order_no,))
    if not rows:
        return None
    if len({r["order_id"] for r in rows}) != 1:
        log.warning("采购单 %s 关联多个销售订单，暂停自动回填", kongfz_order_no)
        return None
    row = next((r for r in rows if r["status"] == "monitoring"), None)
    if row is None:
        return None
    order = models.query("SELECT * FROM orders WHERE id=?",
                         (row["order_id"],), one=True)
    if not order or order["status"] not in (
            "pending", "purchasing", "shipped", "completed"):
        return None
    scan = models.query(
        "SELECT recv_phone,recv_name FROM kongfz_scanned"
        " WHERE kongfz_order_no=?", (kongfz_order_no,), one=True)
    local_phone = _receiver_phone(order)
    scan_phone = (scan["recv_phone"] or "").strip() if scan else ""
    if not local_phone or not scan_phone:
        msg = f"孔夫子单{kongfz_order_no}缺少双方收货手机号，已暂停自动回填，等待信息补齐"
    elif local_phone != scan_phone:
        msg = f"孔夫子单{kongfz_order_no}收货手机号与本订单不符，已暂停自动回填"
    else:
        msg = ""
    if msg:
        models.execute("UPDATE orders SET alert=? WHERE id=?", (msg, row["order_id"]))
        log.warning("订单 %s %s", row["order_id"], msg)
        return None
    mark_shipped(row["order_id"], waybill_no, express_name,
                 auto_fill_xianyu=True, kongfz_order_no=kongfz_order_no)
    return row["order_id"]


def _receiver_phone(order):
    """从订单 receiver_json 里取手机号。"""
    import json as _json
    try:
        return (_json.loads(order["receiver_json"] or "{}")
                .get("receiver_mobile") or "").strip()
    except Exception:
        return ""


def normalize_receiver(recv):
    """收货人名规范化（只改内存里的 dict，不动数据库）：
    孔夫子收货人要求 2-30 字，闲鱼偶尔给单字/掩码名（"王"、"王*"）。
    规则：≥2字原样；1字→先查同手机号历史订单里有没有全名，
    没有就中性加"先生"（称谓错了不碍事，快递只看手机+地址）。"""
    recv = dict(recv or {})
    name = (recv.get("receiver_name") or "").strip().replace("*", "")
    if len(name) >= 2:
        recv["receiver_name"] = name
        return recv
    phone = (recv.get("receiver_mobile") or "").strip()
    best = ""
    if phone:
        for r in models.query(
                "SELECT receiver_json FROM orders WHERE receiver_json LIKE ?",
                (f"%{phone}%",)):
            try:
                n = (json.loads(r["receiver_json"] or "{}")
                     .get("receiver_name") or "").strip().replace("*", "")
            except Exception:
                n = ""
            if 2 <= len(n) <= 30 and len(n) > len(best):
                best = n
    if best:
        recv["receiver_name"] = best
    elif name:
        recv["receiver_name"] = name + "先生"
    return recv


def unbound_candidates_for(orders):
    """订单页勾选绑定：每个待采购/采购中订单，列手机号一致的未绑孔夫子单。
    只给"还在运输途中"的近期单（历史完成单不该是新单的货源）。
    返回 {order_id: [{no,title,total,status,date,recv_name,name_match}]}。"""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    out = {}
    for o in orders:
        if o["status"] not in ("pending", "purchasing"):
            continue
        # 已绑过单的不再显示候选框，避免误勾同手机号的其它在途单；
        # 一单多本时改用"孔夫子单号"输入框手动加绑
        if o["kongfz_order_no"] or models.query(
                "SELECT 1 FROM kongfz_orders WHERE order_id=? LIMIT 1",
                (o["id"],), one=True):
            continue
        phone = _receiver_phone(o)
        if not phone:
            continue
        rows = models.query(
            "SELECT * FROM kongfz_scanned WHERE recv_phone=?"
            " AND status IN ('等待卖家发货','待收货') AND order_date>=?"
            " AND kongfz_order_no NOT IN"
            " (SELECT kongfz_order_no FROM kongfz_orders)",
            (phone, cutoff))
        if not rows:
            continue
        import json as _json
        try:
            rname = (_json.loads(o["receiver_json"] or "{}")
                     .get("receiver_name") or "")
        except Exception:
            rname = ""
        n = lambda s: (s or "").strip()
        out[o["id"]] = [
            {**dict(r),
             "name_match": n(r["recv_name"]) == n(rname)}
            for r in rows]
    return out


def bind_picked(order_id, nos):
    """勾选绑定：逐个把 kongfz_scanned 里的单绑到订单上，成本取实付价。"""
    bound, errs = [], []
    for no in nos:
        scan = models.query(
            "SELECT total,title,recv_phone FROM kongfz_scanned"
            " WHERE kongfz_order_no=?", (no,), one=True)
        try:
            bind_kongfz_order(order_id, no,
                              scan["total"] if scan else None, amount_basis="list_total")
            bound.append(no)
            _title_alert_if_off(order_id, scan["title"] if scan else "")
        except Exception as e:
            errs.append(f"{no}: {e}")
    return bound, errs


def record_unpaid_report(phone, name="", addr="", title="",
                         amount=None, kf_no="", ts=""):
    """油猴上报：孔夫子点了"提交订单"（未付款）。按收货手机号找到待采购订单，
    记一条"已下单待付款"提示（不进 kongfz_orders，付款后才算正式绑定）。
    有单号时同时 upsert 进 kongfz_scanned，监控下一轮会刷新它的真实状态。
    返回匹配到的订单 id 列表。"""
    from datetime import datetime
    phone = (phone or "").strip()
    if not phone:
        return []
    if kf_no:
        models.execute(
            "INSERT INTO kongfz_scanned(kongfz_order_no,title,total,status,"
            " order_date,recv_name,recv_phone,recv_addr,scanned_at)"
            " VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"
            " ON CONFLICT(kongfz_order_no) DO UPDATE SET title=excluded.title,"
            " total=excluded.total,recv_name=excluded.recv_name,"
            " recv_phone=excluded.recv_phone,recv_addr=excluded.recv_addr,"
            " scanned_at=CURRENT_TIMESTAMP",
            (kf_no, title, amount, "待付款",
             datetime.now().strftime("%Y-%m-%d"), name, phone, addr))
    rows = models.query(
        "SELECT id,unpaid_hint FROM orders"
        " WHERE receiver_json LIKE ? AND status='pending'",
        (f"%{phone}%",))
    matched = []
    for o in rows:
        try:
            items = json.loads(o["unpaid_hint"] or "[]")
        except Exception:
            items = []
        # 同一单重复上报（点击时一次、跳页后补单号一次）按 单号/书名+金额 去重：
        # 已存在就合并（补上后到的单号），不存在才新增
        dup = next((it for it in items if
                    (kf_no and it.get("kf_no") == kf_no)
                    or (_norm(it.get("title")) == _norm(title)
                        and abs((it.get("amount") or 0) - (amount or 0)) < 0.01)),
                   None)
        if dup:
            if kf_no:
                dup["kf_no"] = kf_no
            dup["ts"] = ts or datetime.now().isoformat(timespec="seconds")
        else:
            items.append({"title": title, "amount": amount, "name": name,
                          "kf_no": kf_no,
                          "ts": ts or datetime.now().isoformat(timespec="seconds")})
        models.execute("UPDATE orders SET unpaid_hint=? WHERE id=?",
                       (json.dumps(items, ensure_ascii=False), o["id"]))
        matched.append(o["id"])
    return matched


def toggle_unpaid_manual(order_id, on=None):
    """手动标记/取消"孔夫子已下单待付款"（脚本/监控都没报上时的兜底）。
    on=None 取反。返回新状态 True=已标记。"""
    from datetime import datetime
    o = models.query(
        "SELECT unpaid_hint FROM orders WHERE id=? AND status='pending'",
        (order_id,), one=True)
    if not o:
        raise ValueError("只有待采购订单能手动标记")
    try:
        items = json.loads(o["unpaid_hint"] or "[]")
    except Exception:
        items = []
    marked = any(it.get("manual") for it in items)
    want = (not marked) if on is None else bool(on)
    items = [it for it in items if not it.get("manual")]
    if want:
        items.append({"manual": True,
                      "ts": datetime.now().isoformat(timespec="seconds")})
    models.execute(
        "UPDATE orders SET unpaid_hint=? WHERE id=?",
        (json.dumps(items, ensure_ascii=False) if items else None, order_id))
    return want


def unpaid_hints_for(orders):
    """订单页"孔夫子已下单待付款"灰色提示：合并监控扫到的待付款单 + 油猴上报。
    返回 {order_id: [{title,amount,kf_no,name,name_match}]}。"""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    last_scan = models.get_setting("kongfz_last_scan") or ""
    out = {}
    for o in orders:
        if o["status"] != "pending":
            continue
        phone = _receiver_phone(o)
        if not phone:
            continue
        items, seen_no, seen_ta = [], set(), set()
        for r in models.query(
                "SELECT * FROM kongfz_scanned WHERE recv_phone=?"
                " AND status='待付款' AND order_date>=?"
                " AND kongfz_order_no NOT IN"
                " (SELECT kongfz_order_no FROM kongfz_orders)",
                (phone, cutoff)):
            items.append({"title": r["title"], "amount": r["total"],
                          "kf_no": r["kongfz_order_no"], "name": r["recv_name"]})
            seen_no.add(r["kongfz_order_no"])
            seen_ta.add((_norm(r["title"]), round(r["total"] or 0, 2)))
        try:
            reps = json.loads(o["unpaid_hint"] or "[]")
        except Exception:
            reps = []
        for it in reps:
            if it.get("manual"):
                # 手动标记不受扫描过期影响——用户自己标的，只有手动取消/绑定才清
                items.append({"manual": True})
                continue
            if (it.get("kf_no") or "") in seen_no:
                continue            # 同一单监控已收录，以扫描行显示
            key = (_norm(it.get("title")), round(it.get("amount") or 0, 2))
            if key in seen_ta:
                continue            # 无单号上报与扫描行撞上同一本，去重
            if last_scan and it.get("ts") and it["ts"] < last_scan:
                continue            # 上报后又扫过一轮却没这单 → 已被取消/失效
            items.append({"title": it.get("title"), "amount": it.get("amount"),
                          "kf_no": it.get("kf_no") or "",
                          "name": it.get("name") or ""})
        if not items:
            continue
        try:
            rname = (json.loads(o["receiver_json"] or "{}")
                     .get("receiver_name") or "")
        except Exception:
            rname = ""
        for it in items:
            n = lambda s: (s or "").strip()
            it["name_match"] = not it.get("name") or n(it["name"]) == n(rname)
        out[o["id"]] = items
    return out


def _norm(s):
    import re as _re
    s = _re.sub(r"[^0-9A-Za-z一-鿿]+", "", (s or "")).lower()
    # 版次/年份尾巴不算匹配依据：「2023年版」「第3版」「修订版」这类
    # 两本不同教材常共享，会造成 LCS 误判同书
    s = _re.sub(r"20\d{2}年?(新|修订|增订|最新)?版?", "", s)
    s = _re.sub(r"(最新|修订|增订|新版|纪念|珍藏|精装|平装)?版$", "", s)
    s = _re.sub(r"第\d+版", "", s)
    return s


def _lcs_len(a, b):
    """最长公共子串长度。"""
    if len(a) > len(b):
        a, b = b, a
    prev, best = [0] * (len(a) + 1), 0
    for ch in b:
        cur = [0]
        for i, ca in enumerate(a):
            v = prev[i] + 1 if ca == ch else 0
            cur.append(v)
            if v > best:
                best = v
        prev = cur
    return best


def title_hit(kf_title, our_title):
    """同一本书判定：归一化后互相包含，或最长公共子串 >= 5 字；
    书名本身不足 5 字时要求较短标题整本出现（如《我们仨》）。
    比字符重合率抗噪：孔夫子标题常带作者/ISBN/副标题会稀释比例。"""
    a, b = _norm(kf_title), _norm(our_title)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return _lcs_len(a, b) >= min(5, len(a), len(b))


def _title_alert_if_off(order_id, kf_title):
    """手动绑的孔夫子单书名跟闲鱼标题完全不像 → 标黄疑似买错书。"""
    order = models.query("SELECT product_title FROM orders WHERE id=?",
                         (order_id,), one=True)
    if not order or not kf_title:
        return
    hit = title_hit(kf_title, order["product_title"])
    if not hit:
        models.execute(
            "UPDATE orders SET alert=? WHERE id=?",
            (f"孔夫子单书名「{kf_title}」与本单商品不符，请核对是否买错书",
             order_id))


def touch_kongfz_check(kongfz_order_no):
    models.execute(
        "UPDATE kongfz_orders SET last_check_time=CURRENT_TIMESTAMP,"
        " updated_at=CURRENT_TIMESTAMP WHERE kongfz_order_no=? AND status='monitoring'",
        (kongfz_order_no,),
    )


# ---------- 演示模式数据 ----------

def seed_demo_orders():
    if models.query("SELECT id FROM orders LIMIT 1"):
        return
    demo = [
        ("XY20260930001", "shop_demo_a", "张**", "浙江省杭州市西湖区文三路 100 号",
         "偷影子的人", 29.90, "pending", None, None),
        ("XY20260930002", "shop_demo_a", "李**", "江苏省南京市鼓楼区中山路 20 号",
         "活着（余华）", 35.00, "purchasing", "KF20260930001", 18.00),
        ("XY20260929001", "shop_demo_b", "王**", "广东省深圳市南山区科技园 5 栋",
         "百年孤独", 42.00, "shipped", "KF20260929001", 25.00),
        ("XY20260928001", "shop_demo_b", "陈**", "北京市朝阳区望京 SOHO",
         "人类简史", 56.00, "refunding", "KF20260928001", 33.00),
        ("XY20260927001", "shop_demo_a", "刘**", "四川省成都市武侯区",
         "平凡的世界（全三册）", 68.00, "completed", "KF20260927001", 45.00),
    ]
    for no, sid, buyer, addr, title, price, status, kf_no, kf_price in demo:
        oid = models.execute(
            "INSERT INTO orders(order_no,seller_id,buyer_name,buyer_address,"
            "product_title,sale_price,status,kongfz_order_no,kongfz_price,order_time)"
            " VALUES(?,?,?,?,?,?,?,?,?,datetime('now','-2 days'))",
            (no, sid, buyer, addr, title, price, status, kf_no, kf_price),
        )
        if kf_no:
            kf_status = "shipped" if status in ("shipped", "completed") else "monitoring"
            models.execute(
                "INSERT INTO kongfz_orders(order_id,kongfz_order_no,status,waybill_no,"
                "express_name) VALUES(?,?,?,?,?)",
                (oid, kf_no, kf_status,
                 "SF1234567890" if kf_status == "shipped" else None,
                 "顺丰速运" if kf_status == "shipped" else None),
            )
            if kf_status == "shipped":
                models.execute(
                    "UPDATE orders SET waybill_no='SF1234567890',"
                    " express_name='顺丰速运', ship_time=datetime('now','-1 day')"
                    " WHERE id=?", (oid,))
        _recalc_profit(oid)
    for sid, name in (("shop_demo_a", "演示店铺A"), ("shop_demo_b", "演示店铺B")):
        models.execute(
            "INSERT OR IGNORE INTO xianyu_accounts(seller_id,shop_name) VALUES(?,?)",
            (sid, name),
        )
