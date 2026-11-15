"""首次分发启动入口：验证数据隔离及默认不启动业务任务。"""
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('local_launch_test_module',ROOT/'scripts/local_launch.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class LocalLaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder=ROOT/'backups'/'test-artifacts'/('launcher-'+uuid.uuid4().hex)
        self.folder.mkdir(parents=True)
        self.previous=Path.cwd();self.addCleanup(os.chdir,self.previous)
        root=patch.object(launcher,'ROOT',self.folder);root.start();self.addCleanup(root.stop)
        env=patch.dict(os.environ,{},clear=False);env.start();self.addCleanup(env.stop)

    def run_mode(self,*args):
        with patch.object(sys,'argv',['local_launch.py',*args,'--no-browser']), \
             patch.object(launcher.socket,'create_connection',side_effect=ConnectionRefusedError), \
             patch.object(launcher.runpy,'run_path') as start:
            launcher.main()
            return start

    def test_demo_clears_inherited_credentials_and_pins_demo_data(self):
        os.environ.update(XGJ_APP_KEY='offline-key',XGJ_APP_SECRET='offline-secret',
                          XIANSHUSHU_DATABASE='unrelated.db',XIANSHUSHU_SCHEDULER='1')
        self.run_mode('demo').assert_called_once()
        self.assertNotIn('XGJ_APP_KEY',os.environ)
        self.assertNotIn('XGJ_APP_SECRET',os.environ)
        self.assertEqual(os.environ['XIANSHUSHU_SCHEDULER'],'0')
        self.assertEqual(Path(os.environ['XIANSHUSHU_DATABASE']),self.folder/'data/demo/database.db')

    def test_live_is_separate_and_does_not_enable_tasks_by_default(self):
        self.run_mode('live')
        self.assertEqual(os.environ['DEMO_MODE'],'0')
        self.assertEqual(os.environ['XIANSHUSHU_SCHEDULER'],'0')
        self.assertEqual(Path(os.environ['KONGFZ_USER_DATA_DIR']),self.folder/'data/live/browser_data')

    def test_tasks_must_be_explicit_and_only_for_live(self):
        self.run_mode('live','--tasks')
        self.assertEqual(os.environ['XIANSHUSHU_SCHEDULER'],'1')
        with self.assertRaises(SystemExit):self.run_mode('demo','--tasks')

    def test_existing_demo_credentials_stop_startup_without_deleting_data(self):
        path=self.folder/'data/demo/database.db';path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO settings VALUES('xgj_app_key','offline-existing-key')")
        with self.assertRaisesRegex(SystemExit,'演示库已保存平台凭据'):self.run_mode('demo')
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM settings').fetchone()[0],1)

    def test_occupied_port_does_not_open_or_stop_someone_elses_server(self):
        with patch.object(sys,'argv',['local_launch.py','demo','--no-browser']), \
             patch.object(launcher.socket,'create_connection'), \
             patch.object(launcher.runpy,'run_path') as start:
            with self.assertRaisesRegex(SystemExit,'5000端口已占用'):launcher.main()
            start.assert_not_called()
