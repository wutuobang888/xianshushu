# -*- coding: utf-8 -*-
"""SQLite 数据层：建表 + 轻量行访问封装（KISS：标准库 sqlite3，不引 ORM）。"""
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

import config

_transaction_connection = ContextVar("database_transaction", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS xianyu_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id TEXT UNIQUE NOT NULL,
    authorize_id TEXT,
    shop_name TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kongfz_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    cookie_path TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_title TEXT NOT NULL,
    pure_title TEXT,
    author TEXT,
    publisher TEXT,
    pub_year TEXT,
    isbn TEXT,
    entry_url TEXT,
    kongfz_url TEXT,
    kongfz_price REAL,
    sale_price REAL NOT NULL,
    image_url TEXT,
    title_template_id INTEGER,
    desc_template_id INTEGER,
    xianyu_product_id TEXT,
    xianyu_item_id TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    seller_id TEXT,
    buyer_name TEXT,
    buyer_address TEXT,
    product_title TEXT,
    sale_price REAL,
    item_id TEXT,
    product_id TEXT,
    isbn TEXT,
    kongfz_url TEXT,
    kongfz_order_no TEXT,
    kongfz_price REAL,
    waybill_no TEXT,
    express_name TEXT,
    status TEXT DEFAULT 'pending',
    order_time TIMESTAMP,
    ship_time TIMESTAMP,
    profit REAL,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 孔夫子货源缓存：每次搜书的结果自动入库，kongfz_url 为唯一键
CREATE TABLE IF NOT EXISTS book_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn TEXT,
    book_title TEXT,
    author TEXT,
    publisher TEXT,
    pub_year TEXT,
    kongfz_url TEXT UNIQUE NOT NULL,
    kongfz_price REAL,
    image_url TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 图片候选池：只存 URL 不落盘。source=kongfz(卖家实拍)/xhs(小红书收图)
-- final_url = 去水印或转存后的公网 URL，发布时优先用它
CREATE TABLE IF NOT EXISTS image_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_url TEXT NOT NULL,
    source TEXT DEFAULT 'kongfz',
    shop_name TEXT,
    source_url TEXT,
    shop_price REAL,
    image_url TEXT NOT NULL,
    final_url TEXT,
    selected INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entry_url, image_url)
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    is_default INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kongfz_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id),
    kongfz_order_no TEXT NOT NULL,
    status TEXT DEFAULT 'monitoring',
    waybill_no TEXT,
    express_name TEXT,
    last_check_time TIMESTAMP,
    paid_amount_fen INTEGER CHECK(paid_amount_fen IS NULL OR paid_amount_fen >= 0),
    refund_amount_fen INTEGER CHECK(refund_amount_fen IS NULL OR refund_amount_fen >= 0),
    amount_basis TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    seller_id TEXT NOT NULL,
    remote_product_id TEXT,
    remote_item_id TEXT,
    sale_price_fen INTEGER CHECK(sale_price_fen IS NULL OR sale_price_fen >= 0),
    status TEXT NOT NULL DEFAULT 'needs_verify',
    payload_json TEXT,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, seller_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 孔夫子买家订单列表扫描缓存：已付款单都进，绑定后仍保留——
-- 勾选绑定列表用它，发货回填前的收货人校验闸门也靠它。
CREATE TABLE IF NOT EXISTS kongfz_scanned (
    kongfz_order_no TEXT PRIMARY KEY,
    title TEXT,
    total REAL,
    status TEXT,
    order_date TEXT,
    recv_name TEXT,
    recv_phone TEXT,
    recv_addr TEXT,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 客户沉淀：订单窗口期可见的买家手机号，交易结束后本地仍保留
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    city TEXT,
    order_count INTEGER DEFAULT 0,
    total_spend REAL DEFAULT 0,
    titles TEXT,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS hot_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    rank_no INTEGER,
    title TEXT,
    author TEXT,
    extra TEXT,
    book_url TEXT,
    fetched_date TEXT NOT NULL,
    UNIQUE(source, fetched_date, title)
);
"""


def get_db():
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    Path(config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """老库补列：SQLite 的 ALTER TABLE 没有 IF NOT EXISTS，先查 PRAGMA。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    for col in ("item_id", "product_id", "isbn", "kongfz_url"):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    for col in ("pure_title", "entry_url", "seller_id"):
        if col not in pcols:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT")
    acols = {r[1] for r in conn.execute("PRAGMA table_info(xianyu_accounts)")}
    if "authorize_id" not in acols:
        conn.execute("ALTER TABLE xianyu_accounts ADD COLUMN authorize_id TEXT")
    if "receiver_json" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN receiver_json TEXT")
    if "alert" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN alert TEXT")
    if "unpaid_hint" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN unpaid_hint TEXT")
    kcols = {r[1] for r in conn.execute("PRAGMA table_info(kongfz_orders)")}
    for col in ("paid_amount_fen", "refund_amount_fen"):
        if col not in kcols:
            conn.execute(f"ALTER TABLE kongfz_orders ADD COLUMN {col} INTEGER"
                         f" CHECK({col} IS NULL OR {col} >= 0)")
    if "amount_basis" not in kcols:
        conn.execute("ALTER TABLE kongfz_orders ADD COLUMN amount_basis TEXT")
    lcols = {r[1] for r in conn.execute("PRAGMA table_info(product_listings)")}
    if "payload_json" not in lcols:
        conn.execute("ALTER TABLE product_listings ADD COLUMN payload_json TEXT")
    # 旧的店铺归属先标待核对，不能把最后一个远端ID当作每家店的真实ID。
    for p in conn.execute("SELECT id,seller_id,xianyu_product_id,sale_price FROM products").fetchall():
        for sid in {s.strip() for s in (p[1] or "").split(",") if s.strip()}:
            conn.execute(
                "INSERT OR IGNORE INTO product_listings"
                "(product_id,seller_id,remote_product_id,sale_price_fen)"
                " VALUES(?,?,?,CAST(ROUND(? * 100) AS INTEGER))",
                (p[0], sid, p[2], p[3]))
    # 只补新字段，保留原聚合成本；有重复归属的历史采购不推定金额。
    conn.execute(
        "UPDATE kongfz_orders SET paid_amount_fen=CAST(ROUND((SELECT total"
        " FROM kongfz_scanned s WHERE s.kongfz_order_no=kongfz_orders.kongfz_order_no)*100) AS INTEGER),"
        " amount_basis='list_total' WHERE paid_amount_fen IS NULL"
        " AND (SELECT COUNT(*) FROM kongfz_orders k"
        " WHERE k.kongfz_order_no=kongfz_orders.kongfz_order_no)=1"
        " AND EXISTS(SELECT 1 FROM kongfz_scanned s"
        " WHERE s.kongfz_order_no=kongfz_orders.kongfz_order_no AND s.total>=0)")


def query(sql, params=(), one=False):
    conn = _transaction_connection.get()
    if conn is not None:
        rows = conn.execute(sql, params).fetchall()
    else:
        with closing(get_db()) as conn:
            rows = conn.execute(sql, params).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    conn = _transaction_connection.get()
    if conn is not None:
        return conn.execute(sql, params).lastrowid
    with closing(get_db()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


@contextmanager
def transaction():
    """一次业务操作共用连接；提前取得写锁，失败时整笔回滚。"""
    if _transaction_connection.get() is not None:
        raise RuntimeError("不支持嵌套数据库事务")
    with closing(get_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        token = _transaction_connection.set(conn)
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            _transaction_connection.reset(token)


def get_setting(key, default=""):
    row = query("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return row["value"] if row else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
        (key, value),
    )
