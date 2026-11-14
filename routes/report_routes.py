# -*- coding: utf-8 -*-
import datetime

from flask import Blueprint, render_template, request

import models
from services import report_service

bp = Blueprint("reports", __name__)


@bp.route("/reports")
def index():
    today = datetime.date.today()
    rng = request.args.get("range", "month")
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    if rng == "today":
        date_from = date_to = today.isoformat()
    elif rng == "week":
        date_from = (today - datetime.timedelta(days=today.weekday())).isoformat()
        date_to = today.isoformat()
    elif rng == "month":
        date_from = today.replace(day=1).isoformat()
        date_to = today.isoformat()
    return render_template(
        "reports.html",
        rng=rng, date_from=date_from or "", date_to=date_to or "",
        totals=report_service.totals(date_from, date_to),
        daily=report_service.daily_summary(date_from, date_to),
        accounts=report_service.account_summary(date_from, date_to),
        monthly=report_service.monthly_summary(),
    )


@bp.route("/customers")
def customers():
    """客户沉淀：窗口期攒下来的买家手机号，熟客识别/私域用。"""
    only = request.args.get("only", "")
    sql = "SELECT * FROM customers"
    if only == "repeat":
        sql += " WHERE order_count>1"
    rows = models.query(
        sql + " ORDER BY order_count DESC, total_spend DESC, last_seen DESC")
    stats = models.query(
        "SELECT COUNT(*) n,"
        " COALESCE(SUM(CASE WHEN order_count>1 THEN 1 ELSE 0 END),0) r"
        " FROM customers", one=True)
    return render_template("customers.html", rows=rows, only=only,
                           total=stats["n"], repeats=stats["r"])
