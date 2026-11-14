# -*- coding: utf-8 -*-
"""闲管家开放平台 API 客户端。

签名规则（文档第七节）：
    sign = MD5("应用KEY,Body报文Md5值,时间戳,应用密钥")
即 MD5(f"{app_key},{md5(body)},{timestamp},{app_secret}")。

接口字段名在方案文档中未给出，本模块对返回体做宽松解包（code/data/records 等常见
结构），字段提取集中在 pick_* 一组函数里，字段有差异时只改这里。
"""
import hashlib
import json
import logging
import time

import requests

import config

log = logging.getLogger(__name__)


class XgjError(Exception):
    pass


class XgjUncertainError(XgjError):
    """写请求没有拿到可靠结果，必须核对远端后再继续。"""
    pass


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_cred(key: str) -> str:
    """凭证优先级：settings 表 > config 常量 > 环境变量。"""
    import models

    # key 形如 XGJ_APP_KEY，settings 表里的键就是小写形式 xgj_app_key
    val = models.get_setting(key.lower())
    return val or getattr(config, key, "")


class XgjClient:
    _READ_PATHS = frozenset({
        "/api/open/user/authorize/list", "/api/open/order/list",
        "/api/open/order/detail", "/api/open/trade/refund/list",
        "/api/open/product/list", "/api/open/product/detail",
    })
    def __init__(self, app_key=None, app_secret=None, base_url=None, timeout=20):
        self.app_key = (app_key or get_cred("XGJ_APP_KEY") or "").strip()
        self.app_secret = (app_secret or get_cred("XGJ_APP_SECRET") or "").strip()
        self.base_url = (base_url or config.XGJ_BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def call(self, path: str, payload: dict | None = None) -> dict:
        if not self.configured:
            raise XgjError("闲管家 AppKey/AppSecret 未配置（系统设置页或 config.py）")
        body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        # 实测协议：appid/timestamp/sign 三个参数放 query，timestamp 为秒级。
        # 签名 = md5(f"{appid},{md5(body)},{timestamp},{appSecret}")
        timestamp = str(int(time.time()))
        sign = _md5(f"{self.app_key},{_md5(body)},{timestamp},{self.app_secret}")
        url = (f"{self.base_url}{path}"
               f"?appid={self.app_key}&timestamp={timestamp}&sign={sign}")
        resp = None
        last_err = None
        attempts = 3 if path in self._READ_PATHS else 1
        for attempt in range(attempts):
            try:
                resp = requests.post(
                    url,
                    data=body.encode("utf-8"),
                    headers={"Content-Type": "application/json;charset=utf-8"},
                    timeout=self.timeout,
                )
                break
            except (requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ReadTimeout) as e:
                last_err = e
                if attempt + 1 < attempts:
                    time.sleep(2)
        if resp is None:
            if path not in self._READ_PATHS:
                raise XgjUncertainError("操作结果未确认，已停止自动重试，请先核对平台结果再继续")
            raise XgjError(f"闲管家读取失败（尝试{attempts}次）：{type(last_err).__name__}")
        try:
            data = resp.json()
        except ValueError:
            if path not in self._READ_PATHS:
                raise XgjUncertainError("操作未返回可靠结果，请先核对平台状态")
            raise XgjError(f"接口返回非 JSON：HTTP {resp.status_code} {resp.text[:200]}")
        if resp.status_code != 200:
            if path not in self._READ_PATHS and resp.status_code >= 500:
                raise XgjUncertainError("平台服务异常，操作结果未确认，请先同步核对")
            raise XgjError(f"HTTP {resp.status_code}: {data}")
        if not isinstance(data, dict):
            error = XgjUncertainError if path not in self._READ_PATHS else XgjError
            raise error("接口返回结构异常，请先核对平台状态")
        code = data.get("code", data.get("status"))
        if code is None and path not in self._READ_PATHS:
            raise XgjUncertainError("操作结果缺少确认码，请先同步核对")
        if code not in (None, 0, 200, "0", "200", "SUCCESS", "success"):
            raise XgjError(f"接口错误 {code}: {data.get('message') or data.get('msg') or data}")
        return data

    # ---------- 业务封装 ----------

    def list_shops(self):
        return _extract(self.call("/api/open/user/authorize/list"))

    def list_orders(self, seller_id=None, authorize_id=None, page=1,
                    page_size=50, **extra):
        # 官方参数是蛇形 page_no/page_size/authorize_id（店铺授权ID，按店铺过滤用）
        payload = {"page_no": page, "page_size": page_size, **extra}
        if authorize_id:
            payload["authorize_id"] = int(authorize_id)
        return _extract(self.call("/api/open/order/list", payload))

    def order_detail(self, order_no, seller_id=None):
        payload = {"order_no": order_no}
        if seller_id:
            payload["seller_id"] = seller_id
        return _extract(self.call("/api/open/order/detail", payload), single=True)

    # 快递公司名 -> 闲管家 express_code（官方必填字段）；匹配不上走 qita+express_name
    _EXPRESS_CODES = {
        "圆通": "yuantong", "中通": "zhongtong", "韵达": "yunda",
        "申通": "shentong", "顺丰": "shunfeng", "邮政": "youzhengguonei",
        "EMS": "ems", "京东": "jd", "极兔": "jtexpress", "德邦": "debang",
        "百世": "baishiwuliu", "丰网": "fengwang",
        "快递包裹": "youzhengguonei",   # 孔夫子详情页对邮政快递包裹的写法
    }

    def ship_order(self, order_no, waybill_no, express_name="", seller_id=None):
        # 官方参数全蛇形且 express_code/express_name 必填（seller_id 仅商务对接走query，不传）
        code = next((v for k, v in self._EXPRESS_CODES.items()
                     if k in (express_name or "")), "qita")
        payload = {
            "order_no": str(order_no),
            "waybill_no": str(waybill_no),
            "express_code": code,
            "express_name": express_name or "其他",
        }
        return self.call("/api/open/order/ship", payload)

    def list_refunds(self, seller_id=None, page=1, page_size=50):
        payload = {"page_no": page, "page_size": page_size}
        if seller_id:
            payload["seller_id"] = seller_id
        return _extract(self.call("/api/open/trade/refund/list", payload))

    def create_product(self, payload: dict):
        return _extract(self.call("/api/open/product/create", payload), single=True)

    def publish_product(self, product_id=None, item_id=None, seller_id=None):
        payload = {}
        if product_id:
            payload["product_id"] = int(product_id)
        if item_id:
            payload["item_id"] = int(item_id)
        if seller_id:
            payload["user_name"] = [seller_id]
        return self.call("/api/open/product/publish", payload)

    def list_products(self, seller_id=None, page=1, page_size=50):
        payload = {"page_no": page, "page_size": page_size}
        if seller_id:
            payload["seller_id"] = seller_id
        return _extract(self.call("/api/open/product/list", payload))

    def product_detail(self, product_id):
        return _extract(
            self.call("/api/open/product/detail", {"product_id": int(product_id)}),
            single=True)

    def down_shelf(self, product_id=None, item_id=None, seller_id=None):
        """下架商品：官方接口按 product_id（蛇形）。"""
        if not product_id:
            raise XgjError("下架需要 product_id")
        payload = {"product_id": int(product_id)}
        if seller_id:
            payload["sellerId"] = seller_id
        return self.call("/api/open/product/downShelf", payload)


def _first(d: dict, *keys, default=None):
    """宽松取值：同一字段不同命名风格都试一遍。"""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _extract(resp, single=False):
    """解包闲管家响应体：兼容 data 为 list / dict / 包在 records|rows|list 里的情况。"""
    data = resp.get("data", resp)
    if isinstance(data, list):
        return data[0] if (single and data) else data
    if not isinstance(data, dict):
        return {} if single else []
    for key in ("records", "rows", "list", "items", "orderList", "resultList"):
        if isinstance(data.get(key), list):
            lst = data[key]
            return (lst[0] if lst else {}) if single else lst
    return data if single else ([data] if data else [])


# ---------- 字段提取（供 order_service / product_service 复用） ----------

def pick_order_no(item):
    return str(_first(item, "order_no", "orderNo", "orderId", "tid", default="")).strip()


def pick_seller_id(item):
    return str(_first(item, "seller_name", "sellerId", "seller_id", "shopId",
                      "userId", default="")).strip()


def pick_buyer_name(item):
    return _first(item, "buyer_nick", "buyerName", "buyerNick", "buyer", "receiverName",
                  "receiver_name", default="")


def pick_buyer_address(item):
    addr = _first(item, "buyerAddress", "receiverAddress", "address", "fullAddress",
                  "receiver_address", "buyer_address",
                  default="")
    if isinstance(addr, dict):
        addr = "".join(str(addr.get(k, "")) for k in ("province", "city", "district", "detail"))
    return addr or ""


def pick_title(item):
    g = item.get("goods")
    if isinstance(g, dict):
        t = _first(g, "title", "itemTitle", "itemName", default="")
        if t:
            return t
    t = _first(item, "itemTitle", "productTitle", "title", "bookName", "itemName",
               default="")
    if not t and isinstance(item.get("items"), list) and item["items"]:
        t = _first(item["items"][0], "title", "itemTitle", "itemName", default="")
    return t or ""


def pick_price(item):
    for k in ("pay_amount", "total_amount", "payment", "totalFee", "price",
              "salePrice", "actualPayment"):
        v = item.get(k)
        if v is None:
            continue
        try:
            v = float(v)
            return v / 100  # 接口金额单位是分
        except (TypeError, ValueError):
            continue
    return None


def pick_order_time(item):
    v = _first(item, "pay_time", "order_time", "createTime", "orderTime",
               "gmtCreate", "created", default="")
    if v:
        try:
            import datetime
            return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return str(v)
    return ""


def pick_waybill(item):
    return _first(item, "waybill_no", "waybillNo", "waybill", default="") or ""


def pick_express(item):
    return _first(item, "express_name", "expressName", "express_code", default="") or ""


def pick_ship_status(item):
    return str(_first(item, "order_status", "status", "orderStatus", "tradeStatus",
                      default="")).upper()


# 官方文档状态码：11=待付款 12=待发货(已付款) 21=已发货 22=已完成 23=已退款 24=已关闭
# 真发货以 waybill_no/consign_time 为准——24 无运单无发货时间的一律算关闭。
_SHIPPED_STATES = {"21", "SHIPPED", "WAIT_BUYER_CONFIRM"}
_CLOSED_CODES = {"24"}


def map_remote_status(item, refund_map: dict | None = None):
    """把闲管家订单状态映射到本地状态。refund_map: order_no -> 退款是否完成。"""
    no = pick_order_no(item)
    if refund_map and no in refund_map:
        return "refunded" if refund_map[no] else "refunding"
    code = pick_ship_status(item)
    try:
        shipped_proof = bool(item.get("waybill_no")) or int(
            item.get("consign_time") or 0) > 0
    except (TypeError, ValueError):
        shipped_proof = bool(item.get("waybill_no"))
    # refund_status 订单级退款状态：1=退款进行中，5=退款完成，0=无退款；
    # 2~4 是已了结的退款记录（撤销/拒绝/关闭），不覆盖订单本身状态。
    rs = str(item.get("refund_status") or "0")
    if rs == "5":
        return "refunded"
    if rs == "1":
        return "refunding"
    if code == "22":
        return "completed_remote"   # 已完成：买家已确认收货
    if code == "23":
        return "refunded"           # 官方：23=已退款
    if code in _CLOSED_CODES:
        return "shipped_remote" if shipped_proof else "closed"
    if code in _SHIPPED_STATES or shipped_proof:
        return "shipped_remote"
    if code in {"REFUND", "REFUNDING", "4", "5"}:
        return "refunding"
    return "pending"


def pick_product_ids(item):
    """返回 (product_id, item_id)。订单侧藏在 goods 里，商品列表/详情在顶层。"""
    g = item.get("goods")
    if isinstance(g, list):
        g = g[0] if g else {}
    sources = [g] if isinstance(g, dict) else []
    sources.append(item)
    pid = iid = ""
    for src in sources:
        if not pid:
            pid = _first(src, "product_id", "productId", default="")
        if not iid:
            iid = _first(src, "item_id", "itemId", default="")
    if not iid:
        shops = item.get("publish_shop")
        if isinstance(shops, list) and shops:
            iid = _first(shops[0], "item_id", "itemId", default="")
    return str(pid or ""), str(iid or "")


def pick_isbn(item):
    """宽松提取图书 ISBN：商品详情在 book_data，订单可能藏在 goods/outer_id。"""
    g = item.get("goods")
    if isinstance(g, list):
        g = g[0] if g else {}
    bd = item.get("book_data")
    sources = [s for s in (bd, g, item) if isinstance(s, dict)]
    for src in sources:
        v = _first(src, "isbn", "ISBN", "isbn13", "bookIsbn", "outerId",
                   "outer_id", "skuOuterId", "sku_outer_id", "barcode", default="")
        if v:
            return str(v).strip()
    return ""
