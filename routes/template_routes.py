# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash

from services import template_service

bp = Blueprint("templates", __name__)


@bp.route("/templates")
def index():
    return render_template(
        "templates.html",
        tpl_title=template_service.list_templates("title"),
        tpl_desc=template_service.list_templates("description"),
    )


@bp.route("/templates", methods=["POST"])
def create():
    try:
        template_service.create_template(
            request.form.get("name", "").strip() or "未命名模板",
            request.form.get("type", "title"),
            request.form.get("content", ""),
            1 if request.form.get("is_default") else 0,
        )
        flash("模板已保存", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("templates.index"))


@bp.route("/templates/<int:tid>/edit", methods=["POST"])
def update(tid):
    try:
        template_service.update_template(
            tid,
            request.form.get("name", "").strip() or "未命名模板",
            request.form.get("content", ""),
            1 if request.form.get("is_default") else 0,
        )
        flash("模板已更新", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("templates.index"))


@bp.route("/templates/<int:tid>/delete", methods=["POST"])
def delete(tid):
    template_service.delete_template(tid)
    flash("模板已删除", "success")
    return redirect(url_for("templates.index"))
