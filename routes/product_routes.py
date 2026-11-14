# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from uuid import uuid4
import json

import models
from services import product_service, template_service, image_service, \
    hotlist_service
from bots import kongfz_bot

bp = Blueprint("products", __name__)


@bp.route("/publish")
def publish_page():
    shop = request.args.get("shop", "")
    accounts = product_service.list_xianyu_accounts()
    products = product_service.list_products(shop=shop or None)
    pools = {}       # 草稿行的图池（拍平成单图列表，上架表单勾图用）
    for p in products:
        if p["status"] == "draft" and p["kongfz_url"]:
            pools[p["id"]] = [
                im for g in image_service.list_pool(p["kongfz_url"])
                for im in g["images"]]
            saved = models.get_setting(f"product_image_order:{p['id']}")
            if saved:
                order = json.loads(saved)
                position = {iid: i for i, iid in enumerate(order)}
                for im in pools[p["id"]]:
                    im["selected"] = im["id"] in position
                pools[p["id"]].sort(key=lambda im: position.get(im["id"], len(order)))
    return render_template(
        "publish.html",
        submission_token=str(uuid4()),
        tpl_title=template_service.list_templates("title"),
        tpl_desc=template_service.list_templates("description"),
        accounts=accounts,
        products=products,
        pools=pools,
        shop=shop,
        shop_counts=product_service.product_shop_counts(),
        shop_map={a["seller_id"]: a["shop_name"] or a["seller_id"]
                  for a in accounts},
        shop_color={a["seller_id"]: c for a, c in zip(
            accounts, ("primary", "success", "info", "warning") * 2)},
    )


@bp.route("/shop_products")
def shop_products():
    """店铺商品：按店分组的货架视图。

    默认每店只显示前 OVERVIEW_ROWS 行（防长页）；
    ?shop=<sid>&page=N 单店视图，PAGE_SIZE 条/页翻页。"""
    OVERVIEW_ROWS, PAGE_SIZE = 15, 30
    shop = request.args.get("shop", "")
    page = max(int(request.args.get("page", 1) or 1), 1)
    accounts = product_service.list_xianyu_accounts()
    groups, drafts = product_service.shop_product_groups()
    full_sizes = {sid: len(items) for sid, items in groups.items()}

    if shop:                       # 单店视图：分页
        all_items = groups.get(shop, [])
        total = len(all_items)
        groups = {shop: all_items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]}
        drafts = []
    else:                          # 概览：每店截断
        total = 0
        groups = {sid: items[:OVERVIEW_ROWS] for sid, items in groups.items()}

    pools = {}
    for items in list(groups.values()) + [drafts]:
        for p in items:
            if p["kongfz_url"]:
                pools[p["id"]] = [
                    im for g in image_service.list_pool(p["kongfz_url"])
                    for im in g["images"]]
    return render_template(
        "shop_products.html",
        accounts=accounts,
        groups=groups,
        drafts=drafts,
        pools=pools,
        shop=shop, page=page, page_size=PAGE_SIZE,
        overview_rows=OVERVIEW_ROWS, full_sizes=full_sizes, total=total,
        tpl_title=template_service.list_templates("title"),
        tpl_desc=template_service.list_templates("description"),
        shop_map={a["seller_id"]: a["shop_name"] or a["seller_id"]
                  for a in accounts},
        shop_color={a["seller_id"]: c for a, c in zip(
            accounts, ("primary", "success", "info", "warning") * 2)},
    )


@bp.route("/products/<int:pid>/publish_to", methods=["POST"])
def publish_to(pid):
    """挂到另一店：同品换店再上架，可覆盖模板/售价/图片。"""
    img_ids = [int(x) for x in request.form.getlist("img_ids")
               if x.strip().isdigit()] or None
    try:
        title = product_service.publish_product(
            pid,
            seller_id=request.form.get("seller_id") or None,
            title_template_id=_i(request.form.get("title_template_id")),
            desc_template_id=_i(request.form.get("desc_template_id")),
            sale_price=_f(request.form.get("sale_price")),
            image_ids=img_ids)
        flash(f"上架操作已提交：{title}，状态以店铺商品页的核对结果为准", "success")
    except Exception as e:
        flash(f"上架失败：{e}", "danger")
    if request.form.get("back") == "publish":
        return redirect(url_for("products.publish_page"))
    return redirect(url_for("products.shop_products"))


@bp.route("/hotlist")
def hotlist():
    """热书榜：多源榜单跨源去重，标注在售状态。支持榜源筛选/只看未上架/翻页。"""
    source = request.args.get("source", "").strip()
    unlisted = request.args.get("unlisted", "")
    page = max(1, int(request.args.get("page") or 1))
    per_page = 30
    rows, sources = hotlist_service.list_latest(
        source=source or None, only_unlisted=bool(unlisted))
    pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = min(page, pages)
    accounts = product_service.list_xianyu_accounts()
    return render_template(
        "hotlist.html",
        rows=rows[(page - 1) * per_page:page * per_page],
        total=len(rows), page=page, pages=pages,
        source=source, sources=sources, unlisted=unlisted,
        shop_map={a["seller_id"]: a["shop_name"] or a["seller_id"]
                  for a in accounts},
        shop_color={a["seller_id"]: c for a, c in zip(
            accounts, ("primary", "success", "info", "warning") * 2)},
        fetched_at=rows[0]["fetched_date"] if rows else "",
    )


@bp.route("/hotlist/refresh", methods=["POST"])
def hotlist_refresh():
    try:
        stats = hotlist_service.refresh()
        flash("榜单已刷新：" + " / ".join(
            f"{s} {n}条" for s, n in stats), "success")
    except Exception as e:
        flash(f"刷新失败：{e}", "danger")
    return redirect(url_for("products.hotlist"))


@bp.route("/api/kongfz/search")
def search():
    kw = request.args.get("key", "").strip()
    try:
        books = kongfz_bot.search_entries(kw)
        try:
            product_service.cache_kongfz_results(books)
        except Exception:
            pass  # 入库失败不影响搜索本身
        return jsonify({"ok": True, "items": books})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/scripts/<path:fname>")
def userscript(fname):
    """吐出油猴脚本本体：浏览器打开 .user.js URL 会触发油猴安装页。"""
    from flask import send_file
    if not fname.endswith(".user.js") or "/" in fname or ".." in fname:
        return "not found", 404
    return send_file(f"scripts/{fname}", mimetype="text/javascript")


# ---------- 图片候选池 ----------

@bp.route("/api/images/fetch", methods=["POST"])
def fetch_images():
    """条目页抓卖家实拍图：开 Edge 逐店进商品页，约 30~60 秒。

    每次自动跳过图池里已抓过的店（"换一批"效果），抓完返回新增店数。"""
    d = request.get_json(force=True, silent=True) or {}
    entry_url = (d.get("entry_url") or "").strip()
    if not entry_url:
        return jsonify({"ok": False, "error": "缺 entry_url"}), 400
    known = {r["source_url"] for r in models.query(
        "SELECT DISTINCT source_url FROM image_pool WHERE entry_url=?",
        (entry_url,)) if r["source_url"]}
    try:
        shops = kongfz_bot.fetch_listing_images(entry_url,
                                              skip_urls=known)
        image_service.save_candidates(entry_url, shops)
        return jsonify({"ok": True, "shops": len(shops),
                        "groups": image_service.list_pool(entry_url)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/images/pool")
def image_pool():
    entry_url = request.args.get("entry_url", "")
    return jsonify({"ok": True, "groups": image_service.list_pool(entry_url)})


@bp.route("/pool_img/<int:iid>")
def pool_img(iid):
    """图池本地缓存图（小红书 CDN 链接会过期，收图时已落盘）。"""
    from flask import send_file
    f = image_service.local_image_file(iid)
    if not f:
        return "not found", 404
    return send_file(str(f), mimetype="image/jpeg")


@bp.route("/api/images/select", methods=["POST"])
def select_image():
    d = request.get_json(force=True, silent=True) or {}
    image_service.set_selected(d.get("id"), bool(d.get("selected")))
    return jsonify({"ok": True})


@bp.route("/api/images/<int:iid>", methods=["DELETE"])
def del_image(iid):
    image_service.delete_image(iid)
    return jsonify({"ok": True})


@bp.route("/api/images/dewatermark", methods=["POST"])
def dewatermark():
    d = request.get_json(force=True, silent=True) or {}
    try:
        url = image_service.dewatermark_image(d.get("id"))
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/images/restore", methods=["POST"])
def restore_image():
    d = request.get_json(force=True, silent=True) or {}
    image_service.restore_image(d.get("id"))
    return jsonify({"ok": True})


@bp.route("/api/images/collect_target", methods=["GET", "POST", "DELETE"])
def collect_target():
    """小红书收图模式开关：POST {entry_url} 开，DELETE 关，GET 查当前目标。"""
    try:
        if request.method == "POST":
            d = request.get_json(force=True, silent=True) or {}
            info = image_service.collect_target_info(d.get("entry_url"))
            image_service.set_collect_target(info["target"])
            return jsonify({"ok": True, **info})
        if request.method == "DELETE":
            image_service.set_collect_target("")
        cur = image_service.collect_target()
        return jsonify({"ok": True, "target": cur})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409


@bp.route("/api/images/collect", methods=["POST"])
def collect_images():
    """油猴脚本回传小红书笔记图片 -> 进当前收图目标的图池。"""
    d = request.get_json(force=True, silent=True) or {}
    try:
        info = image_service.collect_target_info(d.get("entry_url"))
        source = d.get("source", "xhs")
        if source not in ("xhs", "kongfz"):
            raise ValueError("不支持的收图来源")
        collector = image_service.collect_kongfz if source == "kongfz" else image_service.collect_xhs
        n = collector(
            d.get("urls") or [], d.get("note_url", ""), d.get("note_title", ""),
            entry_url=info["target"])
        if not n:
            return jsonify({"ok": False, "count": 0, "error": "没有成功缓存图片，请检查商品或笔记图片并重试",
                            **info}), 409
        return jsonify({"ok": True, "count": n, **info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 409


@bp.route("/products/down_non_books", methods=["POST"])
def down_non_books():
    try:
        ok, fails = product_service.down_shelf_non_books()
        msg = f"已下架 {ok} 个非图书商品"
        if fails:
            msg += f"，失败 {len(fails)} 个（{'; '.join(fails[:2])}）"
        flash(msg, "warning" if fails else "success")
    except Exception as e:
        flash(f"下架失败：{e}", "danger")
    return redirect(url_for("products.publish_page"))


@bp.route("/products/sync", methods=["POST"])
def sync_products():
    try:
        n = product_service.sync_xianyu_products()
        flash(f"已核对 {n} 个商品", "success")
    except Exception as e:
        flash(f"同步失败：{e}", "danger")
    return redirect(url_for("products.publish_page"))


@bp.route("/products", methods=["POST"])
def save():
    book = {
        "book_title": request.form.get("book_title", "").strip(),
        "author": request.form.get("author", ""),
        "publisher": request.form.get("publisher", ""),
        "pub_year": request.form.get("pub_year", ""),
        "isbn": request.form.get("isbn", ""),
        "kongfz_url": request.form.get("kongfz_url", ""),
        "kongfz_price": _f(request.form.get("kongfz_price")),
        "image_url": request.form.get("image_url", ""),
    }
    if not book["book_title"]:
        flash("书名不能为空", "danger")
        return redirect(url_for("products.publish_page"))
    try:
        token = request.form.get("submission_token")
        if not token:
            raise ValueError("表单已过期，请刷新页面后重试")
        image_ids = (json.loads(request.form.get("ordered_image_ids", ""))
                     if request.form.get("image_selection_explicit") == "1" else None)
        if request.form.get("image_selection_explicit") == "1":
            image_service.validate_image_ids(book["kongfz_url"], image_ids)
        pid = product_service.save_product(
            book, request.form.get("sale_price") or 0,
            _i(request.form.get("title_template_id")),
            _i(request.form.get("desc_template_id")), submission_token=token, image_ids=image_ids)
    except Exception as e:
        flash(f"保存失败：{e}", "danger")
        return redirect(url_for("products.publish_page"))
    if request.form.get("action") == "publish":
        try:
            title = product_service.publish_product(
                pid, seller_id=request.form.get("seller_id") or None)
            flash(f"上架操作已提交：{title}，状态以店铺商品页的核对结果为准", "success")
        except Exception as e:
            flash(f"上架失败：{e}", "danger")
    else:
        flash("已保存为草稿", "success")
    return redirect(url_for("products.publish_page"))


@bp.route("/products/<int:pid>/down", methods=["POST"])
def down(pid):
    try:
        product_service.down_shelf(pid,
                                   seller_id=request.form.get("seller_id") or None)
        flash("已下架", "success")
    except Exception as e:
        flash(str(e), "danger")
    back = request.form.get("back")
    return redirect(url_for("products.shop_products") if back == "shop"
                    else url_for("products.publish_page"))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
