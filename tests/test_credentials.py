# -*- coding: utf-8 -*-

"""按应用凭证测试 — 语句注册 / 哈希生成校验 / 中间件三分支 / channel 覆盖"""

from __future__ import annotations

import pytest

from core.db.statements import STATEMENTS


def test_credential_statements_registered():
    """凭证 CRUD×5 + get_session_owner 双方言语句已注册且占位符数量一致"""
    for name in (
        "get_credential_by_hash",
        "get_credential_by_id",
        "insert_credential",
        "list_credentials",
        "revoke_credential",
        "get_session_owner",
    ):
        assert name in STATEMENTS, name
        st = STATEMENTS[name]
        assert st.pg and st.mysql, name
        assert st.pg.count("$") == st.mysql.count("$"), name  # 双方言占位符数一致


# ---------------------------------------------------------------
# core/credentials.py — 哈希 / 生成 / 校验纯函数
# ---------------------------------------------------------------
from core.credentials import generate_api_key, hash_api_key, verify_credential


def test_hash_api_key_stable_and_hex():
    h1, h2 = hash_api_key("ak_test_123"), hash_api_key("ak_test_123")
    assert h1 == h2 and len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)
    assert hash_api_key("other") != h1


def test_generate_api_key_format():
    k = generate_api_key()
    assert k.startswith("ak_") and len(k) >= 16
    assert generate_api_key() != k  # 随机不重复


class _FakeDB:
    is_initialized = True  # 对齐 DatabaseManager property（中间件三分支检查）

    def __init__(self, row=None, exc=None):
        self.row, self.exc, self.calls = row, exc, []

    async def fetch_named(self, name, *args):
        self.calls.append((name, args))
        if self.exc:
            raise self.exc
        return self.row


@pytest.mark.asyncio
async def test_verify_credential_hit():
    db = _FakeDB(row={"id": 1, "cred_type": "api_key", "app_id": "a",
                      "channel": "acme", "scopes": "chat"})
    out = await verify_credential(db, "ak_x")
    assert out["app_id"] == "a" and db.calls[0][0] == "get_credential_by_hash"


@pytest.mark.asyncio
async def test_verify_credential_miss_and_exception_propagate():
    assert await verify_credential(_FakeDB(row=None), "ak_x") is None
    with pytest.raises(RuntimeError):
        await verify_credential(_FakeDB(exc=RuntimeError("db down")), "ak_x")


# ---------------------------------------------------------------
# server._api_key_auth_middleware — 按应用凭证校验三分支（D3/D7）
# ---------------------------------------------------------------

def _scope(path="/chat/stream", headers=None):
    return {"type": "http", "path": path, "headers": headers or [],
            "query_string": b""}


async def _call(mw, scope, record=None):
    """驱动中间件，返回 HTTP 状态码；record 传 dict 时由下游回显收到的 state。"""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def send(msg):
        messages.append(msg)

    await mw(scope, receive, send)
    status = next(
        (m["status"] for m in messages if m["type"] == "http.response.start"),
        None,
    )
    return status


def _mw_with_recorder(db=None, record=None, **kw):
    """构造把下游 state 记录进 record 的中间件。"""
    from server import _api_key_auth_middleware

    async def downstream(scope, receive, send):
        if record is not None:
            record.update(scope.get("state", {}))
        from fastapi.responses import JSONResponse
        await JSONResponse({})(scope, receive, send)

    return _api_key_auth_middleware(downstream, **kw)


@pytest.mark.asyncio
async def test_mw_master_key_passes_without_credential(monkeypatch):
    monkeypatch.setenv("API_KEY", "master123")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    record = {}
    mw = _mw_with_recorder(record=record, auth_required=True, api_key="")
    assert await _call(mw, _scope(headers=[(b"x-api-key", b"master123")])) == 200
    assert record["api_key"] == "master123"
    assert "credential" not in record  # 主 key 不注入 credential（D7）


@pytest.mark.asyncio
async def test_mw_credential_hit_injects_state(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    db = _FakeDB(row={"id": 1, "cred_type": "api_key", "app_id": "a",
                      "channel": "acme", "scopes": "chat"})
    record = {}
    mw = _mw_with_recorder(record=record, auth_required=True, api_key="",
                           db_provider=lambda: db)
    status = await _call(mw, _scope(headers=[(b"x-api-key", b"ak_good")]),
                         record=record)
    assert status == 200
    assert record["credential"]["channel"] == "acme"
    assert record["api_key"] == "ak_good"
    assert db.calls[0][0] == "get_credential_by_hash"


@pytest.mark.asyncio
async def test_mw_unknown_key_401_and_db_down_503(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    # 凭证表未命中 → 401
    mw = _mw_with_recorder(auth_required=True, api_key="",
                           db_provider=lambda: _FakeDB(row=None))
    assert await _call(mw, _scope(headers=[(b"x-api-key", b"ak_bad")])) == 401

    # 查询异常 → 503（D3）
    mw503 = _mw_with_recorder(auth_required=True, api_key="",
                              db_provider=lambda: _FakeDB(exc=RuntimeError()))
    assert await _call(mw503, _scope(headers=[(b"x-api-key", b"ak_x")])) == 503

    # DB 未初始化/未配置 → 503（D3）
    mw_nodb = _mw_with_recorder(auth_required=True, api_key="",
                                db_provider=lambda: None)
    assert await _call(mw_nodb, _scope(headers=[(b"x-api-key", b"ak_x")])) == 503


@pytest.mark.asyncio
async def test_mw_missing_key_401(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    # 缺 key → 401（未接 db_provider）
    mw = _mw_with_recorder(auth_required=True, api_key="")
    assert await _call(mw, _scope()) == 401
    # 未接 db_provider 时错误 key 维持旧行为 401（与 test_server_auth 兼容）
    assert await _call(mw, _scope(headers=[(b"x-api-key", b"ak_wrong")])) == 401


# ---------------------------------------------------------------
# resolve_channel — 凭证 channel 强制覆盖（D2）
# ---------------------------------------------------------------
from core.credentials import resolve_channel


class _Req:
    def __init__(self, cred=None):
        self.scope = {"state": {}} if cred is None else {"state": {"credential": cred}}


def test_resolve_channel_override():
    assert resolve_channel(_Req(), "web") == "web"            # 无凭证（含主 key）不动
    cred = {"channel": "acme", "app_id": "a"}
    assert resolve_channel(_Req(cred), "web") == "acme"        # 强制覆盖（防归因伪造）
    assert resolve_channel(_Req(cred), "acme") == "acme"       # 一致不搅动
