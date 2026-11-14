# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash

import models
import config
from services import product_service, xgj_client as xgj
from services import kongfz_auth

bp = Blueprint("settings", __name__)


@bp.route("/settings")
def index():
    return render_template(
        "settings.html",
        app_key=xgj.get_cred("XGJ_APP_KEY"),
        app_secret=xgj.get_cred("XGJ_APP_SECRET"),
        accounts=product_service.list_xianyu_accounts(),
        demo=config.DEMO_MODE,
        check_interval=config.KONGFZ_CHECK_INTERVAL_MINUTES,
        kongfz_status=kongfz_auth.public_status(),
    )


@bp.route("/settings/xgj", methods=["POST"])
def save_xgj():
    models.set_setting("xgj_app_key", request.form.get("app_key", "").strip())
    models.set_setting("xgj_app_secret", request.form.get("app_secret", "").strip())
    flash("闲管家凭证已保存", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/accounts", methods=["POST"])
def add_account():
    sid = request.form.get("seller_id", "").strip()
    name = request.form.get("shop_name", "").strip()
    if sid:
        product_service.add_xianyu_account(sid, name)
        flash("闲鱼账号已添加", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/kongfz",methods=["POST"])
def save_kongfz():
    if not config.CLOUD_MODE:
        return "仅云端正式实例支持此功能",400
    from services.kongfz_auth import save_credentials
    try:
        save_credentials(request.form.get("username","").strip(),request.form.get("password",""))
        flash("孔夫子账号已加密保存，检查任务会在需要时尝试登录；遇到验证请打开孔夫子窗口处理。","success")
    except Exception:
        flash("未能保存孔夫子账号，请检查服务器加密配置及填写内容。","danger")
    return redirect(url_for("settings.index"))


@bp.route("/settings/kongfz/login",methods=["POST"])
def login_kongfz():
    from services.kongfz_auth import start_login_window
    try:
        start_login_window()
        flash("登录窗口正在打开。点击“孔夫子窗口”完成验证；登录成功后窗口会自动关闭。","info")
    except Exception:
        flash("登录窗口未能打开，请稍后重试。","danger")
    return redirect(url_for("settings.index"))


@bp.route("/settings/accounts/<int:aid>/del", methods=["POST"])
def del_account(aid):
    models.execute("UPDATE xianyu_accounts SET is_active=0 WHERE id=?", (aid,))
    flash("已停用", "success")
    return redirect(url_for("settings.index"))
