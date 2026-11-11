# -*- coding: utf-8 -*-
"""一次性回填 stats_daily 统计事实表（幂等，可断点续跑）

用法：
    .venv/Scripts/python.exe scripts/backfill_stats_daily.py

按 conversations（status='active'）折算 uv/session_active/turn/turn_legacy，
按 sessions（status='active'）折算 session_new（渠道派生自首条 active 消息）。
stat_day 日界按 database.timezone（与写时一致）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=False)

from core.config import ConfigManager
from core.database import DatabaseManager
from core.stats import conversation_facts, session_created_fact

BATCH = 5000


def _local_date(value: datetime | None, tz: str):
    """存储的时间列 → 本地日界（PG aware 转配置时区；MySQL naive 原样）"""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(tz))
    return value.date()


async def _backfill_conversations(db: DatabaseManager, tz: str) -> int:
    """conversations → uv / session_active / turn / turn_legacy"""
    total = 0
    cursor = 0
    while True:
        rows = await db.fetch(
            'SELECT "id", "user_id", "session_id", "role", "channel", "turn_id", "created_at" '
            'FROM "conversations" WHERE "status" = \'active\' AND "id" > $1 '
            'ORDER BY "id" ASC LIMIT $2',
            cursor, BATCH,
        )
        if not rows:
            break
        facts = []
        for r in rows:
            day = _local_date(r["created_at"], tz)
            facts.extend(conversation_facts(
                day, r["channel"], r["user_id"], r["session_id"],
                r["role"], r["turn_id"], r["id"],
            ))
        await db.record_stats_facts(facts)
        total += len(rows)
        cursor = rows[-1]["id"]
        logger.info("conversations 回填进度: {} 行 (cursor={})", total, cursor)
    return total


async def _backfill_sessions(db: DatabaseManager, tz: str) -> int:
    """sessions → session_new（渠道 = 首条 active 消息 MIN(channel)）"""
    total = 0
    cursor = ""
    while True:
        rows = await db.fetch(
            'SELECT s."id", s."created_at", '
            'COALESCE((SELECT MIN(c."channel") FROM "conversations" c '
            '          WHERE c."session_id" = s."id" AND c."status" = \'active\'), \'\') AS "channel" '
            'FROM "sessions" s WHERE s."status" = \'active\' AND s."id" > $1 '
            'ORDER BY s."id" ASC LIMIT $2',
            cursor, BATCH,
        )
        if not rows:
            break
        facts = [
            session_created_fact(_local_date(r["created_at"], tz), r["channel"], r["id"])
            for r in rows
        ]
        await db.record_stats_facts(facts)
        total += len(rows)
        cursor = rows[-1]["id"]
        logger.info("sessions 回填进度: {} 行 (cursor={})", total, cursor)
    return total


async def main() -> None:
    config = ConfigManager.get_instance().load()
    db = DatabaseManager(config.database)
    await db.initialize()
    try:
        tz = config.database.timezone
        n_conv = await _backfill_conversations(db, tz)
        n_sess = await _backfill_sessions(db, tz)
        logger.info("回填完成: conversations={} sessions={}", n_conv, n_sess)
    finally:
        await db.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
