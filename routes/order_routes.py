# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import models
from services import order_service

bp = Blueprint("orders", __name__)


@bp.route("/")
@bp.route("/orders")
def index():
    status = request.args.get("status", "")
    keyword = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    size = request.args.get("size", 50, type=int) or 50
    if size not in (30, 50):
        size = 50
    sort = request.args.get("sort", "desc")
    if sort not in ("asc", "desc"):
        sort = "desc"
    orders, total = order_service.list_orders(
        status or None, keyword or None, page=page, size=size, sort=sort)
    pages = max(1, -(-total // size))
    if page > pages:
        return redirect(url_for("orders.index", status=status, q=keyword,
                                page=pages, size=size, sort=sort))
    counts = order_service.count_by_status()
    cands_map = order_service.unbound_candidates_for(orders)
    unpaid_map = order_service.unpaid_hints_for(orders)
    purchase_map = {}
    if orders:
        ids = [o["id"] for o in orders]
        for purchase in models.query("SELECT * FROM kongfz_orders WHERE order_id IN ("
                                     + ",".join("?" for _ in ids) + ") ORDER BY id", ids):
            purchase_map.setdefault(purchase["order_id"], []).append(purchase)
    return render_template("orders.html", orders=orders, counts=counts,
                           status=status, keyword=keyword, cands_map=cands_map,
                           unpaid_map=unpaid_map,
                           purchase_map=purchase_map,
                           page=page, pages=pages, total=total,
                           size=size, sort=sort)


@bp.route("/orders/<int:oid>/bind", methods=["POST"])
def bind(oid):
    kf_no = request.form.get("kongfz_order_no", "").strip()
    kf_price = request.form.get("kongfz_price", "").strip()
    kf_url = request.form.get("kongfz_url", "").strip()
    try:
        order_service.bind_kongfz_order(
            oid, kf_no, float(kf_price) if kf_price else None, kf_url or None)
        flash(f"已绑定孔夫子订单 {kf_no}，进入监控", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("orders.index",
                            status=request.form.get("status", ""))
                    + f"#o{oid}")


@bp.route("/orders/<int:oid>/bind_picked", methods=["POST"])
def bind_picked(oid):
    """勾选绑定：孔夫子扫描缓存里选中的单号逐个绑上来。"""
    nos = request.form.getlist("nos")
    bound, errs = order_service.bind_picked(oid, nos)
    if bound:
        flash(f"已绑定 {len(bound)} 个孔夫子订单：{'、'.join(bound)}", "success")
    for e in errs:
        flash(e, "danger")
    return redirect(url_for("orders.index",
                            status=request.form.get("status", ""))
                    + f"#o{oid}")


@bp.route("/orders/<int:oid>/unbind", methods=["POST"])
def unbind(oid):
    try:
        order_service.unbind_kongfz_order(oid)
        flash("已取消绑定，订单回到待采购", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("orders.index",
                            status=request.form.get("status", ""))
                    + f"#o{oid}")


@bp.route("/orders/<int:oid>/toggle_unpaid", methods=["POST"])
def toggle_unpaid(oid):
    """手动标记/取消"孔夫子已下单待付款"。ajax=1 时返回 JSON 不刷新页面。"""
    try:
        on = order_service.toggle_unpaid_manual(oid)
    except Exception as e:
        if request.form.get("ajax"):
            return jsonify({"ok": False, "error": str(e)}), 400
        flash(str(e), "danger")
        return redirect(url_for("orders.index",
                                status=request.form.get("status", ""))
                        + f"#o{oid}")
    if request.form.get("ajax"):
        order = models.query("SELECT * FROM orders WHERE id=?",
                             (oid,), one=True)
        hints = order_service.unpaid_hints_for([dict(order)])
        return jsonify({"ok": True, "on": on,
                        "hints": hints.get(oid, [])})
    flash("已标记：孔夫子已下单待付款" if on else "已取消待付款标记", "info")
    return redirect(url_for("orders.index",
                            status=request.form.get("status", ""))
                    + f"#o{oid}")


@bp.route("/orders/<int:oid>/ship", methods=["POST"])
def ship(oid):
    waybill = request.form.get("waybill_no", "").strip()
    express = request.form.get("express_name", "").strip()
    try:
        order_service.mark_shipped(oid, waybill, express, auto_fill_xianyu=True)
        flash(f"已发货并回填闲鱼：{waybill}", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("orders.index",
                            status=request.form.get("status", ""))
                    + f"#o{oid}")


@bp.route("/orders/<int:oid>/prepare_source", methods=["POST"])
def prepare_source(oid):
    """点「找货源」时 beacon 过来：后台拉收货详情 + 设为孔夫子填单目标。"""
    order = order_service.prepare_source(oid)
    return jsonify({"ok": bool(order)})


@bp.route("/orders/<int:oid>/fill_checkout", methods=["POST"])
def fill_checkout(oid):
    """拉起孔夫子下单页自动填表（可见浏览器，停手不提交）。"""
    kongfz_url = (request.form.get("kongfz_url") or "").strip() or None
    try:
        import config
        if config.CLOUD_MODE:
            from services import checkout_task
            task = checkout_task.start(oid, kongfz_url, buy_now=True)
            return jsonify(ok=True, task=task, browser_url=checkout_task.BROWSER_URL)
        order_service.start_fill_checkout(oid, kongfz_url)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route('/orders/<int:oid>/cloud_purchase', methods=['POST'])
def cloud_purchase(oid):
    from services import checkout_task
    try:
        task = checkout_task.start(oid)
        return jsonify(ok=True, task=task, browser_url=checkout_task.BROWSER_URL)
    except (ValueError, RuntimeError) as error:
        # 不将平台原始异常或收货信息回传为错误详情。
        return jsonify(ok=False, error=str(error)), 400


@bp.route('/orders/<int:oid>/source')
def source_prepare(oid):
    import config
    if not config.CLOUD_MODE:
        return redirect(url_for('orders.index'))
    # GET 只显示准备页，地址写入由带会话及CSRF的POST执行。
    return render_template('source_prepare.html', order_id=oid)


@bp.route('/orders/<int:oid>/sync_address', methods=['POST'])
def sync_address(oid):
    from services.kongfz_address import prepare, AddressSyncError
    from services.browser_session import BrowserBusyError
    try:
        return jsonify(prepare(oid))
    except (AddressSyncError, BrowserBusyError) as error:
        return jsonify(ok=False, error=str(error)), 400
    except Exception:
        return jsonify(ok=False, error='未能核实地址同步结果，请稍后重试；不要在旧结算页提交'), 400


@bp.route('/orders/<int:oid>/browser_target', methods=['POST'])
def browser_target(oid):
    """地址助手按本单读取：云端要求登录，本地要求同源会话及 CSRF。"""
    from services.checkout_task import fresh_receiver
    order = models.query('SELECT * FROM orders WHERE id=?', (oid,), one=True)
    if not order or order['status'] not in ('pending','purchasing'):
        return jsonify(ok=False, error='这笔订单当前不能采购'), 400
    try:
        receiver = fresh_receiver(order)
    except Exception:
        return jsonify(ok=False, error='未能核实最新收货信息，请稍后重试'), 400
    return jsonify(ok=True, target=dict(order_id=oid, order_no=order['order_no'],
                   product_title=order['product_title'], receiver=receiver))


@bp.route('/orders/cloud_purchase')
def cloud_purchase_status():
    import config
    if not config.CLOUD_MODE:
        return jsonify(ok=False), 404
    from services import checkout_task
    return jsonify(ok=True, task=checkout_task.current(), browser_url=checkout_task.BROWSER_URL)


@bp.route('/orders/cloud_purchase/close', methods=['POST'])
def cloud_purchase_close():
    import config
    if not config.CLOUD_MODE:
        return jsonify(ok=False), 404
    from services import checkout_task
    try:
        checkout_task.close(request.form.get('task_id', ''))
        return jsonify(ok=True)
    except ValueError as error:
        return jsonify(ok=False, error=str(error)), 400


@bp.route("/orders/source_target")
def source_target():
    """孔夫子油猴脚本查：当前待填单的收货信息。"""
    return jsonify({"target": order_service.source_target()})


@bp.route("/api/kongfz/unpaid_report", methods=["POST"])
def kongfz_unpaid_report():
    """油猴上报：孔夫子"提交订单"（未付款）。记"已下单待付款"提示，
    按收货手机号匹配待采购订单。不做绑定——付款后由监控正式绑定。"""
    d = request.get_json(silent=True) or request.form
    try:
        amount = d.get("amount")
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    matched = order_service.record_unpaid_report(
        phone=d.get("phone"), name=d.get("name"), addr=d.get("addr"),
        title=d.get("title"), amount=amount,
        kf_no=(d.get("kf_no") or "").strip(), ts=d.get("ts"))
    return jsonify({"ok": True, "matched": matched})


@bp.route("/orders/sync", methods=["POST"])
def sync():
    try:
        n = order_service.sync_orders()
        flash(f"同步完成，新增 {n} 单", "success")
    except Exception as e:
        flash(f"同步失败：{e}", "danger")
    return redirect(url_for("orders.index"))
