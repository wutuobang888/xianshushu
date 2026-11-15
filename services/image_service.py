# -*- coding: utf-8 -*-
"""图片候选池：图池存取、勾选、孔夫子右下角水印裁剪、临时图床转存。

设计约定：
- 孔夫子原图保存 URL；小红书收图及处理过的图片保留本地缓存
- 去水印后的新图需要公网 URL：传 uguu.se（匿名、直出图片、约 3h 失效）
- 处理过的图同时存 logs/imgtmp/{id}.jpg，发布时重传，保留供跨店复用
- entry_url 是图池的锚点（= products.kongfz_url，即孔夫子条目页）
"""
import logging
import hashlib
import os
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import requests
from urllib.parse import urlsplit

import config
import models

log = logging.getLogger(__name__)

TMP_DIR = Path(config.DATA_DIR) / "logs" / "imgtmp"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


# ---------- 图池存取 ----------

def save_candidates(entry_url, shops):
    """把抓取到的卖家图集写入图池，(entry_url, image_url) 去重。"""
    n = 0
    for sh in shops or []:
        for u in sh.get("images") or []:
            models.execute(
                "INSERT OR IGNORE INTO image_pool"
                "(entry_url,source,shop_name,source_url,shop_price,image_url)"
                " VALUES(?,'kongfz',?,?,?,?)",
                (entry_url, sh.get("shop_name"), sh.get("url"),
                 sh.get("shop_price"), u))
            n += 1
    return n


def list_pool(entry_url):
    """图池按来源+卖家分组返回，供前端平铺。"""
    rows = models.query(
        "SELECT * FROM image_pool WHERE entry_url=? ORDER BY id", (entry_url,))
    groups, seen = [], {}
    for r in rows:
        key = (r["source"], r["shop_name"] or "", r["source_url"] or "")
        if key not in seen:
            seen[key] = {"source": r["source"], "shop_name": r["shop_name"] or "",
                         "source_url": r["source_url"] or "",
                         "shop_price": r["shop_price"], "images": []}
            groups.append(seen[key])
        image = dict(r)
        image["processed"] = bool(models.get_setting(f"image_cleaned_sha256:{r['id']}")) or str(r["final_url"] or "").startswith("https://")
        seen[key]["images"].append(image)
    return groups


def set_selected(image_id, selected):
    models.execute("UPDATE image_pool SET selected=? WHERE id=?",
                   (1 if selected else 0, image_id))


def delete_image(image_id):
    models.execute("DELETE FROM image_pool WHERE id=?", (image_id,))


def selected_count(entry_url):
    r = models.query(
        "SELECT COUNT(*) c FROM image_pool WHERE entry_url=? AND selected=1",
        (entry_url,), one=True)
    return r["c"] if r else 0


# ---------- 下载 / 转存 ----------

def _download(url):
    headers = dict(_UA)
    if "xiaohongshu" in url or "xhscdn" in url:
        headers["Referer"] = "https://www.xiaohongshu.com/"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    if len(r.content) < 1000:
        raise ValueError("图片内容异常（可能被防盗链拦截）")
    return r.content


def _tmp_file(image_id):
    return TMP_DIR / f"{image_id}.jpg"


def _original_file(image_id):
    return TMP_DIR / "original" / f"{image_id}.jpg"


def _rehost(content: bytes, filename="book.jpg") -> str:
    """上传到当前官方接口；Windows使用系统TLS，避免Python连接兼容问题。"""
    endpoint = "https://uguu.se/upload"
    curl = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "curl.exe"
    try:
        if os.name == "nt" and curl.is_file():
            result = subprocess.run(
                [str(curl), "--silent", "--show-error", "--fail-with-body",
                 "--connect-timeout", "10", "--max-time", "60",
                 "--form", f"files[]=@-;filename={filename};type=image/jpeg", endpoint],
                input=content, capture_output=True, timeout=65,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("临时图床连接或上传失败，本地图片已保留，请稍后重试")
            data = json.loads(result.stdout)
        else:
            r = requests.post(endpoint, files={"files[]": (filename, content, "image/jpeg")},
                              timeout=(10, 60), headers=_UA)
            r.raise_for_status()
            data = r.json()
    except (requests.RequestException, subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError("临时图床连接失败，本地图片已保留，请稍后重试") from e
    except ValueError as e:
        raise RuntimeError("图床返回内容异常，本地图片已保留") from e
    if not isinstance(data, dict) or data.get("success") is not True:
        raise RuntimeError("图床未确认上传成功，本地图片已保留")
    files = data.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise RuntimeError("图床未返回有效图片地址，本地图片已保留")
    url = files[0].get("url")
    if not isinstance(url, str):
        raise RuntimeError("图床未返回有效图片地址，本地图片已保留")
    parsed = urlsplit(url)
    if parsed.scheme == "https" and (
            parsed.hostname == "uguu.se" or (parsed.hostname or "").endswith(".uguu.se")):
        return url
    raise RuntimeError("图床未返回有效图片地址，本地图片已保留")


# ---------- 去水印 ----------

def _dewatermark(content: bytes) -> bytes:
    """识别孔夫子右下角水印后裁掉边缘，不再用模糊修补替换背景。

    优先裁底部；如检测到大块浅色书本跨越裁线，则尝试裁右侧。
    该保护是保守的图像判断，无法识别所有深色书本，处理后仍需预览。
    """
    img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("图片解码失败")
    h, w = img.shape[:2]
    if min(h, w) < 100:
        raise ValueError("图片过小，请手动裁剪水印")
    x0, y0 = int(w * 0.74), int(h * 0.90)
    roi = img[y0:, x0:]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    chroma = roi.max(axis=2).astype(np.int16) - roi.min(axis=2).astype(np.int16)
    mask = ((tophat > 12) & (gray > 110) & (chroma < 120)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    text_mask = np.zeros_like(mask)
    rh, rw = mask.shape
    for label in range(1, count):
        x, y, cw, ch, area = stats[label]
        if (area >= 40 and ch >= 6 and x > 2 and y > 2
                and x + cw < rw - 2 and y + ch < rh - 2
                and cw < rw * 0.35 and ch < rh * 0.55 and cw < ch * 7):
            text_mask[labels == label] = 1
    if not text_mask.any():
        raise ValueError("未能可靠识别水印，请手动裁剪或更换图片")
    ys, xs = np.nonzero(text_mask)
    # 给笔画阴影留边，裁掉整条边缘，保留剩余区域的真实纹理。
    cut_y = y0 + int(ys.min()) - max(4, round(h * 0.006))
    cut_x = x0 + int(xs.min()) - max(4, round(w * 0.006))
    gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    chroma_full = img.max(axis=2).astype(np.int16) - img.min(axis=2).astype(np.int16)
    pale = ((gray_full > 135) & (chroma_full < 95)).astype(np.uint8)
    _, _, book_stats, _ = cv2.connectedComponentsWithStats(pale)
    books = [r for r in book_stats[1:] if r[4] > h * w * 0.04]
    crosses_bottom = any(y < cut_y < y + bh for x, y, bw, bh, area in books)
    crosses_right = any(x < cut_x < x + bw for x, y, bw, bh, area in books)
    if not crosses_bottom:
        out = img[:cut_y, :]
    elif not crosses_right:
        out = img[:, :cut_x]
    else:
        raise ValueError("水印裁剪可能切到书本，请手动裁剪或更换图片")
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("图片编码失败")
    return buf.tobytes()


def _save_cleaned_image(image_id, original):
    """保留原文件及去水印缓存，预览不依赖外部图床。"""
    cleaned = _dewatermark(original)
    original_file = _original_file(image_id)
    original_file.parent.mkdir(parents=True, exist_ok=True)
    if not original_file.exists():
        original_file.write_bytes(original)
    _tmp_file(image_id).write_bytes(cleaned)
    models.set_setting(f"image_cleaned_sha256:{image_id}", hashlib.sha256(cleaned).hexdigest())
    models.set_setting(f"image_processing_method:{image_id}", "crop-v1")
    models.execute("UPDATE image_pool SET final_url=? WHERE id=?", (f"/pool_img/{image_id}", image_id))
    return cleaned


def dewatermark_image(image_id):
    """下载原图→去水印→保存原图备份及本地预览。上传只在上架时执行。"""
    row = models.query("SELECT * FROM image_pool WHERE id=?", (image_id,), one=True)
    if not row:
        raise ValueError("图片不存在")
    _save_cleaned_image(image_id, _download(row["image_url"]))
    return f"/pool_img/{image_id}"


def restore_image(image_id):
    """恢复原图预览，保留全部文件；上架时仍自动执行去水印流程。"""
    row = models.query("SELECT * FROM image_pool WHERE id=?", (image_id,), one=True)
    if not row:
        raise ValueError("图片不存在")
    original = _original_file(image_id)
    content = original.read_bytes() if original.exists() else _download(row["image_url"])
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    _tmp_file(image_id).write_bytes(content)
    models.set_setting(f"image_cleaned_sha256:{image_id}", "")
    if row["source"] in ("kongfz", "kongfz_manual"):
        models.set_setting(f"image_original_sha256:{image_id}", hashlib.sha256(content).hexdigest())
    models.execute("UPDATE image_pool SET final_url=? WHERE id=?", (f"/pool_img/{image_id}", image_id))


# ---------- 小红书收图（浏览器扩展回传） ----------

def collect_target():
    return models.get_setting("xhs_collect_target")


def set_collect_target(entry_url):
    if entry_url:
        collect_target_info(entry_url)
    models.set_setting("xhs_collect_target", entry_url or "")


def collect_target_info(entry_url):
    """只接受本地已知图池；目标由标签页明确提交，不能借用全局旧目标。"""
    if not isinstance(entry_url, str) or not entry_url.strip():
        raise ValueError("收图缺少书籍目标，请更新收图脚本后从网站图池重新打开找图网站")
    entry_url = entry_url.strip()
    row = models.query(
        "SELECT book_title FROM book_sources WHERE kongfz_url=?"
        " UNION ALL SELECT book_title FROM products WHERE kongfz_url=?"
        " UNION ALL SELECT '当前图池' FROM image_pool WHERE entry_url=? LIMIT 1",
        (entry_url, entry_url, entry_url), one=True)
    if not row:
        raise ValueError("收图目标不存在，请从网站选择书籍后重新打开小红书")
    return {"target": entry_url, "title": row["book_title"] or "当前图池"}


def collect_xhs(image_urls, note_url="", note_title="", entry_url=None):
    """笔记图片写入标签页明确携带的图池目标。返回成功缓存数量。

    小红书 CDN 链接带签名时效，收图瞬间立刻下载落盘（logs/imgtmp/），
    final_url 指本地 /pool_img/<id> 预览；发布管线命中临时文件直接转存。"""
    return _collect_images(image_urls, note_url, note_title, entry_url, "xhs")


def collect_kongfz(image_urls, note_url="", note_title="", entry_url=None):
    """只收卖家商品详情页的孔夫子图床图片，保持明确图池归属。"""
    page = urlsplit(note_url)
    parts = page.path.strip("/").split("/")
    if page.scheme != "https" or page.hostname != "book.kongfz.com" or len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("请进入孔夫子卖家商品详情页后再收图")
    if not isinstance(image_urls, list) or any(
            not isinstance(u, str) or urlsplit(u).scheme != "https" or not
            ((urlsplit(u).hostname or "").endswith(".kfzimg.com") or urlsplit(u).hostname == "kfzimg.com") for u in image_urls):
        raise ValueError("孔夫子图片地址无效，请在商品详情页重新收图")
    return _collect_images(image_urls, note_url, note_title, entry_url, "kongfz_manual")


def _collect_images(image_urls, note_url, note_title, entry_url, source):
    target = collect_target_info(entry_url)["target"]
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for u in image_urls or []:
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        models.execute(
            "INSERT OR IGNORE INTO image_pool"
            "(entry_url,source,shop_name,source_url,image_url)"
            " VALUES(?,?,?,?,?)",
            (target, source, note_title[:80] if note_title else ("小红书" if source == "xhs" else "孔夫子手动收图"),
             note_url, u))
        # INSERT OR IGNORE 的返回值不能判断是否插入；按唯一键取得实际记录。
        row = models.query("SELECT id FROM image_pool WHERE entry_url=? AND image_url=?",
                           (target, u), one=True)
        if not row:
            continue
        iid = row["id"]
        try:
            f = _tmp_file(iid)
            if not f.exists():
                content = _download(u)
                f.write_bytes(content)
                models.set_setting(f"image_cleaned_sha256:{iid}", "")
                if source == "kongfz_manual":
                    models.set_setting(f"image_original_sha256:{iid}", hashlib.sha256(content).hexdigest())
            models.execute("UPDATE image_pool SET final_url=? WHERE id=?",
                           (f"/pool_img/{iid}", iid))
            n += 1
        except Exception as e:
            log.warning("%s图 #%s 落盘失败（链接可能已过期）：%s", source, iid, e)
    return n


def local_image_file(image_id):
    """图池本地缓存文件路径（供 /pool_img/<id> 路由发文件）。"""
    f = _tmp_file(image_id)
    return f if f.exists() else None


# ---------- 发布管线 ----------

def _publish_image_content(row):
    """上架孔夫子图片必须使用已处理缓存，不能退回带水印的原链接。"""
    tmp = _tmp_file(row["id"])
    if tmp.exists():
        content = tmp.read_bytes()
    else:
        if row["final_url"] and not str(row["final_url"]).startswith("/pool_img/"):
            raise ValueError("处理后的图片缓存缺失，请重新处理该图片")
        content = _download(row["image_url"])
    if row["source"] not in ("kongfz", "kongfz_manual"):
        return content
    digest = hashlib.sha256(content).hexdigest()
    cleaned_digest = models.get_setting(f"image_cleaned_sha256:{row['id']}")
    method = models.get_setting(f"image_processing_method:{row['id']}")
    if cleaned_digest == digest and method == "crop-v1":
        return content
    # 旧修补图必须从原图重新裁剪，禁止裁剪旧处理图或重复裁剪。
    original = _original_file(row["id"])
    original_digest = models.get_setting(f"image_original_sha256:{row['id']}")
    if original.exists():
        content = original.read_bytes()
    elif cleaned_digest or (original_digest and digest != original_digest) or str(row["final_url"] or "").startswith("https://"):
        content = _download(row["image_url"])
    return _save_cleaned_image(row["id"], content)


def validate_image_ids(entry_url, image_ids):
    """检查显式图片顺序，拒绝错书图片、已删除图片和重复ID。"""
    if not isinstance(image_ids, list) or any(type(i) is not int or i <= 0 for i in image_ids):
        raise ValueError("图片顺序格式无效，请重新选择图片")
    if len(set(image_ids)) != len(image_ids):
        raise ValueError("图片顺序包含重复图片，请重新选择")
    if image_ids:
        rows = models.query("SELECT id FROM image_pool WHERE entry_url=? AND id IN ("
                            + ",".join("?" for _ in image_ids) + ")", [entry_url] + image_ids)
        if {r["id"] for r in rows} != set(image_ids):
            raise ValueError("选中的图片已删除或不属于这本书，请刷新后重选")
    return image_ids


def publish_image_urls(entry_url, image_ids=None):
    """选中图 → 最终公网 URL 列表。

    - 孔夫子图片自动去水印并保留原图备份；重复上架复用处理图
    - 上传处理后的图片取得新鲜公网地址，失败时保留缓存并暂停上架
    - 小红书图片下载或使用已有缓存后转存
    image_ids：显式指定用哪几张（跨店上架差异化用图），None=用当前勾选
    """
    sql = "SELECT * FROM image_pool WHERE entry_url=?"
    params = [entry_url]
    if image_ids is not None:
        validate_image_ids(entry_url, image_ids)
        if not image_ids:
            return []
        sql += f" AND id IN ({','.join('?'*len(image_ids))})"
        params += list(image_ids)
    else:
        sql += " AND selected=1"
    rows = models.query(sql + " ORDER BY id", params)
    if image_ids is not None:
        by_id = {r["id"]: r for r in rows}
        rows = [by_id[i] for i in image_ids]
    urls = []
    failures = []
    reasons = []
    for r in rows:
        try:
            urls.append(_rehost(_publish_image_content(r), f"book{r['id']}.jpg"))
        except Exception as e:
            log.warning("图 #%s 转存失败：%s", r["id"], e)
            failures.append(str(r["id"]))
            reason = str(e)[:160]
            if reason not in reasons:
                reasons.append(reason)
    if failures:
        raise ValueError("选中的图片准备失败，已暂停上架，图片 #" + ",".join(failures)
                         + "。原因：" + "；".join(reasons))
    return urls


def cleanup_tmp(entry_url):
    """发布完成后清掉这条目下的本地临时文件（图片不留在电脑上）。"""
    rows = models.query(
        "SELECT id FROM image_pool WHERE entry_url=?", (entry_url,))
    for r in rows:
        f = _tmp_file(r["id"])
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass
