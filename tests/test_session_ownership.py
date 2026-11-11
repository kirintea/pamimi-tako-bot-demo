# -*- coding: utf-8 -*-

"""session 级端点归属校验测试（缺口 7，D4/D5 方案 A）

authorize 语义（_authorize_session_access）：
  ① session_exists(uid, sid) 配对命中 → 放行（本人，不查 PG）
  ② 配对未命中 → 查 PG get_session_owner(sid)：
     owner == uid → 放行；owner 他人 → viewer 为 root 放行否则 403；
     owner 无痕迹（None）→ 放行（路线 C 首 turn 竞态，D5-A）
  ③ PG 未配置/未初始化 → 跳过 ② 放行（dev 无库，D5-A）
  ④ PG 查询异常 → 503
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.chat as chat_mod
from api.chat import _authorize_session_access


class _Mgr:
    def __init__(self, exists: bool = True):
        self.exists = exists
        self.calls = []

    async def session_exists(self, uid, sid):
        self.calls.append((uid, sid))
        return self.exists


class _FakeDB:
    """归属查询假库（对齐 DatabaseManager.fetch_named 接口）"""

    is_initialized = True

    def __init__(self, row=None, exc=None):
        self.row, self.exc, self.calls = row, exc, []

    async def fetch_named(self, name, *args):
        self.calls.append((name, args))
        if self.exc:
            raise self.exc
        return self.row


def _req(mgr=None, db=None, with_db_attr=True) -> SimpleNamespace:
    state = SimpleNamespace(
        session_manager=mgr if mgr is not None else _Mgr(),
        database_manager=db,
    )
    if not with_db_attr:
        del state.database_manager
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app, scope={"state": {}})


async def _check(req, uid="u1", sid="s1"):
    """运行 authorize，返回 None（放行）或 HTTPException"""
    try:
        return await _authorize_session_access(req, uid, sid)
    except HTTPException as e:
        return e


# ----------------------------------------------------------------
# ① 配对命中（本人）→ 放行，不查 PG
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_pair_hit_allows():
    mgr = _Mgr(exists=True)
    db = _FakeDB(row={"user_id": "someone-else"})
    req = _req(mgr=mgr, db=db)
    assert await _check(req) is None
    assert mgr.calls == [("u1", "s1")]
    assert db.calls == []  # 配对命中不落 PG


# ----------------------------------------------------------------
# ② 他人会话 → 403；root 绕过
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_foreign_session_403(monkeypatch):
    async def _role(request, viewer):
        return "normal"

    monkeypatch.setattr(chat_mod, "_resolve_viewer_role", _role)
    db = _FakeDB(row={"user_id": "other"})
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert isinstance(out, HTTPException) and out.status_code == 403
    # get_session_owner 单参（$1 重复，双方言均传一次）
    assert db.calls == [("get_session_owner", ("s1",))]


@pytest.mark.asyncio
async def test_root_bypass(monkeypatch):
    async def _role(request, viewer):
        return "root"

    monkeypatch.setattr(chat_mod, "_resolve_viewer_role", _role)
    db = _FakeDB(row={"user_id": "other"})
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert out is None


@pytest.mark.asyncio
async def test_owner_match_via_pg_allows():
    """配对未命中但 PG 显示 owner==uid（如 KV 过期后从 PG 回填）→ 放行"""
    db = _FakeDB(row={"user_id": "u1"})
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert out is None


# ----------------------------------------------------------------
# ③ D5-A：无痕迹 / 无 PG → 放行
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_session_allows_d5a():
    """owner 为 None（首 turn 竞态，meta 未写入）→ 放行"""
    db = _FakeDB(row=None)
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert out is None


@pytest.mark.asyncio
async def test_no_pg_skips_to_allow_d5a():
    """database_manager 缺失（dev 无库）→ 跳过归属查询直接放行"""
    out = await _check(_req(mgr=_Mgr(exists=False), db=None))
    assert out is None


@pytest.mark.asyncio
async def test_pg_not_initialized_skips_to_allow_d5a():
    """database_manager 存在但未初始化（URL 未配置）→ 跳过放行"""
    db = _FakeDB()
    db.is_initialized = False
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert out is None


# ----------------------------------------------------------------
# ④ PG 查询异常 → 503
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_error_503():
    db = _FakeDB(exc=RuntimeError("db down"))
    out = await _check(_req(mgr=_Mgr(exists=False), db=db))
    assert isinstance(out, HTTPException) and out.status_code == 503
