# -*- coding: utf-8 -*-
"""标题/文案模板：CRUD + 变量渲染。"""
import re

import models

VAR_PATTERN = re.compile(r"\{([^{}]+)\}")


def list_templates(tpl_type=None):
    if tpl_type:
        return models.query(
            "SELECT * FROM templates WHERE type=? ORDER BY is_default DESC, id DESC",
            (tpl_type,),
        )
    return models.query("SELECT * FROM templates ORDER BY type, is_default DESC, id DESC")


def get_template(tpl_id):
    return models.query("SELECT * FROM templates WHERE id=?", (tpl_id,), one=True)


def create_template(name, tpl_type, content, is_default=0):
    if tpl_type not in ("title", "description"):
        raise ValueError("type 只能是 title 或 description")
    if is_default:
        models.execute("UPDATE templates SET is_default=0 WHERE type=?", (tpl_type,))
    return models.execute(
        "INSERT INTO templates(name,type,content,is_default) VALUES(?,?,?,?)",
        (name, tpl_type, content, 1 if is_default else 0),
    )


def update_template(tpl_id, name, content, is_default=0):
    row = get_template(tpl_id)
    if not row:
        raise ValueError("模板不存在")
    if is_default:
        models.execute("UPDATE templates SET is_default=0 WHERE type=?", (row["type"],))
    models.execute(
        "UPDATE templates SET name=?, content=?, is_default=? WHERE id=?",
        (name, content, 1 if is_default else 0, tpl_id),
    )


def delete_template(tpl_id):
    models.execute("DELETE FROM templates WHERE id=?", (tpl_id,))


def render(content: str, book: dict) -> str:
    """把 {书名}{作者}{出版社}{年份}{ISBN}{售价} 等变量替换为图书字段。"""
    mapping = {
        "书名": book.get("book_title") or book.get("title") or "",
        "标题": book.get("book_title") or book.get("title") or "",
        "作者": book.get("author") or "",
        "出版社": book.get("publisher") or book.get("press") or "",
        "年份": book.get("pub_year") or book.get("pub_date") or "",
        "ISBN": book.get("isbn") or "",
        "isbn": book.get("isbn") or "",
        "售价": str(book.get("sale_price") or ""),
    }
    return VAR_PATTERN.sub(lambda m: mapping.get(m.group(1).strip(), m.group(0)), content)


def publish_title(title: str) -> str:
    """平台标题最多60字符；按UTF-16计数，兼容浏览器和含emoji的标题。"""
    return title.strip().encode("utf-16-le")[:120].decode("utf-16-le", errors="ignore").rstrip()


def seed_defaults():
    """内置两套默认模板，仅当表为空时写入。"""
    if models.query("SELECT id FROM templates LIMIT 1"):
        return
    create_template(
        "默认标题", "title",
        "【正版二手】{书名} {作者} {出版社} 包邮", is_default=1,
    )
    create_template(
        "默认文案", "description",
        "书名：{书名}\n作者：{作者}\n出版社：{出版社}\n年份：{年份}\n\n"
        "二手图书，品相良好，实物拍摄，拍下后 48 小时内发货，全国包邮（偏远地区除外）。",
        is_default=1,
    )
