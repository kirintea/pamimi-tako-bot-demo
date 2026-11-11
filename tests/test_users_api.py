# -*- coding: utf-8 -*-
"""用户信息接口测试 — POST /users（父调研 §10.1）

覆盖：
  - 服务层（Task 3）：get_or_create_user 返回规范化 display_name/channel
  - 端点层（Task 4 追加）：POST /users 契约（200/400/422/503、D1 extra 仅日志、D2 并发）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.user_service import UserService


class _FakeDB:
    """记录 upsert_user kwargs 的假 DatabaseManager"""

    is_initialized = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upsert_user(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return kwargs["user_id"]


def _service(db=None) -> UserService:
    cfg = SimpleNamespace(agent=SimpleNamespace(root_user_ids=[]))
    return UserService(cfg, db)


# ============================================================
# 服务层（Task 3）
# ============================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_user_passes_channel_to_upsert():
    """既有行为守卫：channel 透传 upsert_user（wecom 懒注册同路），且无 extra"""
    db = _FakeDB()
    svc = _service(db)
    await svc.get_or_create_user("u1", channel="partner_a")
    assert db.calls, "应触发 upsert_user"
    assert db.calls[0]["channel"] == "partner_a"
    assert "extra" not in db.calls[0], "D1：不透传 extra"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_user_returns_normalized_fields():
    svc = _service(_FakeDB())
    record = await svc.get_or_create_user(
        "u1", display_name="  张三  ", channel="partner_a",
    )
    assert record["display_name"] == "张三"   # strip 后回显
    assert record["channel"] == "partner_a"
    assert record["user_id"] == "u1"
    assert record["role"] in ("normal", "root")


@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_user_defaults_web():
    db = _FakeDB()
    svc = _service(db)
    record = await svc.get_or_create_user("u1")
    assert db.calls[0]["channel"] == "web"
    assert "extra" not in db.calls[0]
    assert record["channel"] == "web"
    assert record["display_name"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_user_degrades_without_db():
    """数据库未初始化：不抛错、仍返回记录（既有降级行为回归守卫）"""
    svc = _service(None)
    record = await svc.get_or_create_user("u1")
    assert record["user_id"] == "u1"


# ============================================================
# 端点层（Task 4）
# ============================================================


class _FakeUserService:
    """记录 kwargs 的假 UserService（直接替换 app.state.user_service）"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_or_create_user(self, user_id, display_name=None,
                                 channel="web") -> dict:
        self.calls.append({
            "user_id": user_id, "display_name": display_name,
            "channel": channel,
        })
        return {
            "user_id": user_id, "role": "normal", "is_root": False,
            "display_name": display_name, "channel": channel,
        }


def _build_app(*, db=None, user_service=None):
    """最小应用：只挂 users_router，无 lifespan（镜像 tests/test_cors.py 模式）

    返回 FastAPI 实例——并发用例（D2）需配合 httpx.ASGITransport。
    """
    from fastapi import FastAPI

    from api.users import router as users_router

    app = FastAPI()
    app.include_router(users_router)
    if db is not None:
        app.state.database_manager = db
    if user_service is not None:
        app.state.user_service = user_service
    return app


def _make_app(*, db=None, user_service=None):
    from fastapi.testclient import TestClient

    return TestClient(_build_app(db=db, user_service=user_service))


def test_post_users_success_forwards_all_fields():
    svc = _FakeUserService()
    client = _make_app(db=_FakeDB(), user_service=svc)
    resp = client.post("/users", json={
        "user_id": "u1",
        "channel": "partner_a",
        "user_name": "张三",
        "extra": {"plan": "pro"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "u1"
    assert body["channel"] == "partner_a"
    assert body["display_name"] == "张三"   # 入参名 user_name，响应字段名保持后端语义
    assert body["extra"] == {"plan": "pro"}     # D1：响应回显请求值
    assert svc.calls[0]["channel"] == "partner_a"
    assert svc.calls[0]["display_name"] == "张三", "入参 user_name 应映射为服务层 display_name"
    assert "extra" not in svc.calls[0], "D1：extra 不透传服务层（仅日志）"


def test_post_users_extra_is_logged_not_forwarded():
    """D1 决策：extra 仅日志记录——有 INFO 日志、响应回显、不进服务层"""
    import io

    from loguru import logger

    sink = io.StringIO()
    handler_id = logger.add(sink, level="INFO")
    try:
        svc = _FakeUserService()
        client = _make_app(db=_FakeDB(), user_service=svc)
        resp = client.post("/users", json={
            "user_id": "u1", "extra": {"plan": "pro"},
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["extra"] == {"plan": "pro"}
        assert "extra" not in svc.calls[0]
        assert "plan" in sink.getvalue(), "extra 应写入 INFO 日志"
    finally:
        logger.remove(handler_id)


def test_post_users_defaults_channel_web():
    svc = _FakeUserService()
    client = _make_app(db=_FakeDB(), user_service=svc)
    resp = client.post("/users", json={"user_id": "u1"})
    assert resp.status_code == 200, resp.text
    assert svc.calls[0]["channel"] == "web"
    assert resp.json()["channel"] == "web"


def test_post_users_invalid_channel_422():
    client = _make_app(db=_FakeDB(), user_service=_FakeUserService())
    resp = client.post("/users", json={"user_id": "u1", "channel": "bad channel!"})
    assert resp.status_code == 422


def test_post_users_missing_user_id_422():
    client = _make_app(db=_FakeDB(), user_service=_FakeUserService())
    resp = client.post("/users", json={"channel": "web"})
    assert resp.status_code == 422


def test_post_users_bad_user_id_400():
    client = _make_app(db=_FakeDB(), user_service=_FakeUserService())
    resp = client.post("/users", json={"user_id": "bad id!"})
    assert resp.status_code == 400


def test_post_users_extra_must_be_object_422():
    client = _make_app(db=_FakeDB(), user_service=_FakeUserService())
    resp = client.post("/users", json={"user_id": "u1", "extra": "not-a-dict"})
    assert resp.status_code == 422


def test_post_users_503_without_db():
    client = _make_app(user_service=_FakeUserService())  # db 未装配
    resp = client.post("/users", json={"user_id": "u1"})
    assert resp.status_code == 503


def test_post_users_503_without_user_service():
    client = _make_app(db=_FakeDB())  # service 未装配
    resp = client.post("/users", json={"user_id": "u1"})
    assert resp.status_code == 503


def test_post_users_concurrent_multi_user():
    """D2 决策：调用方多用户同时请求 —— N 个不同 user_id 并发上报

    全部 200 且互不串扰（每个 user_id 恰好一次、channel 与请求一致）。
    并发语义：upsert 为单语句原子（PG ON CONFLICT / MySQL ODKU）；
    不同 user_id 不同行无竞争，同 user_id 并发 = 后写覆盖（已知语义）；
    连接池 pool_size=10 支撑；本端点限流仍属缺口 4 非目标。
    """
    import asyncio

    import httpx

    svc = _FakeUserService()
    app = _build_app(db=_FakeDB(), user_service=svc)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            return await asyncio.gather(*[
                client.post(
                    "/users",
                    json={"user_id": f"u{i}", "channel": f"ch_{i}"},
                )
                for i in range(10)
            ])

    responses = asyncio.run(_run())
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses if r.status_code != 200
    ]
    assert len(svc.calls) == 10
    by_user = {c["user_id"]: c for c in svc.calls}
    assert set(by_user) == {f"u{i}" for i in range(10)}, "user_id 串扰"
    for i in range(10):
        assert by_user[f"u{i}"]["channel"] == f"ch_{i}", f"u{i} channel 串扰"
