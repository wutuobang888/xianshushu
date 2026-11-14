"""单账号云端采购：固定销售订单及地址，一次只允许一项人工采购。"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

import config
import models

KEY = "cloud_checkout_task"
ACTIVE = {"opening", "browsing", "filling", "ready", "attention", "closing"}
BROWSER_URL = "/browser/vnc.html?autoconnect=1&resize=scale&path=/browser/websockify"


def valid_source_url(url):
    parsed = urlsplit(url or "")
    return (parsed.scheme == "https" and parsed.hostname in
            {"book.kongfz.com", "item.kongfz.com", "search.kongfz.com"}
            and not parsed.username and not parsed.password
            and parsed.port in (None, 443))


def canonical_source_url(url):
    # 历史货源中有 HTTP 链接，只对固定官方主机升级为 HTTPS。
    parsed = urlsplit(url or '')
    if parsed.scheme == 'http' and parsed.hostname in {'book.kongfz.com','item.kongfz.com','search.kongfz.com'}:
        return parsed._replace(scheme='https').geturl()
    return url


def _alive(task):
    if task.get("state") not in ACTIVE:
        return False
    if not task.get("pid"):
        return time.time() - task.get("created_at", 0) < 120
    try:
        os.kill(int(task["pid"]), 0)
        if os.name != 'nt':
            # PID 重用不能把旧任务误判为仍在采购。
            if task['id'].encode() not in Path('/proc', str(task['pid']), 'cmdline').read_bytes():
                return False
        return True
    except (OSError, ValueError):
        return False


def current(private=False):
    task = json.loads(models.get_setting(KEY, "{}"))
    if not task:
        return None
    result = dict(task)
    result["active"] = _alive(task)
    if not result["active"] and result["state"] in ACTIVE:
        result.update(state="closed", message="采购窗口已结束，可以重新打开")
    if not private:
        result.pop("receiver", None)
        result.pop("pid", None)
    return result


def update(task_id, **values):
    with models.transaction():
        task = json.loads(models.get_setting(KEY, "{}"))
        if task.get("id") != task_id:
            raise ValueError("采购任务已更换，请关闭旧窗口")
        if task.get('state') == 'closing' and values.get('state') not in ('closed','failed'):
            return
        task.update(values, updated_at=int(time.time()))
        models.set_setting(KEY, json.dumps(task, ensure_ascii=False))


def fresh_receiver(order, *, allow_city_level=False):
    from services import xgj_client as xgj
    from services.order_service import normalize_receiver
    remote = xgj.XgjClient().order_detail(order['order_no'])
    if (xgj.pick_order_no(remote) != order['order_no']
            or xgj.map_remote_status(remote) != 'pending'
            or xgj.pick_ship_status(remote) != '12' or xgj.pick_waybill(remote)):
        raise ValueError('闲鱼订单当前不是待发货状态，已停止采购')
    receiver = {k:remote.get(k) or '' for k in ('receiver_name','receiver_mobile',
               'prov_name','city_name','area_name','town_name','address')}
    required = ['receiver_name','receiver_mobile','prov_name','city_name','address']
    # 中山市不设区县。仅免插件同步允许缺区，仍须由孔夫子地区代码回读核实。
    city_level = (allow_city_level and receiver['prov_name'] == '广东省'
                  and receiver['city_name'] == '中山市' and not receiver['area_name'])
    if not city_level:
        required.append('area_name')
    if not all(receiver.get(k) for k in required):
        raise ValueError('未能取得完整的最新收货信息，请稍后重试')
    if not re.fullmatch(r'\d{7,20}', receiver['receiver_mobile']):
        raise ValueError('收货电话不完整，不能自动填单')
    models.execute('UPDATE orders SET receiver_json=? WHERE id=?',
                   (json.dumps(receiver, ensure_ascii=False), order['id']))
    return normalize_receiver(receiver)


def start(order_id, source_url=None, buy_now=False):
    from services import order_service
    if not config.CLOUD_MODE:
        raise ValueError("此入口仅用于服务器采购")
    # 整个准备阶段串行化，防止两次点击各自启动一个浏览器。
    from services.process_lock import acquire
    lock = acquire(config.DATA_DIR / "locks" / "checkout-start.lock", "正在准备采购，请稍后再试")
    try:
        task = current(private=True)
        if task and task["active"]:
            if task["order_id"] == order_id and task["state"] != "closing":
                return current()
            raise ValueError("已有采购窗口正在处理订单 " + task["order_no"] + "，请先结束该采购")
        order = models.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
        if not order or order["status"] not in ("pending", "purchasing"):
            raise ValueError("仅待采购、采购中的订单可以打开采购窗口")
        source_url = canonical_source_url(source_url)
        if source_url and not valid_source_url(source_url):
            raise ValueError("请使用孔夫子官方 HTTPS 商品或搜索链接")
        # 开始采购时重新取地址，不能把过期缓存直接写入孔夫子。
        receiver = fresh_receiver(order)
        if not source_url:
            rows, _ = order_service.list_orders(keyword=order["order_no"], size=50)
            source_url = next((row["jump_url"] for row in rows if row["id"] == order_id), None)
        source_url = canonical_source_url(source_url)
        if not valid_source_url(source_url):
            raise ValueError("未找到有效货源入口")
        task = dict(id=uuid.uuid4().hex, order_id=order_id, order_no=order["order_no"],
                    title=order["product_title"], receiver=receiver, source_url=source_url,
                    buy_now=bool(buy_now), state="opening", message="正在打开服务器采购窗口",
                    created_at=int(time.time()), updated_at=int(time.time()), pid=None)
        with models.transaction():
            models.set_setting(KEY, json.dumps(task, ensure_ascii=False))
            process = subprocess.Popen([sys.executable, "-u", "-m", "bots.cloud_checkout", task["id"]],
                                       cwd=str(config.BASE_DIR), stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            task["pid"] = process.pid
            models.set_setting(KEY, json.dumps(task, ensure_ascii=False))
        return current()
    finally:
        lock.close()


def close(task_id):
    task = current(private=True)
    if not task or task["id"] != task_id:
        raise ValueError("采购任务已更换，请刷新页面")
    update(task_id, state="closing", message="正在结束采购窗口；未提交的页面内容不会保留")
