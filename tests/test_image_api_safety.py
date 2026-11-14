# -*- coding: utf-8 -*-
"""图片缓存及接口重试的离线回归，不下载或上传真实图片。"""
import sqlite3
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import models
from services import image_service as images
from services import xgj_client as xgj


class ImageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(models.SCHEMA)
        self.db.execute("INSERT INTO book_sources(kongfz_url,book_title) VALUES('offline','离线测试书')")
        self.addCleanup(self.db.close)
        def query(sql, params=(), one=False):
            rows = self.db.execute(sql, params).fetchall()
            return (rows[0] if rows else None) if one else rows
        for target, side_effect in (("query", query), ("execute", lambda s, p=():
                self.db.execute(s, p).lastrowid)):
            patcher = patch.object(models, target, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.file = Mock()
        self.file.exists.return_value = False
        self.original_cache = Mock()
        self.original_cache.exists.return_value = False
        for target, kwargs in (("collect_target", {"return_value": "offline"}),
                ("_tmp_file", {"return_value": self.file}),
                ("_original_file", {"return_value": self.original_cache}),
                ("_dewatermark", {"return_value": b"processed-image"}),
                ("_download", {"return_value": b"offline-image"}),
                ("_rehost", {"return_value": "https://example.invalid/fresh.jpg"})):
            patcher = patch.object(images, target, **kwargs)
            result = patcher.start()
            setattr(self, target.lstrip("_"), result)
            self.addCleanup(patcher.stop)
        patcher = patch.object(Path, "mkdir")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_collection_actually_caches_image(self):
        self.assertEqual(images.collect_xhs(["https://example.invalid/image"], entry_url="offline"), 1)
        self.file.write_bytes.assert_called_once_with(b"offline-image")
        self.assertEqual(self.db.execute("SELECT final_url FROM image_pool").fetchone()[0], "/pool_img/1")

    def test_duplicate_collection_reuses_cache(self):
        images.collect_xhs(["https://example.invalid/image"], entry_url="offline")
        self.file.exists.return_value = True
        self.assertEqual(images.collect_xhs(["https://example.invalid/image"], entry_url="offline"), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM image_pool").fetchone()[0], 1)
        self.download.assert_called_once()

    def test_download_failure_is_not_counted_as_success(self):
        self.download.side_effect = RuntimeError("离线下载失败")
        self.assertEqual(images.collect_xhs(["https://example.invalid/image"], entry_url="offline"), 0)
        self.file.write_bytes.assert_not_called()

    def test_explicit_tab_target_overrides_old_global_target(self):
        self.db.execute("INSERT INTO book_sources(kongfz_url,book_title) VALUES('book-b','书B')")
        images.collect_xhs(["https://example.invalid/image"], entry_url="book-b")
        self.assertEqual(self.db.execute("SELECT entry_url FROM image_pool").fetchone()[0], "book-b")
        self.collect_target.assert_not_called()

    def test_missing_target_is_rejected_before_insertion(self):
        with self.assertRaisesRegex(ValueError, "更新收图脚本"):
            images.collect_xhs(["https://example.invalid/image"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM image_pool").fetchone()[0], 0)
        self.download.assert_not_called()

    def test_unknown_target_is_rejected_before_download(self):
        with self.assertRaisesRegex(ValueError, "目标不存在"):
            images.collect_xhs(["https://example.invalid/image"], entry_url="unknown")
        self.download.assert_not_called()

    def test_http_collection_returns_explicit_target_and_book_title(self):
        from flask import Flask
        from routes.product_routes import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        response = app.test_client().post('/api/images/collect', json={
            'entry_url': 'offline', 'urls': ['https://example.invalid/image']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['target'], 'offline')
        self.assertEqual(response.json['title'], '离线测试书')

    def test_manual_kongfz_collection_caches_with_source_and_target(self):
        from flask import Flask
        from routes.product_routes import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        response = app.test_client().post('/api/images/collect', json={
            'source': 'kongfz', 'entry_url': 'offline', 'note_url': 'https://book.kongfz.com/123/456/',
            'note_title': '卖家实拍', 'urls': ['https://img1.kfzimg.com/test-Ab_b.jpg']})
        self.assertEqual(response.status_code, 200)
        row = self.db.execute('SELECT * FROM image_pool').fetchone()
        self.assertEqual(row['source'], 'kongfz_manual')
        self.assertEqual(row['entry_url'], 'offline')
        self.assertEqual(row['final_url'], '/pool_img/1')
        self.assertEqual(row['source_url'], 'https://book.kongfz.com/123/456/')

    def test_manual_kongfz_rejects_non_detail_or_non_kongfz_images(self):
        for page, urls in [
            ('https://item.kongfz.com/book/1.html', ['https://img1.kfzimg.com/test-Ab_b.jpg']),
            ('https://book.kongfz.com/', ['https://img1.kfzimg.com/test-Ab_b.jpg']),
            ('https://book.kongfz.com/1/2/', ['https://other.invalid/book.jpg'])]:
            with self.assertRaises(ValueError):
                images.collect_kongfz(urls, note_url=page, entry_url='offline')
        self.download.assert_not_called()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM image_pool').fetchone()[0], 0)

    def test_http_missing_target_and_zero_download_are_not_success(self):
        from flask import Flask
        from routes.product_routes import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        client = app.test_client()
        response = client.post('/api/images/collect', json={'urls': ['https://example.invalid/image']})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json['ok'])
        self.download.side_effect = RuntimeError('离线失败')
        response = client.post('/api/images/collect', json={'entry_url': 'offline', 'urls': ['https://example.invalid/image']})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json['ok'])

    def add_image(self, source="kongfz", selected=1, final=None):
        return self.db.execute(
            "INSERT INTO image_pool(entry_url,image_url,source,selected,final_url) VALUES(?,?,?,?,?)",
            ("offline", "https://example.invalid/image", source, selected, final)).lastrowid

    def test_explicit_selection_includes_unchecked_image(self):
        iid = self.add_image(selected=0)
        self.assertEqual(images.publish_image_urls("offline", [iid]), ["https://example.invalid/fresh.jpg"])
        self.assertEqual(images.publish_image_urls("offline"), [])

    def test_empty_explicit_selection_uses_no_images(self):
        self.add_image()
        self.assertEqual(images.publish_image_urls("offline", []), [])

    def test_explicit_images_are_published_in_user_order(self):
        first = self.add_image()
        second = self.db.execute("INSERT INTO image_pool(entry_url,image_url,source) VALUES(?,?,?)",
                                 ('offline', 'https://example.invalid/second', 'kongfz')).lastrowid
        self.rehost.side_effect = ['https://example.invalid/second-clean', 'https://example.invalid/first-clean']
        self.assertEqual(images.publish_image_urls('offline', [second, first]),
                         ['https://example.invalid/second-clean', 'https://example.invalid/first-clean'])
        self.assertEqual([call.args[1] for call in self.rehost.call_args_list],
                         [f'book{second}.jpg', f'book{first}.jpg'])

    def test_wrong_book_missing_and_duplicate_images_block_before_upload(self):
        first = self.add_image()
        wrong = self.db.execute("INSERT INTO image_pool(entry_url,image_url,source) VALUES(?,?,?)",
                                ('different-book', 'https://example.invalid/wrong', 'kongfz')).lastrowid
        for ids in ([first, first], [wrong], [999999], None):
            if ids is None:
                continue
            with self.assertRaises(ValueError):
                images.publish_image_urls('offline', ids)
        self.rehost.assert_not_called()

    def test_missing_processed_cache_blocks_publication(self):
        self.add_image(final="https://example.invalid/expired.jpg")
        with self.assertRaisesRegex(ValueError, "暂停上架"):
            images.publish_image_urls("offline")
        self.rehost.assert_not_called()

    def test_xhs_stale_url_is_rehosted_from_source(self):
        self.add_image(source="xhs", final="/pool_img/1")
        self.assertEqual(images.publish_image_urls("offline"), ["https://example.invalid/fresh.jpg"])
        self.download.assert_called_once()
        self.rehost.assert_called_once()

    def test_repeated_publication_uses_local_material_without_deleting_it(self):
        self.add_image(final="https://example.invalid/expired.jpg")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"processed"
        images.publish_image_urls("offline")
        images.publish_image_urls("offline")
        self.assertEqual(self.rehost.call_count, 2)
        self.file.unlink.assert_not_called()

    def test_manual_original_cache_is_cleaned_before_upload(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"offline-image"
        self.assertEqual(images.publish_image_urls("offline", [iid]), ["https://example.invalid/fresh.jpg"])
        self.dewatermark.assert_called_once_with(b"offline-image")
        self.rehost.assert_called_once_with(b"processed-image", f"book{iid}.jpg")
        self.file.unlink.assert_not_called()
        self.file.read_bytes.return_value = b"processed-image"
        images.publish_image_urls("offline", [iid])
        self.dewatermark.assert_called_once()
        self.download.assert_not_called()

    def test_processed_manual_cache_never_falls_back_to_original(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"processed-image"
        models.set_setting(f"image_cleaned_sha256:{iid}", hashlib.sha256(b"processed-image").hexdigest())
        models.set_setting(f"image_processing_method:{iid}", "crop-v1")
        self.assertEqual(images.publish_image_urls("offline", [iid]), ["https://example.invalid/fresh.jpg"])
        self.rehost.assert_called_once_with(b"processed-image", f"book{iid}.jpg")

    def test_original_digest_does_not_match_edited_cache(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        models.set_setting(f"image_original_sha256:{iid}", hashlib.sha256(b"offline-image").hexdigest())
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"processed-image"
        images.publish_image_urls("offline", [iid])
        self.download.assert_called_once()
        self.dewatermark.assert_called_once_with(b"offline-image")
        self.rehost.assert_called_once()

    def test_legacy_repaired_cache_is_recropped_from_original_backup(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"old-repaired-image"
        self.original_cache.exists.return_value = True
        self.original_cache.read_bytes.return_value = b"offline-original"
        models.set_setting(f"image_cleaned_sha256:{iid}", hashlib.sha256(b"old-repaired-image").hexdigest())
        images.publish_image_urls("offline", [iid])
        self.dewatermark.assert_called_once_with(b"offline-original")
        self.download.assert_not_called()
        self.assertEqual(models.get_setting(f"image_processing_method:{iid}"), "crop-v1")
        self.original_cache.write_bytes.assert_not_called()

    def test_failed_processed_upload_reports_reason_and_preserves_cache(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"processed-image"
        self.rehost.side_effect = RuntimeError("临时图床连接失败")
        with self.assertRaisesRegex(ValueError, "暂停上架.*临时图床连接失败"):
            images.publish_image_urls("offline", [iid])
        self.file.unlink.assert_not_called()

    def test_unmarked_legacy_cache_is_cleaned_without_source_download(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"unknown-image"
        self.download.side_effect = RuntimeError("原图无法访问")
        self.assertEqual(images.publish_image_urls("offline", [iid]), ["https://example.invalid/fresh.jpg"])
        self.rehost.assert_called_once_with(b"processed-image", f"book{iid}.jpg")
        self.dewatermark.assert_called_once_with(b"unknown-image")
        self.download.assert_not_called()

    def test_manual_dewatermark_preview_does_not_need_image_host(self):
        iid = self.add_image(source="kongfz_manual")
        self.rehost.side_effect = RuntimeError("图床不可用")
        self.assertEqual(images.dewatermark_image(iid), f"/pool_img/{iid}")
        self.original_cache.write_bytes.assert_called_once_with(b"offline-image")
        self.file.write_bytes.assert_called_once_with(b"processed-image")
        self.rehost.assert_not_called()

    def test_upload_failure_never_uses_watermarked_original(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.file.exists.return_value = True
        self.file.read_bytes.return_value = b"offline-image"
        self.rehost.side_effect = RuntimeError("图床不可用")
        with self.assertRaisesRegex(ValueError, "暂停上架.*图床不可用"):
            images.publish_image_urls("offline", [iid])
        self.file.write_bytes.assert_called_once_with(b"processed-image")
        self.original_cache.write_bytes.assert_called_once_with(b"offline-image")

    def test_restore_preserves_files_and_uses_original_backup(self):
        iid = self.add_image(source="kongfz_manual", final="/pool_img/1")
        self.original_cache.exists.return_value = True
        self.original_cache.read_bytes.return_value = b"offline-image"
        images.restore_image(iid)
        self.file.write_bytes.assert_called_once_with(b"offline-image")
        self.file.unlink.assert_not_called()
        self.original_cache.unlink.assert_not_called()


class ApiRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = xgj.XgjClient(app_key="offline", app_secret="offline")
        self.response = Mock(status_code=200)
        self.response.json.return_value = {"code": 0, "data": {}}

    def test_reads_can_retry_after_timeout(self):
        with patch.object(xgj.requests, "post", side_effect=[
                xgj.requests.exceptions.ReadTimeout(), self.response]) as post, \
                patch.object(xgj.time, "sleep"):
            self.client.call("/api/open/order/detail")
            self.assertEqual(post.call_count, 2)

    def test_writes_are_never_repeated_after_unknown_result(self):
        for path in ("/api/open/product/create", "/api/open/product/publish", "/api/open/order/ship"):
            with self.subTest(path=path), patch.object(xgj.requests, "post", side_effect=
                    xgj.requests.exceptions.ReadTimeout()) as post:
                with self.assertRaises(xgj.XgjUncertainError):
                    self.client.call(path)
                self.assertEqual(post.call_count, 1)

    def test_business_error_does_not_retry(self):
        self.response.json.return_value = {"code": 100001, "message": "离线状态错误"}
        with patch.object(xgj.requests, "post", return_value=self.response) as post:
            with self.assertRaises(xgj.XgjError):
                self.client.call("/api/open/order/ship")
            self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
