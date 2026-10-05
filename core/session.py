# -*- coding: utf-8 -*-

"""会话管理器 — 用户区分 + 会话隔离 + KV 持久化

设计：
- 每个 (user_id, session_id) 维护一个独立的 Agent 实例
- Agent 实例持有独立的 AgentState（对话上下文、摘要等）
- AgentState 通过 KV 持久化，服务器重启后可恢复会话
- 会话有 TTL，超时后自动清理（内存 + KV）

使用方式：
    manager = SessionManager(config)
    await manager.initialize()  # 初始化 KV 存储
    agent = await manager.get_or_create("user_001", "session_abc")
    reply = await agent.reply(UserMsg("user", "你好"))
    await manager.save("user_001", "session_abc")  # 持久化状态
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from agentscope.agent import Agent
from agentscope.state import AgentState

from agentscope.message import AssistantMsg, Msg, UserMsg

from core.agent import AgentFactory
from core.config.schemas import AppConfig
from core.kv import KVStore, create_kv_store

from loguru import logger


@dataclass
class SessionEntry:
    """单个会话条目"""
    user_id: str
    session_id: str
    agent: Agent
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        """更新最后活跃时间"""
        self.last_active = time.time()


class SessionManager:
    """会话管理器 — 管理多用户多会话的 Agent 实例，支持 KV 持久化

    Args:
        config: 应用配置
        session_ttl: 会话超时时间（秒），默认 30 分钟
        max_sessions: 最大同时活跃会话数，默认 100
        storage: 可选的 PostgresStorage，用于 fork_session 落库（默认 None）
    """

    def __init__(
        self,
        config: AppConfig,
        session_ttl: int = 1800,
        max_sessions: int = 100,
        storage=None,
        db=None,
        user_service=None,
    ) -> None:
        self._config = config
        self._session_ttl = session_ttl
        self._max_sessions = max_sessions
        self._db = db  # DatabaseManager，用于 PG 回填
        self._user_service = user_service  # UserService，用于角色解析与懒注册
        # key: (user_id, session_id) → SessionEntry
        self._sessions: dict[tuple[str, str], SessionEntry] = {}
        self._lock = asyncio.Lock()

        # KV 配置（backend: redis / jsonl；key 前缀与 TTL 仍取自 redis 配置）
        redis_cfg = config.redis
        self._redis_url = redis_cfg.url
        self._redis_prefix = redis_cfg.key_prefix
        self._kv: KVStore | None = None

        # 存储层（可选，用于 fork_session 落库）
        self._storage = storage

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化 KV 存储（backend=redis 连接并 ping；backend=jsonl 加载本地文件）"""
        kv_cfg = self._config.kv
        self._kv = create_kv_store(kv_cfg, redis_url=self._redis_url)
        await self._kv.ping()
        logger.info("SessionManager: KV 存储就绪 (backend={})", kv_cfg.backend)

    async def shutdown(self) -> None:
        """关闭 KV 存储。"""
        if self._kv:
            await self._kv.aclose()
            self._kv = None
            logger.info("SessionManager: KV 存储已关闭")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def get_or_create(
        self,
        user_id: str,
        session_id: str,
    ) -> Agent:
        """获取或创建会话对应的 Agent 实例

        优先从内存缓存获取；若内存未命中，尝试从 KV 恢复 AgentState；
        若 KV 也未命中，创建全新 Agent。

        Args:
            user_id: 用户标识
            session_id: 会话标识

        Returns:
            该会话的独立 Agent 实例
        """
        key = (user_id, session_id)

        async with self._lock:
            entry = self._sessions.get(key)
            if entry is not None:
                entry.touch()
                return entry.agent

            # 超过上限时清理过期会话
            if len(self._sessions) >= self._max_sessions:
                await self._evict_expired()

            # 仍然超限则拒绝
            if len(self._sessions) >= self._max_sessions:
                raise RuntimeError(
                    f"会话数已达上限 ({self._max_sessions})，请稍后重试"
                )

            # 尝试从 KV 恢复 AgentState
            saved_state = await self._load_state(user_id, session_id)

            # KV 未命中时，尝试从 PG 回填历史消息
            if saved_state is None:
                saved_state = await self._backfill_from_pg(
                    user_id, session_id,
                    limit=self._config.context.backfill_message_limit,
                )

            # 解析用户角色（root / normal），并懒注册用户记录
            role = "normal"
            if self._user_service is not None:
                try:
                    role = await self._user_service.get_role(user_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("SessionManager: 角色解析失败（降级 normal）: {}", e)
                try:
                    await self._user_service.get_or_create_user(user_id)
                except Exception:  # noqa: BLE001
                    logger.debug("SessionManager: 用户懒注册失败（已忽略）")

            # 创建 Agent 实例（恢复状态 + user_id 沙箱隔离 + role 权限域）
            agent = AgentFactory.create(
                self._config, state=saved_state, user_id=user_id, role=role,
            )
            entry = SessionEntry(
                user_id=user_id,
                session_id=session_id,
                agent=agent,
            )
            self._sessions[key] = entry

            # 从 PG 恢复的状态需同步写入 KV（否则 fork 等依赖 KV 的操作会失败）
            if saved_state is not None:
                await self._save_state(user_id, session_id, agent)

            logger.info(
                "新建会话: user={} session={} (恢复={}, 当前 {} 个会话)",
                user_id, session_id,
                "KV" if saved_state else "无",
                len(self._sessions),
            )
            return agent

    async def refresh_state(self, user_id: str, session_id: str) -> None:
        """强制从 KV 重新加载 AgentState 覆盖内存中的副本

        用于多实例场景：其他实例可能已写入更新的状态到 KV，
        本方法拉取最新 state 替换当前内存中 agent 的 state。

        如果当前内存中没有该会话，则静默返回（下次 get_or_create 会
        自动从 KV 恢复）；如果 KV 中也无 state，则不修改内存。
        """
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return  # 内存中没有，下次 get_or_create 会自动从 KV 恢复
            # 强制从 KV 加载最新状态
            new_state = await self._load_state(user_id, session_id)
            if new_state is not None:
                entry.agent.state = new_state
                # 同步 PermissionEngine 的 context 引用：Agent.__init__ 中
                # self._engine = PermissionEngine(self.state.permission_context)
                # 持有旧 permission_context 引用，替换 state 后必须同步更新，
                # 否则权限检查会用到过期的 context
                engine = getattr(entry.agent, "_engine", None)
                if engine is not None:
                    engine.context = new_state.permission_context
                logger.debug(
                    "refresh_state 成功: user={} session={}",
                    user_id, session_id,
                )
            entry.touch()

    async def fork_session(
        self,
        user_id: str,
        parent_session_id: str,
        new_title: str | None = None,
        branch_after_message_id: int | None = None,
    ) -> str:
        """基于父会话创建分支，返回新会话 ID（新式「带历史前缀的新会话」）。

        设计（对齐 新式 ``fork_session_before_user_index``，已逐行对照源码）：

        1. 复制源会话 PG ``conversations`` 前缀（支持 ``branch_after_message_id``
           中途截断）到子会话 —— 修复原实现「fork 后空白」的根因。
        2. 从**同一份前缀**重建子会话 AgentState（KV == PG，消除 KV↔PG 分裂）。
           fork 本身**不做任何压缩**，仅克隆前缀 + 剔除内部行。
        3. 动态窗口（用户建议 b）：保留最近 ``fork_window_turns`` 轮；
           系统提示词由 AgentFactory 在 ``get_or_create`` 时注入，天然保留。
        4. 易失状态天然不携带（前缀是已完成的历史，不含 in-progress 回复）。
        5. 落库 sessions 行（best-effort，含父会话补写回退）。
        6. 任一环节失败执行原子回滚，避免孤儿数据。

        Args:
            user_id: 用户标识
            parent_session_id: 父会话 ID
            new_title: 子会话标题，None 则沿用父标题并追加 " (分支)"
            branch_after_message_id: 中途 fork 截断点（包含该消息）；
                None 表示复制全部历史

        Returns:
            新生成的子会话 ID

        Raises:
            RuntimeError: KV 存储未初始化
        """
        import asyncio
        import uuid

        if self._kv is None:
            raise RuntimeError(
                "KV 存储未初始化，无法 fork 会话；请先调用 initialize()"
            )

        child_session_id = str(uuid.uuid4())

        # 1) 复制 PG 历史前缀（支持中途 fork：id <= before_id 截断）
        copied = 0
        if self._db is not None and getattr(self._db, "is_initialized", False):
            try:
                copied = await self._db.copy_conversations_prefix(
                    child_session_id, parent_session_id, user_id,
                    before_id=branch_after_message_id,
                )
                logger.info(
                    "fork PG 复制完成: user={} parent={} child={} copied={} branch={}",
                    user_id, parent_session_id, child_session_id,
                    copied, branch_after_message_id,
                )
                if copied == 0:
                    logger.warning(
                        "fork 复制 0 行！可能原因：父会话无 active conversations "
                        "或 user_id/session_id 不匹配。检查 PG conversations 表。"
                    )
            except Exception:
                logger.exception(
                    "fork 复制 PG 历史失败: user={} parent={}",
                    user_id, parent_session_id,
                )

        # 2) 从同一份前缀重建 child AgentState（保证 KV == PG，消除分裂）
        child_state: AgentState | None = None
        if copied > 0:
            child_state = await self._backfill_from_pg(
                user_id, child_session_id, limit=100000,
            )
        if child_state is None:
            child_state = AgentState(session_id=child_session_id)

        # 3) 动态窗口：系统提示词由 AgentFactory 注入；此处仅保留最近 N 轮
        window = getattr(self._config.context, "fork_window_turns", 0)
        if window and window > 0:
            child_state = self._apply_fork_window(child_state, window)

        # 4) 落 KV（从已完成的历史重建，天然不含 in-progress 易失状态）
        state_json = child_state.model_dump_json()
        try:
            await self._kv.set(
                self._redis_key(user_id, child_session_id),
                state_json, ex=self._session_ttl,
            )
        except Exception:
            await self._safe_rollback_child(user_id, child_session_id, copied)
            raise

        # 5) 标题：优先读 PG sessions.title 列（get_session_title 已做 COALESCE），
        #    降级到 first_message（conversations 表首条用户消息），再降级到 KV。
        meta_p = await self._load_session_meta(user_id, parent_session_id) or {}
        source_title: str | None = None
        if self._db is not None and getattr(self._db, "is_initialized", False):
            try:
                source_title = await self._db.get_session_title(
                    user_id, parent_session_id,
                )
            except Exception:
                logger.debug("读取源会话 PG 标题失败，降级到 first_message")
            # 降级：读 conversations 表首条用户消息（与侧栏 get_user_sessions 逻辑一致）
            if not source_title:
                try:
                    first_msg = await self._db.fetchval(
                        'SELECT LEFT("content", 30) FROM conversations '
                        'WHERE "user_id" = $1 AND "session_id" = $2 '
                        'AND "role" = \'user\' AND "status" = \'active\' '
                        'ORDER BY "id" ASC LIMIT 1',
                        user_id, parent_session_id,
                    )
                    if first_msg:
                        source_title = first_msg
                except Exception:
                    logger.debug("读取源会话 first_message 失败，降级到 KV")
        # 降级：KV 元数据标题
        if not source_title:
            source_title = meta_p.get("title", "")
        if not source_title:
            source_title = "新会话"

        if new_title:
            title = new_title
        else:
            title = f"{source_title} - fork"

        now = time.time()
        meta_c = {
            "session_id": child_session_id,
            "user_id": user_id,
            "title": title,
            "parent_session_id": parent_session_id,  # 血缘列保留但本方案不依赖/不渲染
            "forked_at": now,
            "fork_branch_message_id": branch_after_message_id,
            "created_at": meta_p.get("created_at", now),
            "last_active": now,
            "message_count": len(getattr(child_state, "context", []) or []),
        }
        await self._kv.set(
            self._redis_meta_key(user_id, child_session_id),
            json.dumps(meta_c, ensure_ascii=False),
            ex=self._session_ttl,
        )

        # 6) 落库 sessions 行（best-effort，含父会话补写回退）
        if self._storage is not None:
            try:
                await self._storage.fork_session(
                    parent_session_id, user_id,
                    new_name=title,
                    new_session_id=child_session_id,
                    branch_after_message_id=branch_after_message_id,
                )
            except asyncio.CancelledError:
                raise  # 不吞 CancelledError，保留协程取消语义
            except ValueError:
                # 父会话不在 PG 中（8090 流程只写 KV 不写 PG sessions 表），
                # 先 upsert 父会话再重试 fork
                try:
                    from core.storage_models import (
                        SessionConfig,
                        SessionSource,
                    )
                    parent_cfg = SessionConfig(
                        name=meta_p.get("title", "新会话"),
                    )
                    await self._storage.upsert_session(
                        user_id=user_id,
                        agent_id=self._config.agent.name,
                        config=parent_cfg,
                        state_json=state_json,
                        session_id=parent_session_id,
                        source=SessionSource.USER,
                        depth=0,
                    )
                    await self._storage.fork_session(
                        parent_session_id, user_id,
                        new_name=title,
                        new_session_id=child_session_id,
                        branch_after_message_id=branch_after_message_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "fork_session 落库失败（含父会话补写）: user={} parent={}",
                        user_id, parent_session_id,
                    )
            except Exception:
                logger.exception(
                    "fork_session 落库失败: user={} parent={}",
                    user_id, parent_session_id,
                )

        # 7) 写入 config->>'title'（侧栏读此字段，而非 config.name）
        if self._db is not None and getattr(self._db, "is_initialized", False):
            try:
                await self._db.upsert_session_title(
                    user_id, child_session_id, title,
                )
            except Exception:
                logger.warning(
                    "fork 写入 PG session title 失败: child={}", child_session_id,
                )

        logger.info(
            "fork 会话成功: user={} parent={} child={} copied={} branch={}",
            user_id, parent_session_id, child_session_id, copied,
            branch_after_message_id,
        )
        return child_session_id

    # ------------------------------------------------------------------
    # Fork 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _history_to_state(
        messages: list[dict], session_id: str
    ) -> AgentState | None:
        """将 PG 历史消息（``get_conversation_history`` 的结构）转为 AgentState。

        跳过内部行（``thinking`` / ``tool_call``），仅保留 user 文本与
        assistant 文本行，等价于 新式 fork 时的「隐藏/内部消息剔除」。
        fork 与 KV 回填共用此逻辑，保证 Agent 上下文与 UI 一致且已被压缩。
        """
        context = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            metadata = msg.get("metadata") or {}
            mtype = metadata.get("type")
            if role == "user":
                context.append(UserMsg(name="user", content=content))
            elif role == "assistant":
                if mtype in ("thinking", "tool_call"):
                    continue  # 丢弃内部行（压缩）
                context.append(AssistantMsg(name="assistant", content=content))
            # 其他角色（system 等）跳过
        if not context:
            return None
        state = AgentState(session_id=session_id)
        state.context = context
        return state

    @staticmethod
    def _apply_fork_window(
        state: AgentState, window_turns: int
    ) -> AgentState:
        """fork 后的动态窗口（用户建议 b）：保留最近 ``window_turns`` 轮。

        系统提示词由 AgentFactory 在 ``get_or_create`` 注入，不在 context 中，
        故此处仅对 user/assistant 轮次做尾部截断。按 user 消息切分为轮次，
        保留最后 N 轮。
        """
        if window_turns <= 0:
            return state
        context = list(getattr(state, "context", []) or [])
        if not context:
            return state
        # 按 user 消息切分为轮次（每轮 = 一个 user + 其后的 assistant）
        turns: list[list] = []
        current: list = []
        for msg in context:
            role = getattr(msg, "role", "")
            if role == "user" and current:
                turns.append(current)
                current = []
            current.append(msg)
        if current:
            turns.append(current)
        if len(turns) <= window_turns:
            return state
        kept = [m for t in turns[-window_turns:] for m in t]
        state.context = kept
        return state

    async def _safe_rollback_child(
        self,
        user_id: str,
        child_session_id: str,
        copied: int,
    ) -> None:
        """fork 失败回滚：删除已复制的 PG 行与 KV 键，避免孤儿数据。"""
        if copied > 0 and self._db is not None:
            try:
                await self._db.delete_conversations(user_id, child_session_id)
            except Exception:
                logger.warning("fork 回滚 PG 失败: {}", child_session_id)
        if self._kv is not None:
            try:
                await self._kv.delete(
                    self._redis_key(user_id, child_session_id),
                    self._redis_meta_key(user_id, child_session_id),
                )
            except Exception:
                logger.warning("fork 回滚 KV 失败: {}", child_session_id)

    async def save(self, user_id: str, session_id: str) -> None:
        """将指定会话的 AgentState 保存到 KV。

        应在每次 reply/reply_stream 完成后调用。
        同时保存会话元数据（标题、消息数等）。
        正常完成时清除 reply_status / last_reply，避免重连时推送过期数据。
        """
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.get(key)

        if entry:
            await self._save_state(user_id, session_id, entry.agent)
            # 保存元数据（从 AgentState.context 提取）
            context = getattr(entry.agent.state, "context", [])
            msg_count = len(context)
            title = ""
            for msg in context:
                if getattr(msg, "role", "") == "user":
                    content = getattr(msg, "content", [])
                    if isinstance(content, list):
                        for block in content:
                            if getattr(block, "type", "") == "text":
                                title = block.text[:30]
                                break
                    elif isinstance(content, str):
                        title = content[:30]
                    break
            await self.save_session_meta(user_id, session_id, title, msg_count)

            # 正常 save（非断连场景）清除旧的 pending_reply 标记
            if self._kv:
                try:
                    meta_key = self._redis_meta_key(user_id, session_id)
                    existing = await self._load_session_meta(user_id, session_id)
                    if existing and existing.get("reply_status"):
                        existing.pop("reply_status", None)
                        existing.pop("last_reply", None)
                        await self._kv.set(
                            meta_key,
                            json.dumps(existing, ensure_ascii=False),
                            ex=self._session_ttl,
                        )
                except Exception:
                    pass

    async def remove(self, user_id: str, session_id: str) -> bool:
        """移除指定会话（同时清除 KV 中的状态）"""
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.pop(key, None)

        if entry:
            # 先保存最终状态到 KV（可选：也可直接删除）
            await self._save_state(user_id, session_id, entry.agent)
            logger.info("移除会话: user={} session={}", user_id, session_id)
            return True
        return False

    async def list_sessions(self, user_id: str | None = None) -> list[dict]:
        """列出活跃会话（仅内存中）

        Args:
            user_id: 可选，按用户过滤

        Returns:
            会话信息列表
        """
        async with self._lock:
            result = []
            for (uid, sid), entry in self._sessions.items():
                if user_id and uid != user_id:
                    continue
                result.append({
                    "user_id": uid,
                    "session_id": sid,
                    "created_at": entry.created_at,
                    "last_active": entry.last_active,
                })
            return result

    async def list_user_sessions(self, user_id: str) -> list[dict]:
        """列出指定用户的所有会话（按 KV 后端扫描）

        按 KV 后端扫描该用户的所有 :meta key（redis SCAN / jsonl 内存 glob），
        返回会话元数据列表，按 last_active 倒序排列。

        Args:
            user_id: 用户标识

        Returns:
            会话元数据列表
        """
        if not self._kv:
            return []

        meta_prefix = f"{self._redis_prefix}{user_id}:*:meta"
        sessions = []

        try:
            keys = await self._kv.scan_keys(meta_prefix)
            for key in keys:
                try:
                    meta_json = await self._kv.get(key)
                    if meta_json:
                        sessions.append(json.loads(meta_json))
                except Exception:
                    logger.warning("解析会话元数据失败: {}", key)

            # 按 last_active 倒序
            sessions.sort(key=lambda s: s.get("last_active", 0), reverse=True)
        except Exception:
            logger.exception("扫描用户会话失败: user={}", user_id)

        return sessions

    async def list_all_sessions(self) -> list[dict]:
        """列出所有用户的全部会话（root 管理视图）

        扫描 KV 后端所有 ``:meta`` key（redis SCAN / jsonl glob），
        从 key 反解 user_id，按 last_active 倒序排列。

        Returns:
            会话元数据列表（每条含 user_id）
        """
        if not self._kv:
            return []
        meta_prefix = f"{self._redis_prefix}*:*:meta"
        sessions: list[dict] = []
        try:
            keys = await self._kv.scan_keys(meta_prefix)
            for key in keys:
                try:
                    meta_json = await self._kv.get(key)
                    if not meta_json:
                        continue
                    meta = json.loads(meta_json)
                    # key 形如 {prefix}{user_id}:{session_id}:meta
                    rest = key[len(self._redis_prefix):]
                    parts = rest.split(":")
                    if len(parts) >= 3:
                        meta["user_id"] = parts[0]
                        meta["session_id"] = parts[1]
                    sessions.append(meta)
                except Exception:
                    logger.warning("解析会话元数据失败: {}", key)
            sessions.sort(key=lambda s: s.get("last_active", 0), reverse=True)
        except Exception:
            logger.exception("扫描全部会话失败")
        return sessions

    async def save_session_meta(
        self,
        user_id: str,
        session_id: str,
        title: str = "",
        message_count: int = 0,
    ) -> None:
        """保存会话元数据到 KV

        元数据与 AgentState 使用相同的 TTL，独立 key 存储。
        """
        if not self._kv:
            return

        key = self._redis_meta_key(user_id, session_id)
        now = time.time()

        # 尝试更新已有元数据
        existing = await self._load_session_meta(user_id, session_id)
        if existing:
            meta = {
                **existing,
                "last_active": now,
                "message_count": message_count,
            }
            # 只在 title 为空时更新
            if title and not existing.get("title"):
                meta["title"] = title
        else:
            meta = {
                "session_id": session_id,
                "user_id": user_id,
                "title": title or "新会话",
                "created_at": now,
                "last_active": now,
                "message_count": message_count,
            }

        try:
            await self._kv.set(
                key, json.dumps(meta, ensure_ascii=False), ex=self._session_ttl
            )
        except Exception:
            logger.exception("保存会话元数据失败: user={} session={}", user_id, session_id)

    async def save_session_reply(
        self,
        user_id: str,
        session_id: str,
        last_reply: str,
        reply_status: str = "completed",
    ) -> None:
        """将断连期间生成的回复写入会话元数据（供前端重连时拉取）。

        Args:
            last_reply: 最后一条 assistant 回复文本
            reply_status: 回复状态（completed / partial / timeout / error）
        """
        if not self._kv:
            return

        existing = await self._load_session_meta(user_id, session_id) or {}
        meta = {
            **existing,
            "session_id": session_id,
            "user_id": user_id,
            "last_reply": last_reply[-2000:],  # 截断防爆（保留最后 2000 字符）
            "reply_status": reply_status,
            "last_active": time.time(),
        }

        key = self._redis_meta_key(user_id, session_id)
        try:
            await self._kv.set(
                key, json.dumps(meta, ensure_ascii=False), ex=self._session_ttl,
            )
        except Exception:
            logger.exception("保存回复元数据失败: user={} session={}", user_id, session_id)

    async def load_session_meta(self, user_id: str, session_id: str) -> dict | None:
        """公开接口：从 KV 加载会话元数据。"""
        return await self._load_session_meta(user_id, session_id)

    async def _load_session_meta(self, user_id: str, session_id: str) -> dict | None:
        """从 KV 加载会话元数据"""
        if not self._kv:
            return None
        try:
            key = self._redis_meta_key(user_id, session_id)
            meta_json = await self._kv.get(key)
            if meta_json:
                return json.loads(meta_json)
        except Exception:
            logger.exception("加载会话元数据失败: user={} session={}", user_id, session_id)
        return None

    async def get_session_messages(
        self, user_id: str, session_id: str
    ) -> list[dict] | None:
        """获取会话的消息历史

        从 KV 加载 AgentState，提取消息列表返回。
        如果会话不存在返回 None。

        Returns:
            消息列表 [{"role": "user"/"assistant", "content": "..."}, ...]
            或 None（会话不存在）
        """
        state = await self._load_state(user_id, session_id)
        if state is None:
            return None

        messages = []
        try:
            # AgentState.context 是 list[Msg]，每条有 role/name/content(list[ContentBlock])
            for msg in state.context:
                role = getattr(msg, "role", "unknown")

                # 确定前端显示角色
                if role == "user":
                    display_role = "user"
                elif role == "assistant":
                    display_role = "agent"
                else:
                    continue  # 跳过 system 等其他角色

                # 提取文本内容 — content 是 list[ContentBlock]
                content = getattr(msg, "content", [])
                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        block_type = getattr(block, "type", "")
                        if block_type == "text":
                            text_parts.append(block.text)
                        elif block_type == "thinking":
                            pass  # 跳过思考过程
                        elif block_type == "tool_call":
                            pass  # 跳过工具调用
                        elif block_type == "tool_result":
                            pass  # 跳过工具结果
                elif isinstance(content, str):
                    text_parts.append(content)

                text = "\n".join(text_parts).strip()
                if text:
                    messages.append({
                        "role": display_role,
                        "content": text,
                    })
        except Exception:
            logger.exception("提取消息历史失败: user={} session={}", user_id, session_id)

        return messages

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """彻底删除会话（内存 + KV AgentState + KV 元数据）

        Returns:
            是否成功删除
        """
        key = (user_id, session_id)
        async with self._lock:
            self._sessions.pop(key, None)

        deleted = False
        if self._kv:
            try:
                state_key = self._redis_key(user_id, session_id)
                meta_key = self._redis_meta_key(user_id, session_id)
                result = await self._kv.delete(state_key, meta_key)
                deleted = result > 0
            except Exception:
                logger.exception("删除会话 KV 数据失败: user={} session={}", user_id, session_id)

        logger.info("删除会话: user={} session={} deleted={}", user_id, session_id, deleted)
        return deleted

    async def cleanup(self) -> int:
        """手动触发过期会话清理

        Returns:
            清理的会话数量
        """
        async with self._lock:
            return await self._evict_expired()

    @property
    def active_count(self) -> int:
        """当前活跃会话数"""
        return len(self._sessions)

    # ------------------------------------------------------------------
    # KV 持久化
    # ------------------------------------------------------------------

    def _redis_key(self, user_id: str, session_id: str) -> str:
        """生成 Redis key: {prefix}{user_id}:{session_id}"""
        return f"{self._redis_prefix}{user_id}:{session_id}"

    def _redis_meta_key(self, user_id: str, session_id: str) -> str:
        """生成 Redis 元数据 key: {prefix}{user_id}:{session_id}:meta"""
        return f"{self._redis_prefix}{user_id}:{session_id}:meta"

    async def _save_state(
        self,
        user_id: str,
        session_id: str,
        agent: Agent,
    ) -> None:
        """序列化 AgentState 并写入 KV。"""
        if not self._kv:
            return
        try:
            key = self._redis_key(user_id, session_id)
            state_json = agent.state.model_dump_json()
            await self._kv.set(key, state_json, ex=self._session_ttl)
            logger.debug("状态已保存: {} ({} bytes)", key, len(state_json))
        except Exception:
            logger.exception("保存状态失败: user={} session={}", user_id, session_id)

    async def _load_state(
        self,
        user_id: str,
        session_id: str,
    ) -> AgentState | None:
        """从 KV 反序列化 AgentState，未命中返回 None。"""
        if not self._kv:
            return None
        try:
            key = self._redis_key(user_id, session_id)
            state_json = await self._kv.get(key)
            if state_json:
                state = AgentState.model_validate_json(state_json)
                logger.debug("从 KV 恢复状态: {}", key)
                return state
        except Exception:
            logger.exception("加载状态失败: user={} session={}", user_id, session_id)
        return None

    async def _backfill_from_pg(
        self,
        user_id: str,
        session_id: str,
        limit: int = 20,
    ) -> AgentState | None:
        """从 PG 加载历史消息，构造 AgentState 用于恢复上下文

        当 KV 未命中时调用，将 PG 中最近 N 条消息转为 Msg 列表
        注入 AgentState.context，使 Agent 能继续之前的对话。

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            limit: 加载消息条数上限（默认 20，可通过 context.backfill_message_limit 配置）

        Returns:
            AgentState 或 None（PG 无数据时）
        """
        if not self._db or not self._db.is_initialized:
            return None
        try:
            result = await self._db.get_conversation_history(
                user_id, session_id, limit=limit,
            )
            messages = result.get("messages", [])
            if not messages:
                return None

            # 复用 fork 的历史→状态逻辑：跳过 thinking/tool_call 内部行，
            # 仅保留 user/assistant 文本，等价于轮内压缩的「丢弃内部草稿」。
            state = self._history_to_state(messages, session_id)
            if state is None:
                return None
            logger.info(
                "PG 回填成功: user={} session={}, 加载 {} 条消息",
                user_id, session_id, len(getattr(state, "context", []) or []),
            )
            return state
        except Exception:
            logger.exception("PG 回填失败: user={} session={}", user_id, session_id)
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _evict_expired(self) -> int:
        """清理过期会话（需在持有 _lock 时调用）"""
        now = time.time()
        expired = [
            key for key, entry in self._sessions.items()
            if now - entry.last_active > self._session_ttl
        ]
        for key in expired:
            uid, sid = key
            # 过期前保存状态到 KV（TTL 会续期）
            entry = self._sessions.pop(key)
            await self._save_state(uid, sid, entry.agent)
            logger.info("清理过期会话: user={} session={}", uid, sid)
        return len(expired)
