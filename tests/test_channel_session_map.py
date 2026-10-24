# -*- coding: utf-8 -*-
"""ChannelSessionMap 单测 — get_or_init 固化 / rotate 轮换 / resolve_or_rotate 空闲轮换 / key 不污染会话扫描"""

from __future__ import annotations

import json

import pytest

from core.channels.session_map import ChannelSessionMap, derive_session_id


class FakeKV:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        self.ttls[key] = ex

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.data.pop(k, None) is not None)


PREFIX = "dmx_agent_redis:session:"


def _map() -> tuple[ChannelSessionMap, FakeKV]:
    kv = FakeKV()
    return ChannelSessionMap(kv, PREFIX), kv


class TestDeriveSessionId:
    def test_stable_and_prefixed(self):
        a = derive_session_id("u1", "wecom", "chat1")
        b = derive_session_id("u1", "wecom", "chat1")
        assert a == b and a.startswith("ch-") and len(a) <= 64

    def test_chat_isolated(self):
        assert derive_session_id("u1", "wecom", "chat1") != derive_session_id("u1", "wecom", "chat2")
        assert derive_session_id("u1", "wecom", "c") != derive_session_id("u2", "wecom", "c")


class TestGetOrInit:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_miss_binds_default(self):
        m, kv = _map()
        default = derive_session_id("u1", "wecom", "chat1")
        got = await m.get_or_init("u1", "wecom", "chat1", default=default)
        assert got == default
        assert await m.resolve("u1", "wecom", "chat1") == default
        assert kv.ttls[list(kv.data)[0]] is None  # 永不过期（D2）

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hit_returns_bound(self):
        m, _ = _map()
        await m.bind("u1", "wecom", "chat1", "ch-new")
        assert await m.get_or_init("u1", "wecom", "chat1", default="ch-old") == "ch-new"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rotate(self):
        m, _ = _map()
        await m.get_or_init("u1", "wecom", "chat1", default="ch-old")
        await m.bind("u1", "wecom", "chat1", "ch-gen2")
        assert await m.resolve("u1", "wecom", "chat1") == "ch-gen2"


class TestKeySafety:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_key_not_scanned_by_meta_globs(self):
        """指针 key 不能被 list_user_sessions / list_all_sessions 的 glob 扫出。"""
        import fnmatch

        m, kv = _map()
        await m.bind("u1", "wecom", "chat1", "ch-x")
        key = list(kv.data)[0]
        assert not key.endswith(":meta")
        assert not fnmatch.fnmatch(key, f"{PREFIX}u1:*:meta")
        assert not fnmatch.fnmatch(key, f"{PREFIX}*:*:meta")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_resolve_missing_returns_none(self):
        m, _ = _map()
        assert await m.resolve("u1", "wecom", "nope") is None


class TestResolveOrRotate:
    """空闲超时自动轮换 — resolve_or_rotate"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_first_contact_binds_default(self):
        m, kv = _map()
        default = derive_session_id("u1", "wecom", "chat1")
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default=default, idle_hours=24.0,
        )
        assert sid == default and rotated is None
        assert await m.resolve("u1", "wecom", "chat1") == default

    @pytest.mark.asyncio(loop_scope="session")
    async def test_fresh_touches_last_active(self):
        m, kv = _map()
        t0 = 1_700_000_000.0
        await m.bind("u1", "wecom", "chat1", "ch-a", now=t0)
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0, now=t0 + 3600,
        )
        assert sid == "ch-a" and rotated is None
        raw = kv.data[list(kv.data)[0]]
        assert json.loads(raw)["last_active"] == t0 + 3600  # touch 生效

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rotates_when_idle_exceeded(self):
        m, kv = _map()
        t0 = 1_700_000_000.0
        await m.bind("u1", "wecom", "chat1", "ch-old", now=t0)
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d",
            idle_hours=24.0, now=t0 + 25 * 3600,
        )
        assert rotated == "ch-old"
        assert sid != "ch-old" and sid.startswith("ch-") and len(sid) <= 64
        assert await m.resolve("u1", "wecom", "chat1") == sid  # 指针已重绑

    @pytest.mark.asyncio(loop_scope="session")
    async def test_not_rotated_at_exact_boundary(self):
        """恰好 N 小时不算过期（> 才过期）"""
        m, _ = _map()
        t0 = 1_700_000_000.0
        await m.bind("u1", "wecom", "chat1", "ch-a", now=t0)
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0, now=t0 + 24 * 3600,
        )
        assert sid == "ch-a" and rotated is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_zero_disables_rotation(self):
        m, _ = _map()
        t0 = 1_700_000_000.0
        await m.bind("u1", "wecom", "chat1", "ch-a", now=t0)
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=0, now=t0 + 10**8,
        )
        assert sid == "ch-a" and rotated is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_pointer_backfills_timestamp(self):
        """旧格式指针（无 last_active）：补写时间戳，不轮换"""
        m, kv = _map()
        key = m._key("u1", "wecom", "chat1")
        await kv.set(key, json.dumps({"session_id": "ch-legacy"}))
        t0 = 1_700_000_000.0
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0, now=t0,
        )
        assert sid == "ch-legacy" and rotated is None
        assert json.loads(kv.data[key])["last_active"] == t0

    @pytest.mark.asyncio(loop_scope="session")
    async def test_corrupt_last_active_keeps_session_id(self):
        """last_active 损坏（非数字）按旧格式处理：保留 session_id、补写时间戳、不轮换"""
        m, kv = _map()
        key = m._key("u1", "wecom", "chat1")
        await kv.set(key, json.dumps({"session_id": "ch-good", "last_active": "garbage"}))
        t0 = 1_700_000_000.0
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0, now=t0,
        )
        assert sid == "ch-good" and rotated is None  # 不得退回 default / 不得轮换
        assert json.loads(kv.data[key])["last_active"] == t0

    @pytest.mark.asyncio(loop_scope="session")
    async def test_negative_idle_hours_disables_rotation(self):
        """idle_hours < 0 同样禁用自动轮换（契约：<= 0 关闭）"""
        m, _ = _map()
        t0 = 1_700_000_000.0
        await m.bind("u1", "wecom", "chat1", "ch-a", now=t0)
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=-1, now=t0 + 10**8,
        )
        assert sid == "ch-a" and rotated is None


class TestResolveOrRotateStaleProbe:
    """指针缺失/无时钟时的过期判定 — stale_probe 契约

    背景：指针一丢（重启/驱逐/首次接入）旧逻辑无条件固化 default（derive），
    过期判定形同虚设 —— 旧会话永不轮换，且复活旧重上下文。
    """

    @staticmethod
    def _probe(result, calls=None):
        async def probe(session_id: str):
            if calls is not None:
                calls.append(session_id)
            return result

        return probe

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_pointer_stale_rotates_from_default(self):
        m, kv = _map()
        calls: list[str] = []
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0,
            now=1_700_000_000.0, stale_probe=self._probe(False, calls),
        )
        assert calls == ["ch-d"]  # 判定对象是候选（default）本身
        assert rotated == "ch-d"  # 轮换自旧会话，上层可清理 + 发提示
        assert sid != "ch-d" and sid.startswith("ch-")
        assert await m.resolve("u1", "wecom", "chat1") == sid

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_pointer_fresh_or_unknown_binds_default(self):
        for fresh in (True, None):
            m, _ = _map()
            sid, rotated = await m.resolve_or_rotate(
                "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0,
                now=1_700_000_000.0, stale_probe=self._probe(fresh),
            )
            assert sid == "ch-d" and rotated is None, fresh

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_pointer_stale_rotates(self):
        """旧格式指针（无时钟）同规则：过期 → 轮换，不再无条件续用"""
        m, kv = _map()
        key = m._key("u1", "wecom", "chat1")
        await kv.set(key, json.dumps({"session_id": "ch-legacy"}))
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0,
            now=1_700_000_000.0, stale_probe=self._probe(False),
        )
        assert rotated == "ch-legacy"
        assert sid != "ch-legacy" and sid.startswith("ch-")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pointer_with_clock_skips_probe(self):
        """指针自带 last_active：按指针时钟判定，不查库（探针零调用）"""
        m, _ = _map()
        await m.bind("u1", "wecom", "chat1", "ch-a", now=1_700_000_000.0)
        calls: list[str] = []
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0,
            now=1_700_000_000.0 + 3600, stale_probe=self._probe(False, calls),
        )
        assert calls == []
        assert sid == "ch-a" and rotated is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_probe_error_binds_default(self):
        async def probe(session_id: str):
            raise RuntimeError("db down")

        m, _ = _map()
        sid, rotated = await m.resolve_or_rotate(
            "u1", "wecom", "chat1", default="ch-d", idle_hours=24.0,
            now=1_700_000_000.0, stale_probe=probe,
        )
        assert sid == "ch-d" and rotated is None