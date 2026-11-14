# -*- coding: utf-8 -*-
"""Flask 主入口：初始化数据库、注册路由、启动定时任务。"""
import logging
import secrets

from flask import Flask, jsonify, redirect, request, session, abort

import config
import models
from services import order_service, product_service, template_service
from services import kongfz_monitor, hotlist_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    models.init_db()
    template_service.seed_defaults()
    if config.DEMO_MODE and not _credentials_ok():
        order_service.seed_demo_orders()
        log.info("演示模式：已注入演示订单（配置 AppKey 后重启即走真实接口）")

    from routes.order_routes import bp as order_bp
    from routes.product_routes import bp as product_bp
    from routes.report_routes import bp as report_bp
    from routes.template_routes import bp as template_bp
    from routes.settings_routes import bp as settings_bp
    for bp in (order_bp, product_bp, report_bp, template_bp, settings_bp):
        app.register_blueprint(bp)

    if not config.CLOUD_MODE:
        app.secret_key = secrets.token_hex(32)

        def local_csrf_token():
            if 'local_csrf' not in session:
                session['local_csrf'] = secrets.token_urlsafe(32)
            return session['local_csrf']

        app.jinja_env.globals['csrf_token'] = local_csrf_token

        @app.before_request
        def protect_local_address_helper():
            if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
                abort(403)
            if request.endpoint == 'orders.browser_target':
                origin = request.headers.get('Origin', '')
                token = request.headers.get('X-CSRF-Token', '')
                if (origin != request.host_url.rstrip('/')
                        or not token or not secrets.compare_digest(token, session.get('local_csrf', ''))):
                    abort(403)

        @app.after_request
        def private_local_helper_response(response):
            if request.endpoint == 'orders.browser_target':
                response.headers['Cache-Control'] = 'no-store'
            return response

    @app.before_request
    def migrated_local_instance():
        if not config.CLOUD_MODE and config.AUTOMATION_PAUSE_FILE.exists():
            return jsonify(ok=False, error="本实例已暂停，请联系管理员"), 503

    @app.context_processor
    def inject_globals():
        return {"demo_mode": config.DEMO_MODE and not _credentials_ok(),
                "cloud_mode": config.CLOUD_MODE,
                "kongfz_login_dead": bool(models.get_setting("kongfz_login_dead"))}

    _start_scheduler()
    return app


def _credentials_ok() -> bool:
    from services.xgj_client import XgjClient
    return XgjClient().configured


def _start_scheduler():
    if not config.SCHEDULER_ENABLED or config.AUTOMATION_PAUSE_FILE.exists():
        log.info("当前进程不启动业务定时任务")
        return None
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def safe(fn, name):
        def run():
            if config.AUTOMATION_PAUSE_FILE.exists():
                return
            try:
                fn()
            except Exception as e:
                log.warning("%s 执行失败：%s", name, e)
        return run

    if _credentials_ok():
        scheduler.add_job(safe(order_service.sync_orders, "订单同步"),
                          "interval", minutes=config.ORDER_SYNC_INTERVAL_MINUTES,
                          id="order_sync")
        scheduler.add_job(safe(order_service.sync_refunds, "退款同步"),
                          "interval", minutes=config.REFUND_SYNC_INTERVAL_MINUTES,
                          id="refund_sync")
        scheduler.add_job(safe(product_service.sync_xianyu_products, "商品同步"),
                          "interval", minutes=60, id="product_sync")
    else:
        log.info("未配置闲管家凭证：订单/退款同步任务不启动")

    scheduler.add_job(safe(kongfz_monitor.check_all_once, "孔夫子监控"),
                      "interval", minutes=config.KONGFZ_CHECK_INTERVAL_MINUTES,
                      id="kongfz_check")
    # 热书榜每天 8:05 抓一次（孔夫子7天畅销 + 豆瓣月度热门）
    scheduler.add_job(safe(hotlist_service.refresh, "热书榜抓取"),
                      "cron", hour=8, minute=5, id="hotlist_daily")
    scheduler.start()
    return scheduler


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=config.DEBUG, use_reloader=False)
