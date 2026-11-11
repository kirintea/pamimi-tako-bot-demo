# -*- coding: utf-8 -*-
"""凭证管理路由测试 —— root 门控 / 明文一次性 / 列表掩码 / 吊销幂等 / 404 / 503"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin_credentials import router as cred_router


class FakeUserService:
    def __init__(self, role: str = "root"):
        self._role = role

    async def get_role(self, uid):
        if not uid:
            return "normal"
        return self._role


class FakeDB:
    is_initialized = True
    dialect = "postgres"

    def __init__(self, existing_hash: str | None = None, rows=None):
        self.existing_hash = existing_hash
        self.rows = rows if rows is not None else []
        self.inserted: list[tuple] = []
        self.executed: list[tuple] = []

    async def fetch_named(self, name, *args):
        if name == "get_credential_by_hash":
            if args and args[0] == self.existing_hash:
                return {"id": 99, "cred_type": "api_key", "app_id": "dup",
                        "channel": "x", "scopes": "chat"}
            return None
        if name == "get_credential_by_id":
            for r in self.rows:
                if r["id"] == args[0]:
                    return r
            return None
        if name == "list_credentials":
            return list(self.rows)
        return None

    async def insert_returning_id(self, name, *args):
        self.inserted.append((name, args))
        return 42

    async def execute_named(self, name, *args):
        self.executed.append((name, args))
        return "UPDATE 1"

    def now(self):
        from datetime import datetime
        return datetime(2026, 11, 11, 12, 0, 0)


def _app(role: str = "root", db=None) -> FastAPI:
    app = FastAPI()
    app.include_router(cred_router)
    app.state.database_manager = db if db is not None else FakeDB()
    app.state.user_service = FakeUserService(role)
    return app


def test_non_root_gets_403():
    client = TestClient(_app(role="normal"))
    res = client.get("/admin/credentials", params={"viewer": "u1"})
    assert res.status_code == 403


def test_create_returns_plaintext_once_and_inserts_hash():
    db = FakeDB()
    client = TestClient(_app(db=db))
    res = client.post(
        "/admin/credentials?viewer=root1",
        json={"app_id": "acme", "channel": "acme_im", "description": "demo"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == 42
    assert body["key"].startswith("ak_") and len(body["key"]) >= 16
    assert body["key_prefix"] == body["key"][:8]
    # 落库参数：cred_type, hash(key), prefix, app_id, channel, scopes, desc, now
    name, args = db.inserted[0]
    assert name == "insert_credential"
    from core.credentials import hash_api_key
    assert args[1] == hash_api_key(body["key"])   # 只存哈希（D1）


def test_create_duplicate_hash_409():
    from core.credentials import hash_api_key, generate_api_key
    key = generate_api_key()
    db = FakeDB(existing_hash=hash_api_key(key))
    client = TestClient(_app(db=db))
    res = client.post("/admin/credentials?viewer=root1",
                      json={"app_id": "a", "channel": "c", "key": key})
    assert res.status_code == 409
    assert db.inserted == []                       # 撞库不落库


def test_create_short_key_422():
    client = TestClient(_app())
    res = client.post("/admin/credentials?viewer=root1",
                      json={"app_id": "a", "channel": "c", "key": "short"})
    assert res.status_code == 422


def test_list_masks_secret():
    db = FakeDB(rows=[{"id": 1, "cred_type": "api_key", "key_prefix": "ak_abcd",
                       "app_id": "a", "channel": "c", "scopes": "chat",
                       "status": "active", "description": None,
                       "created_at": "2026-11-11T12:00:00", "revoked_at": None}])
    client = TestClient(_app(db=db))
    res = client.get("/admin/credentials", params={"viewer": "root1"})
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["key_prefix"] == "ak_abcd"
    assert "key" not in item and "key_hash" not in item


def test_revoke_404_idempotent_and_update():
    row = {"id": 5, "status": "active", "revoked_at": None}
    db = FakeDB(rows=[row])
    client = TestClient(_app(db=db))
    assert client.post("/admin/credentials/999/revoke?viewer=r",
                       json={}).status_code == 404
    res = client.post("/admin/credentials/5/revoke?viewer=r", json={})
    assert res.status_code == 200 and db.executed          # 首次执行 UPDATE
    row["status"], row["revoked_at"] = "revoked", "2026-11-11T12:00:00"
    n = len(db.executed)
    res2 = client.post("/admin/credentials/5/revoke?viewer=r", json={})
    assert res2.status_code == 200 and len(db.executed) == n   # 幂等不再 UPDATE


def test_db_not_initialized_503():
    class DownDB(FakeDB):
        is_initialized = False
    client = TestClient(_app(db=DownDB()))
    res = client.get("/admin/credentials", params={"viewer": "root1"})
    assert res.status_code == 503
