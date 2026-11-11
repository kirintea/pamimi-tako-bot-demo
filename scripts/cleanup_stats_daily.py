# -*- coding: utf-8 -*-
"""清理 stats_daily 事实表（测试数据 / 重置）

用法：
    .venv/Scripts/python.exe scripts/cleanup_stats_daily.py                       # 清空全部
    .venv/Scripts/python.exe scripts/cleanup_stats_daily.py --before 2026-10-27   # 只删某日及之前

stats_daily 完全可由 conversations/sessions 重建（见 scripts/backfill_stats_daily.py），
所以清空后重跑回填即可恢复干净数据。写时钩子会让连真实库的集成测试
（tests/test_session_fork.py、tests/test_mysql_integration.py）在 stats_daily 落
测试记录，用本脚本按需清理。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=False)

from core.config import ConfigManager
from core.database import DatabaseManager


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理 stats_daily 事实表")
    parser.add_argument(
        "--before", type=str, default=None,
        help="只删除 stat_day <= 该日期的行（YYYY-MM-DD）；缺省清空全部",
    )
    args = parser.parse_args()

    config = ConfigManager.get_instance().load()
    db = DatabaseManager(config.database)
    await db.initialize()
    try:
        if args.before:
            before = date.fromisoformat(args.before)
            result = await db.execute(
                'DELETE FROM "stats_daily" WHERE "stat_day" <= $1', before,
            )
        else:
            result = await db.execute('DELETE FROM "stats_daily"')
        logger.info("stats_daily 清理完成: {}", result)
    finally:
        await db.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
