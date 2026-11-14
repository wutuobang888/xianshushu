# -*- coding: utf-8 -*-
"""初始化数据库 + 写入默认模板。在项目根目录执行：python scripts/init_db.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402
from services import template_service  # noqa: E402

models.init_db()
template_service.seed_defaults()
print("数据库初始化完成：", models.config.DATABASE_PATH)
