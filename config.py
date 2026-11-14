# -*- coding: utf-8 -*-
"""全局配置。敏感信息优先从环境变量读取，环境变量为空时回落到这里的常量。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("XIANSHUSHU_DATA_DIR", str(BASE_DIR)))
CLOUD_MODE = os.environ.get("XIANSHUSHU_CLOUD_MODE", "0") == "1"
SCHEDULER_ENABLED = os.environ.get("XIANSHUSHU_SCHEDULER", "1") == "1"
AUTOMATION_PAUSE_FILE = DATA_DIR / "automation.paused"

# ---------- 闲管家开放平台 ----------
XGJ_APP_KEY = os.environ.get("XGJ_APP_KEY", "")
XGJ_APP_SECRET = os.environ.get("XGJ_APP_SECRET", "")
XGJ_BASE_URL = os.environ.get("XGJ_BASE_URL", "https://open.goofish.pro")

# ---------- 数据库 ----------
DATABASE_PATH = Path(os.environ.get("XIANSHUSHU_DATABASE", str(DATA_DIR / "database.db")))
DATABASE = f"sqlite:///{DATABASE_PATH.as_posix()}"

# ---------- 孔夫子自动化 ----------
KONGFZ_USER_DATA_DIR = Path(os.environ.get("KONGFZ_USER_DATA_DIR", str(DATA_DIR / "browser_data")))
KONGFZ_HEADLESS = os.environ.get("KONGFZ_HEADLESS", "1") == "1"
KONGFZ_BROWSER_CHANNEL = os.environ.get("KONGFZ_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "chromium")
KONGFZ_KEY_FILE = os.environ.get("KONGFZ_KEY_FILE", "")
KONGFZ_CREDENTIAL_FILE = DATA_DIR / "kongfz-credentials.enc"
KONGFZ_LOGIN_STATUS_FILE = DATA_DIR / "kongfz-login-status.json"

# ---------- 定时任务 ----------
ORDER_SYNC_INTERVAL_MINUTES = int(os.environ.get("ORDER_SYNC_INTERVAL_MINUTES", "10"))
REFUND_SYNC_INTERVAL_MINUTES = int(os.environ.get("REFUND_SYNC_INTERVAL_MINUTES", "10"))
KONGFZ_CHECK_INTERVAL_MINUTES = int(os.environ.get("KONGFZ_CHECK_INTERVAL_MINUTES", "25"))

# ---------- Flask ----------
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "xianshouself-local-dev")
DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

# ---------- 演示模式 ----------
# 未配置 AppKey 时，演示模式生成假订单/假店铺，方便先把界面和流程跑通。
# 正式使用时把它置 0（或配置好 AppKey 后它会自动失效）。
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
