# -*- coding: utf-8 -*-

"""get_conversation_history 的 status 过滤契约

中间链路（thinking / tool_call）落库后会被 chat_service「轮内压缩」软删成
``status='archived'``，让它们退出 UI 回放与 PG 回填。观测台要看见这些行，
所以取历史必须能显式放开 status 过滤；而对话恢复路径必须保持只取 active。
"""

from __future__ import annotations

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _SqlCaptureDB(DatabaseManager):
    """只截获 SQL，不连库"""

    def __init__(self) -> None:
        super().__init__(DatabaseConfig())
        self.captured: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.captured.append((sql, args))
        return []


def _sql_of(db: _SqlCaptureDB) -> str:
    assert db.captured, "get_conversation_history 未发起查询"
    return db.captured[0][0]


async def test_history_default_excludes_archived():
    db = _SqlCaptureDB()
    await db.get_conversation_history("u1", "s1")
    sql = _sql_of(db)
    assert '"status" = \'active\'' in sql


async def test_history_include_archived_keeps_internal_rows():
    db = _SqlCaptureDB()
    await db.get_conversation_history("u1", "s1", include_archived=True)
    sql = _sql_of(db)
    assert '"status" = \'active\'' not in sql
    # deleted 仍排除：那是真的软删，不是「内部行压缩」
    assert '"status" != \'deleted\'' in sql


async def test_history_before_id_cursor_honours_include_archived():
    db = _SqlCaptureDB()
    await db.get_conversation_history("u1", "s1", before_id=9, include_archived=True)
    sql = _sql_of(db)
    assert '"status" = \'active\'' not in sql
    assert '"status" != \'deleted\'' in sql
