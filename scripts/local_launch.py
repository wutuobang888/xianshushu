"""本地统一入口：项目虚拟环境、独立数据目录，默认关闭业务调度。"""
import argparse
import os
from pathlib import Path
import runpy
import socket
import sqlite3
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='启动本地演示、正式版或孔夫子登录')
    parser.add_argument('mode', choices=('demo','live','login'))
    parser.add_argument('--tasks', action='store_true', help='正式版启用业务任务，可能自动回填发货')
    parser.add_argument('--no-browser', action='store_true', help='启动网站时不自动打开网页')
    args = parser.parse_args()
    if args.tasks and args.mode != 'live':
        parser.error('只有 live 模式可以启用业务任务')
    data = ROOT / 'data' / ('demo' if args.mode == 'demo' else 'live')
    data.mkdir(parents=True, exist_ok=True)
    database = data / 'database.db'
    if args.mode == 'demo':
        # 演示绝不继承终端里的真实闲管家凭据，也不删除已有数据库。
        os.environ.pop('XGJ_APP_KEY', None)
        os.environ.pop('XGJ_APP_SECRET', None)
        if database.exists():
            with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
                if exists and connection.execute("SELECT 1 FROM settings WHERE key IN ('xgj_app_key','xgj_app_secret') AND value!='' LIMIT 1").fetchone():
                    raise SystemExit('演示库已保存平台凭据，已停止。请先私有备份并核对数据，再使用正式模式；不要删除数据库。')
    os.environ.update(XIANSHUSHU_CLOUD_MODE='0', XIANSHUSHU_SCHEDULER='1' if args.tasks else '0',
        DEMO_MODE='1' if args.mode=='demo' else '0', FLASK_DEBUG='0',
        XIANSHUSHU_DATA_DIR=str(data), XIANSHUSHU_DATABASE=str(database),
        KONGFZ_USER_DATA_DIR=str(data/'browser_data'))
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    print('数据目录：'+str(data),flush=True)
    print('业务任务：'+('已启用，可能自动回填发货' if args.tasks else '关闭'),flush=True)
    if args.mode == 'login':
        sys.argv=['bots.kongfz_bot','login']
        runpy.run_module('bots.kongfz_bot',run_name='__main__')
        return
    try:
        with socket.create_connection(('127.0.0.1',5000),timeout=0.2):
            raise SystemExit('5000端口已占用，请先确认并正常停止原网站；本程序不会结束其他进程。')
    except (ConnectionRefusedError,TimeoutError,OSError):
        pass
    if not args.no_browser:
        def open_when_ready():
            for _ in range(40):
                try:
                    with urlopen('http://127.0.0.1:5000/orders',timeout=0.5) as response:
                        if response.status==200:
                            webbrowser.open('http://127.0.0.1:5000/orders');return
                except OSError:
                    time.sleep(0.25)
        threading.Thread(target=open_when_ready,daemon=True).start()
    print('关闭网站请在此窗口按 Ctrl+C。',flush=True)
    runpy.run_path(str(ROOT/'app.py'),run_name='__main__')


if __name__=='__main__':
    main()
