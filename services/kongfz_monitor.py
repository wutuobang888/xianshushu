# -*- coding: utf-8 -*-
"""孔夫子订单监控：定时查发货状态，发货后自动回填闲鱼；并自动绑定新下单的孔夫子订单。"""
import logging
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta

import models
from services import order_service
from bots import kongfz_bot

log = logging.getLogger(__name__)

_lock = threading.Lock()

# 可自动绑定的孔夫子订单状态（已付款的）；待付款/取消/退款的不绑
_BINDABLE = ("等待卖家发货", "待收货", "成功完成")


def check_all_once():
    """轮一轮所有 monitoring 状态的孔夫子订单。供 APScheduler / 手动触发复用。"""
    if not _lock.acquire(blocking=False):
        log.info("上一轮孔夫子检查还没结束，跳过本轮")
        return []
    try:
        rows = order_service.monitoring_kongfz_orders()
        done = []
        for row in rows:
            no = row["kongfz_order_no"]
            try:
                res = kongfz_bot.check_order(no)
            except Exception as e:
                log.warning("查询孔夫子订单 %s 失败：%s", no, e)
                continue
            if "未登录" in (res.get("detail") or ""):
                models.set_setting("kongfz_login_dead", "1")
                log.warning("孔夫子登录态失效，本轮监控中止")
                return done
            if not res.get("found"):
                log.warning("孔夫子订单 %s 本次未取得有效详情，未推进成功检查时间",no)
                continue
            models.set_setting("kongfz_login_dead", "")
            order_service.touch_kongfz_check(no)
            if res.get("shipped") and res.get("waybill_no"):
                try:
                    oid = order_service.on_kongfz_shipped(
                        no, res["waybill_no"], res.get("express_name") or "")
                except Exception as e:
                    # 一单回填失败（快递码不认/闲管家接口错）只标该单，
                    # 不能拖垮整轮监控
                    log.warning("孔夫子订单 %s 回填失败：%s", no, e)
                    row = models.query(
                        "SELECT order_id FROM kongfz_orders"
                        " WHERE kongfz_order_no=? AND status='monitoring'",
                        (no,), one=True)
                    if row:
                        models.execute(
                            "UPDATE orders SET alert=? WHERE id=?",
                            (f"发货回填失败：{e}（运单 {res['waybill_no']}，请手动发货）",
                             row["order_id"]))
                    continue
                if oid is None:
                    continue  # 校验未通过不能记为成功，也不加入本轮完成列表
                log.info("孔夫子订单 %s 已发货，运单 %s -> 本地订单 %s",
                         no, res["waybill_no"], oid)
                done.append((no, res["waybill_no"]))
        try:
            bound = auto_bind_once()
            if bound:
                log.info("自动绑定孔夫子订单 %d 笔：%s", len(bound), bound)
        except Exception as e:
            log.warning("孔夫子订单自动绑定失败：%s", e)
        return done
    finally:
        _lock.release()


def _norm_title(t):
    return re.sub(r"[^0-9A-Za-z一-鿿]+", "", (t or "")).lower()


def _title_hit(kf_title, our_title):
    """书名判定统一走 order_service.title_hit（最长公共子串>=5字）。"""
    return order_service.title_hit(kf_title, our_title)


def _parse_receiver(text):
    """列表的收货人字段：姓名，电话，完整地址。"""
    m = re.search(r"([^,，\d]*)[,，]?\s*(\d{7,})[,，]\s*(.*)", text or "")
    return tuple(s.strip() for s in m.groups()) if m else ("", "", "")


def _address_key(text):
    """只统一全半角、空白和标点，不模糊匹配楼栋/门牌号。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    # 仅统一自治区的标准全称/简称；后续城市、街道和门牌仍逐字匹配。
    for full,short in (("广西壮族自治区","广西"),("内蒙古自治区","内蒙古"),
                       ("宁夏回族自治区","宁夏"),("新疆维吾尔自治区","新疆"),("西藏自治区","西藏")):
        if text.startswith(full):
            text = short + text[len(full):]
            break
    return re.sub(r"[\s,，。:：;；()]+", "", text)


def _receiver_hit(scanned, order):
    """姓名、电话、完整地址三者一致才进入自动绑定候选。"""
    name, phone, address = _parse_receiver(scanned.get("receiver"))
    try:
        receiver = json.loads(order["receiver_json"] or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(receiver, dict):
        return False
    if not phone or phone != (receiver.get("receiver_mobile") or "").strip():
        return False
    receiver = order_service.normalize_receiver(receiver)
    if not name or name != (receiver.get("receiver_name") or "").strip():
        return False
    if not address or not receiver.get("address"):
        return False
    parts = []
    for key in ("prov_name", "city_name", "area_name", "town_name", "address"):
        part = _address_key(receiver.get(key))
        if not part:
            continue
        # 直辖市的「天津 + 天津市」合并为「天津市」；详址已含地区时不再重复。
        if parts and part.startswith("".join(parts)):
            parts = [part]
        elif parts and part.startswith(parts[-1]):
            parts[-1] = part
        else:
            parts.append(part)
    return _address_key(address) == "".join(parts)


def _full_scan_due():
    """全量复核每24小时一次；失败或时间记录异常时保持待复核。"""
    try:
        last = datetime.fromisoformat(models.get_setting("kongfz_last_full_scan"))
        elapsed = datetime.now() - last
        return elapsed < timedelta(0) or elapsed >= timedelta(days=1)
    except (ValueError, TypeError):
        return True


def auto_bind_once(force_full=False):
    """日常只扫交易中订单；首次和每24小时全量复核，已绑单不重复计成本。"""
    full_scan = force_full or _full_scan_due()
    scanned = kongfz_bot.list_buyer_orders(active_only=not full_scan)
    if scanned.get("error") or scanned.get("complete") is not True:
        error = scanned.get("error") or "订单列表尚未完整扫描"
        if "未登录" in error:
            models.set_setting("kongfz_login_dead", "1")
        log.warning("孔夫子订单列表扫描失败：%s", error)
        return []
    bound = []
    known_bindings = {r["kongfz_order_no"] for r in models.query(
        "SELECT kongfz_order_no FROM kongfz_orders")}
    cutoff = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    for s in scanned["orders"]:
        # 实际扫到才更新状态：取消/退款也更新，防止旧待付款提示残留。
        # 未扫到不能说明订单已消失，保留缓存供后续扫描校验。
        if s["status"]:
            _cache_unbound(s)
        if s["status"] not in _BINDABLE:
            continue
        if s["date"] and s["date"] < cutoff:
            continue          # 老订单不碰，防历史单误绑到同买家新单
        if s["no"] in known_bindings:
            continue          # 已绑过/已监控
        _, phone, _ = _parse_receiver(s["receiver"])
        if not phone:
            continue
        rows = models.query(
            "SELECT id,product_title,receiver_json FROM orders"
            " WHERE receiver_json LIKE ? AND status IN ('pending','purchasing')",
            (f"%{phone}%",))
        rows = [r for r in rows if _receiver_hit(s, r)]
        if len(rows) > 1:
            rows = [r for r in rows if _title_hit(s["title"], r["product_title"])]
        if len(rows) != 1:
            if len(rows) > 1:
                log.warning("孔夫子单 %s 匹配到 %d 个本地订单，跳过自动绑定",
                            s["no"], len(rows))
            else:
                pass            # 已在上面缓存进候选表
            continue
        try:
            order_service.bind_kongfz_order(rows[0]["id"], s["no"], s["total"], amount_basis="list_total")
        except Exception as e:
            log.warning("孔夫子单 %s 自动绑定失败：%s", s["no"], e)
            continue
        bound.append((s["no"], rows[0]["id"]))
        known_bindings.add(s["no"])
        log.info("孔夫子单 %s 自动绑定本地订单 %s（实付¥%s）",
                 s["no"], rows[0]["id"], s["total"])
    stamp = datetime.now().isoformat(timespec="seconds")
    models.set_setting("kongfz_last_scan", stamp)
    if full_scan:
        models.set_setting("kongfz_last_full_scan", stamp)
    log.info("孔夫子%s扫描完成：%d页、%d单，新增绑定%d笔",
             "全量复核" if full_scan else "交易中订单",
             scanned.get("pages", 0), len(scanned["orders"]), len(bound))
    return bound


def _cache_unbound(s):
    """按单号更新实际扫到的孔夫子订单及其状态。"""
    name, phone, address = _parse_receiver(s["receiver"])
    models.execute(
        "INSERT INTO kongfz_scanned(kongfz_order_no,title,total,status,"
        " order_date,recv_name,recv_phone,recv_addr,scanned_at)"
        " VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"
        " ON CONFLICT(kongfz_order_no) DO UPDATE SET title=excluded.title,"
        " total=excluded.total,status=excluded.status,"
        " order_date=excluded.order_date,recv_name=excluded.recv_name,"
        " recv_phone=excluded.recv_phone,recv_addr=excluded.recv_addr,"
        " scanned_at=CURRENT_TIMESTAMP"
        " WHERE kongfz_scanned.title IS NOT excluded.title"
        " OR kongfz_scanned.total IS NOT excluded.total"
        " OR kongfz_scanned.status IS NOT excluded.status"
        " OR kongfz_scanned.order_date IS NOT excluded.order_date"
        " OR kongfz_scanned.recv_name IS NOT excluded.recv_name"
        " OR kongfz_scanned.recv_phone IS NOT excluded.recv_phone"
        " OR kongfz_scanned.recv_addr IS NOT excluded.recv_addr",
        (s["no"], s["title"], s["total"], s["status"], s["date"],
         name, phone, address or s["receiver"]),
    )


def pending_checks():
    return models.query(
        "SELECT k.*, o.order_no AS xianyu_order_no FROM kongfz_orders k"
        " JOIN orders o ON o.id=k.order_id ORDER BY k.id DESC LIMIT 200")
