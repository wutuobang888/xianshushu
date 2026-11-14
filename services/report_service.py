# -*- coding: utf-8 -*-
"""利润报表：按天 / 按闲鱼号 / 月度汇总。利润口径 = sale_price - kongfz_price。"""
import models

# 计入报表的状态：已发货/已完成/采购中（已付成本）。pending/退款不计。
VALID = ("shipped", "completed", "purchasing")


def _in_clause():
    return ",".join("?" * len(VALID))


def daily_summary(date_from=None, date_to=None):
    sql = (
        "SELECT COALESCE(date(order_time), date(created_at)) d, COUNT(*) cnt,"
        " ROUND(SUM(sale_price),2) sales, ROUND(SUM(kongfz_price),2) cost,"
        " ROUND(SUM(profit),2) profit"
        f" FROM orders WHERE status IN ({_in_clause()})"
    )
    params = list(VALID)
    if date_from:
        sql += " AND COALESCE(date(order_time), date(created_at)) >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND COALESCE(date(order_time), date(created_at)) <= ?"
        params.append(date_to)
    sql += " GROUP BY d ORDER BY d DESC LIMIT 90"
    return models.query(sql, params)


def account_summary(date_from=None, date_to=None):
    sql = (
        "SELECT COALESCE(o.seller_id,'未分配') seller, a.shop_name, COUNT(*) cnt,"
        " ROUND(SUM(o.sale_price),2) sales, ROUND(SUM(o.kongfz_price),2) cost,"
        " ROUND(SUM(o.profit),2) profit"
        " FROM orders o LEFT JOIN xianyu_accounts a ON a.seller_id = o.seller_id"
        f" WHERE o.status IN ({_in_clause()})"
    )
    params = list(VALID)
    if date_from:
        sql += " AND COALESCE(date(o.order_time), date(o.created_at)) >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND COALESCE(date(o.order_time), date(o.created_at)) <= ?"
        params.append(date_to)
    sql += " GROUP BY seller ORDER BY profit DESC"
    return models.query(sql, params)


def totals(date_from=None, date_to=None):
    sql = (
        "SELECT COUNT(*) cnt, ROUND(SUM(sale_price),2) sales,"
        " ROUND(SUM(kongfz_price),2) cost, ROUND(SUM(profit),2) profit"
        f" FROM orders WHERE status IN ({_in_clause()})"
    )
    params = list(VALID)
    if date_from:
        sql += " AND COALESCE(date(order_time), date(created_at)) >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND COALESCE(date(order_time), date(created_at)) <= ?"
        params.append(date_to)
    row = models.query(sql, params, one=True)
    return dict(row) if row else {"cnt": 0, "sales": 0, "cost": 0, "profit": 0}


def monthly_summary():
    return models.query(
        "SELECT substr(COALESCE(order_time, created_at),1,7) m, COUNT(*) cnt,"
        " ROUND(SUM(sale_price),2) sales, ROUND(SUM(profit),2) profit"
        f" FROM orders WHERE status IN ({_in_clause()})"
        " GROUP BY m ORDER BY m DESC LIMIT 24",
        VALID,
    )
