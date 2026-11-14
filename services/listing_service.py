# -*- coding: utf-8 -*-
"""每店挂售记录、外部操作阶段和结果核对。网络调用不持有数据库事务。"""
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import models
from services import xgj_client as xgj
from services.money import to_fen

BUSY = {"creating", "publishing", "publish_pending", "uncertain_create",
        "uncertain_publish", "uncertain_down", "needs_verify"}


def get(product_id, seller_id):
    return models.query("SELECT * FROM product_listings WHERE product_id=? AND seller_id=?",
                        (product_id, seller_id), one=True)


def _refresh_product(product_id):
    """兼容旧页面的汇总字段；真实远端身份只从挂售记录读取。"""
    rows = models.query("SELECT * FROM product_listings WHERE product_id=? ORDER BY id",
                        (product_id,))
    active = [r for r in rows if r["status"] == "listed"]
    statuses = {r["status"] for r in rows}
    status = "listed" if active else ("draft" if statuses - {"sold", "out_of_stock"}
                                     else "sold" if "sold" in statuses else "out_of_stock")
    models.execute("UPDATE products SET seller_id=?,status=? WHERE id=?",
                   (",".join(r["seller_id"] for r in active), status, product_id))


def _state(listing_id, status, error=None):
    with models.transaction():
        models.execute("UPDATE product_listings SET status=?,last_error=?,"
                       " updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, error, listing_id))
        row = models.query("SELECT product_id FROM product_listings WHERE id=?",
                           (listing_id,), one=True)
        _refresh_product(row["product_id"])


def remote_status(detail, shop):
    """sold为累计销量，不是布尔售罄标记；库存及上下架时间共同判定。"""
    def number(key):
        try:
            return int(detail.get(key) or 0)
        except (ValueError, TypeError):
            return 0
    if "stock" in detail and number("stock") == 0 and number("sold") > 0:
        return "sold"
    if number("offline_time") > number("online_time"):
        return "out_of_stock"
    if shop.get("item_id") and number("online_time") > 0:
        return "listed"
    if not shop.get("item_id"):
        return "created"
    return "needs_verify"


def find_product(remote_id, detail):
    """用操作标识恢复超时创建的归属，不用相似书名推定。"""
    token = detail.get("outer_id") or ""
    if re.fullmatch(r"xsb-[0-9a-f]{20}", str(token)):
        marker = json.dumps({"outer_id": token}, ensure_ascii=False, separators=(",", ":"))[1:-1]
        rows = models.query("SELECT product_id FROM product_listings WHERE payload_json LIKE ?",
                            ("%" + marker + "%",))
        ids = {r["product_id"] for r in rows}
        if len(ids) == 1:
            return ids.pop()
    rows = models.query("SELECT DISTINCT product_id FROM product_listings WHERE remote_product_id=?",
                        (str(remote_id),))
    if len(rows) == 1:
        return rows[0]["product_id"]
    rows = models.query("SELECT id FROM products WHERE xianyu_product_id=?", (str(remote_id),))
    return rows[0]["id"] if len(rows) == 1 else None


def reconcile(product_id, remote_id, detail):
    shops = detail.get("publish_shop") or []
    observed = set()
    with models.transaction():
        for shop in shops:
            sid = str(shop.get("user_name") or "").strip()
            if not sid:
                continue
            observed.add(sid)
            old = get(product_id, sid)
            if old and old["remote_product_id"] and str(old["remote_product_id"]) != str(remote_id):
                # 不覆盖另一个已核实的挂售身份；历史别名才允许重新核对。
                if old["status"] != "needs_verify":
                    continue
            status = remote_status(detail, shop)
            if old and old["status"] in {"creating", "publishing", "uncertain_down"}:
                age = (datetime.now(timezone.utc).replace(tzinfo=None)
                       - datetime.fromisoformat(old["updated_at"])).total_seconds()
                if age < 300:
                    # 保留操作租期，不让同步刷新时间导致崩溃任务永远无法恢复。
                    continue
            if old and old["status"] == "publish_pending" and status == "created":
                status = old["status"]
            raw_price = shop.get("price", detail.get("price"))
            price = to_fen(xgj.pick_price({"price": raw_price}), allow_none=True)
            models.execute(
                "INSERT INTO product_listings(product_id,seller_id,remote_product_id,"
                "remote_item_id,sale_price_fen,status) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(product_id,seller_id) DO UPDATE SET"
                " remote_product_id=excluded.remote_product_id,remote_item_id=excluded.remote_item_id,"
                " sale_price_fen=COALESCE(excluded.sale_price_fen,product_listings.sale_price_fen),"
                " status=excluded.status,last_error=NULL,updated_at=CURRENT_TIMESTAMP",
                (product_id, sid, str(remote_id), str(shop.get("item_id") or "") or None, price, status))
        # 旧的“同品多店”字段可能把最后一次创建的ID分给了另一店，保留并说明。
        for row in models.query("SELECT * FROM product_listings WHERE product_id=?", (product_id,)):
            if (row["status"] == "needs_verify" and row["remote_product_id"] == str(remote_id)
                    and row["seller_id"] not in observed):
                models.execute("UPDATE product_listings SET last_error=? WHERE id=?",
                               ("远端商品未包含该店铺，历史归属需要核对", row["id"]))
        _refresh_product(product_id)


def publish(product_id, seller_id, prepare_payload, client):
    prior = get(product_id, seller_id)
    if prior and (prior["status"] in BUSY or prior["status"] in {"listed", "sold"}):
        raise ValueError("该店商品已在售、已售罄或结果待核对，请先同步商品后再操作")
    resume = prior and prior["remote_product_id"] and prior["status"] in {"created", "out_of_stock"}
    payload = None if resume else prepare_payload()
    with models.transaction():
        current = get(product_id, seller_id)
        if current and (current["status"] in BUSY or current["status"] in {"listed", "sold"}):
            raise ValueError("该店已有操作正在处理或结果待核对，请勿重复提交")
        if current and current["remote_product_id"] and current["status"] in {"created", "out_of_stock"}:
            remote_id = current["remote_product_id"]
            payload = json.loads(current["payload_json"] or "{}")
            listing_id = current["id"]
            models.execute("UPDATE product_listings SET status='publishing',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (listing_id,))
        else:
            if payload is None:
                raise ValueError("挂售状态已变化，请重新打开页面")
            payload = dict(payload)
            payload["outer_id"] = "xsb-" + uuid4().hex[:20]
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            price = payload["publish_shop"][0]["price"]
            models.execute("INSERT INTO product_listings(product_id,seller_id,status,sale_price_fen,payload_json)"
                           " VALUES(?,?,'creating',?,?) ON CONFLICT(product_id,seller_id) DO UPDATE SET"
                           " status='creating',sale_price_fen=excluded.sale_price_fen,"
                           " payload_json=excluded.payload_json,last_error=NULL,updated_at=CURRENT_TIMESTAMP",
                           (product_id, seller_id, price, encoded))
            listing_id = get(product_id, seller_id)["id"]
            remote_id = None
    if not remote_id:
        try:
            created = client.create_product(payload)
            remote_id, _ = xgj.pick_product_ids(created)
            if not remote_id:
                raise xgj.XgjUncertainError("创建结果缺少商品ID，请先同步核对，不能重新创建")
        except Exception as e:
            state = "failed" if isinstance(e, xgj.XgjError) and not isinstance(e, xgj.XgjUncertainError) else "uncertain_create"
            _state(listing_id, state, str(e))
            raise
        # 发布前即保存创建结果；失败后继续使用同一个远端商品。
        models.execute("UPDATE product_listings SET remote_product_id=?,status='publishing',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (remote_id, listing_id))
    try:
        client.publish_product(product_id=remote_id, seller_id=seller_id)
    except Exception as e:
        state = "created" if isinstance(e, xgj.XgjError) and not isinstance(e, xgj.XgjUncertainError) else "uncertain_publish"
        _state(listing_id, state, str(e))
        raise
    _state(listing_id, "publish_pending")
    try:
        reconcile(product_id, remote_id, client.product_detail(remote_id))
    except Exception:
        pass  # 发布请求已受理，读取失败等待下一轮同步核对，不重复发布。
    return payload.get("title") or models.query("SELECT book_title FROM products WHERE id=?",
                                               (product_id,), one=True)["book_title"]


def down(product_id, seller_id, client):
    rows = ([get(product_id, seller_id)] if seller_id else models.query(
        "SELECT * FROM product_listings WHERE product_id=? AND status='listed'", (product_id,)))
    if not rows or any(not r or r["status"] != "listed" or not r["remote_product_id"] for r in rows):
        raise ValueError("该店挂售记录尚未核实或当前不在售，请先同步商品")
    for row in rows:
        d = client.product_detail(row["remote_product_id"])
        sids = {str(s.get("user_name") or "") for s in d.get("publish_shop") or []}
        if sids != {row["seller_id"]}:
            raise ValueError("远端商品同时关联多个店铺或归属变化，暂停下架，避免影响其他店")
    for row in rows:
        with models.transaction():
            fresh = get(product_id, row["seller_id"])
            if not fresh or fresh["status"] != "listed" or fresh["remote_product_id"] != row["remote_product_id"]:
                raise ValueError("挂售状态已变化，请刷新后重试")
            models.execute("UPDATE product_listings SET status='uncertain_down',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        try:
            client.down_shelf(product_id=row["remote_product_id"])
        except Exception as e:
            state = "listed" if isinstance(e, xgj.XgjError) and not isinstance(e, xgj.XgjUncertainError) else "uncertain_down"
            _state(row["id"], state, str(e))
            raise
        _state(row["id"], "out_of_stock")
