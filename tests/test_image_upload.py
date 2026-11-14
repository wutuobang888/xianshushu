# -*- coding: utf-8 -*-
"""图片上传与裁剪回归：模拟网络，不发布商品。"""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from services import image_service as images


class ImageUploadTests(unittest.TestCase):
    def response(self, data):
        return subprocess.CompletedProcess([], 0, json.dumps(data).encode(), b'')

    def test_windows_upload_uses_current_endpoint_and_system_tls(self):
        response = self.response({'success': True, 'files': [{'url': 'https://n.uguu.se/book.jpg'}]})
        with patch.object(images.subprocess, 'run', return_value=response) as run, \
                patch.object(images.Path, 'is_file', return_value=True), \
                patch.object(images.requests, 'post') as post:
            url = images._rehost(b'processed-image', 'book401.jpg')
        self.assertEqual(url, 'https://n.uguu.se/book.jpg')
        self.assertEqual(run.call_args.args[0][-1], 'https://uguu.se/upload')
        self.assertEqual(run.call_args.kwargs['input'], b'processed-image')
        self.assertNotIn('--insecure', run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs.get('shell', False))
        post.assert_not_called()

    def test_host_errors_do_not_return_original_or_invalid_addresses(self):
        responses = [self.response([]), self.response({'success': False, 'files': []}),
                     self.response({'success': True, 'files': [{'url': 'https://foreign.example/book.jpg'}]}),
                     subprocess.CompletedProcess([], 0, b'not json', b''),
                     subprocess.CompletedProcess([], 22, b'', b'HTTP 500')]
        for response in responses:
            with self.subTest(response=response.stdout), \
                    patch.object(images.Path, 'is_file', return_value=True), \
                    patch.object(images.subprocess, 'run', return_value=response):
                with self.assertRaises(RuntimeError):
                    images._rehost(b'processed-image')

    def test_timeout_keeps_failure_explicit(self):
        with patch.object(images.Path, 'is_file', return_value=True), \
                patch.object(images.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl', 65)):
            with self.assertRaisesRegex(RuntimeError, '本地图片已保留'):
                images._rehost(b'processed-image')

    def test_without_system_curl_uses_same_current_endpoint(self):
        response = Mock()
        response.json.return_value = {'success': True, 'files': [{'url': 'https://n.uguu.se/book.jpg'}]}
        with patch.object(images.Path, 'is_file', return_value=False), \
                patch.object(images.requests, 'post', return_value=response) as post:
            self.assertEqual(images._rehost(b'processed-image'), 'https://n.uguu.se/book.jpg')
        self.assertEqual(post.call_args.args[0], 'https://uguu.se/upload')

    def test_watermark_removal_preserves_book_edge_outside_corner(self):
        image = np.full((480, 480, 3), 65, np.uint8)
        image[80:415, 95:380] = 180
        cv2.putText(image, 'Kfz', (365, 458), cv2.FONT_HERSHEY_SIMPLEX, .65, (240, 240, 240), 2)
        _, encoded = cv2.imencode('.jpg', image)
        original = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        cleaned = cv2.imdecode(np.frombuffer(images._dewatermark(encoded.tobytes()), np.uint8), cv2.IMREAD_COLOR)
        self.assertLess(cleaned.shape[0], original.shape[0])
        self.assertEqual(cleaned.shape[1], original.shape[1])
        self.assertLess(np.abs(original[80:415, 95:380].astype(float) - cleaned[80:415, 95:380]).mean(), 1)
        self.assertLessEqual(cleaned.shape[0], 440)

    def test_invalid_image_blocks_watermark_processing(self):
        with self.assertRaisesRegex(ValueError, '解码失败'):
            images._dewatermark(b'not an image')

    def test_white_book_edge_is_not_extended_into_watermark_background(self):
        image = np.full((480, 480, 3), (45, 35, 120), np.uint8)
        image[420:438, 355:465] = 210
        cv2.putText(image, 'Kfz', (365, 458), cv2.FONT_HERSHEY_SIMPLEX, .65, (240, 240, 240), 2)
        _, encoded = cv2.imencode('.jpg', image)
        cleaned = cv2.imdecode(np.frombuffer(images._dewatermark(encoded.tobytes()), np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(cleaned[434, 400].min(), 180)
        self.assertLessEqual(cleaned.shape[0], 440)

    def test_book_crossing_bottom_uses_right_crop(self):
        image = np.full((480, 480, 3), 65, np.uint8)
        image[80:475, 95:330] = 180
        cv2.putText(image, 'Kfz', (365, 458), cv2.FONT_HERSHEY_SIMPLEX, .65, (240, 240, 240), 2)
        _, encoded = cv2.imencode('.jpg', image)
        cleaned = cv2.imdecode(np.frombuffer(images._dewatermark(encoded.tobytes()), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(cleaned.shape[0], 480)
        self.assertGreater(cleaned.shape[1], 330)
        self.assertLess(cleaned.shape[1], 365)

    def test_book_crossing_both_lines_blocks_crop(self):
        image = np.full((480, 480, 3), 65, np.uint8)
        image[80:475, 95:400] = 180
        cv2.putText(image, 'Kfz', (365, 458), cv2.FONT_HERSHEY_SIMPLEX, .65, (240, 240, 240), 2)
        _, encoded = cv2.imencode('.jpg', image)
        with self.assertRaisesRegex(ValueError, '切到书本'):
            images._dewatermark(encoded.tobytes())

    def test_unrecognized_watermark_does_not_blindly_crop(self):
        _, encoded = cv2.imencode('.jpg', np.full((480, 480, 3), 65, np.uint8))
        with self.assertRaisesRegex(ValueError, '未能可靠识别'):
            images._dewatermark(encoded.tobytes())


if __name__ == '__main__':
    unittest.main()
