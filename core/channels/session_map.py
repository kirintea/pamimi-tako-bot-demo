# -*- coding: utf-8 -*-
"""渠道会话指针 — (user_id, channel, chat_id) → 当前 session_id

解决企微单聊天窗口全部历史堆积在同一会话的问题：
`/new` 或空闲超时轮换指针后，后续消息进入全新会话；旧会话历史独立保留。

key 以 ":cur" 结尾、不以 ":meta" 结尾 —— 不会被
SessionManager.list_user_sessions / list_all_sessions 的 SCAN glob 扫出。
指针 TTL 永不过期（ex=None）：过期回退 derive 会让 /new 失效并复活旧重上下文。

指针值 JSON：{"session_id": ..., "last_active": <unix 秒>}。
last_active 为空闲自动轮换的时钟（每次入站 touch）；旧格式指针缺该字段时
补写时间戳、不触发轮换（升级平滑）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable

from loguru import logger

from core.kv.base import KVStore


def derive_session_id(user_id: str, channel: str, chat_id: str) -> str:
    """为 (user_id, channel, chat_id) 生成稳定且唯一的会话 id（≤ 64 字符）。

    使用 sha256 截断，避免 chat_id 过长或含特殊字符破坏 sessions.id 约束。
    （自 core/channels/manager.py 迁入，manager 保留委托以兼容既有调用。）
    """
    raw = f"{user_id}|{channel}|{chat_id}".encode("utf-8")
    return "ch-" + hashlib.sha256(raw).hexdigest()[:24]


def mint_session_id() -> str:
    """铸造全新渠道会话 id（空闲轮换用，格式与 /new 的 ch- 前缀一致）。"""
    return "ch-" + uuid.uuid4().hex[:24]


class ChannelSessionMap:
    """渠道会话指针存取（共享 KV：redis / jsonl）。"""

    def __init__(self, kv: KVStore | None, key_prefix: str) -> None:
        self._kv = kv
        self._prefix = key_prefix

    def _key(self, user_id: str, channel: str, chat_id: str) -> str:
        return f"{self._prefix}__chmap__:{user_id}:{channel}:{chat_id}:cur"

    async def _read(self, user_id: str, channel: str, chat_id: str) -> tuple[str | None, float | None]:
        """返回 (session_id, last_active)；无指针返回 (None, None)。"""
        if self._kv is None:
            return None, None
        try:
            raw = await self._kv.get(self._key(user_id, channel, chat_id))
            if raw:
                data = json.loads(raw)
                sid = data.get("session_id")
                la = data.get("last_active")
                try:
                    la_f = float(la) if la is not None else None
                except (TypeError, ValueError):
                    la_f = None  # 时间戳损坏按旧格式处理：补写、不轮换
                return sid, la_f
        except Exception:
            logger.exception(
                "ChannelSessionMap: 解析指针失败 user={} channel={} chat={}",
                user_id, channel, chat_id,
            )
        return None, None

    async def resolve(self, user_id: str, channel: str, chat_id: str) -> str | None:
        sid, _ = await self._read(user_id, channel, chat_id)
        return sid

    async def bind(
        self,
        user_id: str,
        channel: str,
        chat_id: str,
        session_id: str,
        *,
        now: float | None = None,
    ) -> None:
        if self._kv is None:
            raise RuntimeError("KV 存储未初始化，无法写入渠道会话指针")
        ts = time.time() if now is None else now
        await self._kv.set(
            self._key(user_id, channel, chat_id),
            json.dumps(
                {"session_id": session_id, "last_active": ts},
                ensure_ascii=False,
            ),
            ex=None,  # 永不过期（D2）
        )

    async def get_or_init(self, user_id: str, channel: str, chat_id: str, default: str) -> str:
        """返回当前绑定的 session_id；无绑定时固化 default 并返回。"""
        cur = await self.resolve(user_id, channel, chat_id)
        if cur:
            return cur
        await self.bind(user_id, channel, chat_id, default)
        return default

    async def resolve_or_rotate(
        self,
        user_id: str,
        channel: str,
        chat_id: str,
        *,
        default: str,
        idle_hours: float = 24.0,
        now: float | None = None,
        stale_probe: Callable[[str], Awaitable[bool | None]] | None = None,
    ) -> tuple[str, str | None]:
        """入站解析当前会话；空闲超过 idle_hours 自动轮换到全新会话。

        返回 (session_id, rotated_from)：发生轮换时 rotated_from 为旧会话 id，
        否则为 None。每次入站都会 touch last_active。

        - 指针不存在 → 固化 default（与 get_or_init 一致）
        - idle_hours <= 0 → 禁用自动轮换（仅 /new 手动）
        - 旧格式指针（无 last_active）→ 补写时间戳；仅当 stale_probe 判定过期才轮换
        - 恰好 idle_hours 边界不算过期（严格大于才轮换）
        - stale_probe(session_id)：仅在指针读不到 last_active（指针缺失 / 旧格式
          / 时钟损坏）时调用。True=窗口内仍有对话（延续）、False=已过期（轮换，
          rotated_from=候选会话）、None=无法判定（按现状延续）。没有它，指针一丢
          就会无条件复活 default（derive 旧会话），N 小时自动新开对这类会话
          永不生效，且把很早之前的历史带进「新对话」。
        """
        ts = time.time() if now is None else now
        cur, last_active = await self._read(user_id, channel, chat_id)

        if last_active is None:
            # 指针缺失或无时钟：候选 = 指针 session（旧格式）或 default（derive）
            candidate = cur if cur is not None else default
            fresh: bool | None = None
            if stale_probe is not None:
                try:
                    fresh = await stale_probe(candidate)
                except Exception:
                    logger.exception(
                        "ChannelSessionMap: 会话过期探测失败 candidate={}", candidate,
                    )
                    fresh = None
            if fresh is False and idle_hours > 0:
                new_sid = mint_session_id()
                await self.bind(user_id, channel, chat_id, new_sid, now=ts)
                logger.info(
                    "ChannelSessionMap: 候选会话已过期自动新开 user={} channel={} chat={} "
                    "old={} new={} idle_h={}",
                    user_id, channel, chat_id, candidate, new_sid, idle_hours,
                )
                return new_sid, candidate
            await self.bind(user_id, channel, chat_id, candidate, now=ts)  # 固化/补写时钟
            return candidate, None

        if idle_hours > 0 and (ts - last_active) > idle_hours * 3600:
            new_sid = mint_session_id()
            await self.bind(user_id, channel, chat_id, new_sid, now=ts)
            logger.info(
                "ChannelSessionMap: 会话空闲超时自动新开 user={} channel={} chat={} "
                "old={} new={} idle_h={}",
                user_id, channel, chat_id, cur, new_sid, idle_hours,
            )
            return new_sid, cur
        await self.bind(user_id, channel, chat_id, cur, now=ts)  # touch
        return cur, None
