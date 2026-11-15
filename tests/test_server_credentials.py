"""新服务器凭据初始化不覆盖现有密钥，不保存明文密码。"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

from cryptography.fernet import Fernet
from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('server_credentials', ROOT/'scripts/init_server_credentials.py')
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


class ServerCredentialTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT/'backups/test-artifacts'/('credentials-'+uuid.uuid4().hex)
        self.folder.mkdir(parents=True)
        self.access = self.folder/'access.json'
        self.key = self.folder/'kongfz.key'

    def initialize(self, passwords=('offline-test-password', 'offline-test-password')):
        with patch.object(sys, 'argv', ['init', '--access-file', str(self.access), '--key-file', str(self.key)]), \
             patch('builtins.input', return_value='test-user'), \
             patch.object(initializer.getpass, 'getpass', side_effect=passwords):
            initializer.main()

    def test_new_credentials_are_usable_and_do_not_contain_plain_password(self):
        self.initialize()
        value = json.loads(self.access.read_text())
        self.assertEqual(value['username'], 'test-user')
        self.assertTrue(check_password_hash(value['password_hash'], 'offline-test-password'))
        self.assertNotIn('offline-test-password', self.access.read_text())
        cipher = Fernet(self.key.read_bytes())
        self.assertEqual(cipher.decrypt(cipher.encrypt(b'test')), b'test')

    def test_existing_file_is_preserved_and_other_file_is_not_created(self):
        self.key.write_bytes(b'keep-existing-key')
        with self.assertRaisesRegex(SystemExit, '不要覆盖'):
            self.initialize()
        self.assertEqual(self.key.read_bytes(), b'keep-existing-key')
        self.assertFalse(self.access.exists())

    def test_password_mismatch_creates_nothing(self):
        with self.assertRaisesRegex(SystemExit, '未保存'):
            self.initialize(('one', 'two'))
        self.assertFalse(self.access.exists())
        self.assertFalse(self.key.exists())
