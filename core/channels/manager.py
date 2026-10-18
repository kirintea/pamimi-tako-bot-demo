# -*- coding: utf-8 -*-

"""ChannelManager — 渠道注册 / 启停 / 出站分发 / 热切换 / 状态

架构变更（2026-10-07）：
- 渠道连接配置改为 JSON 文件驱动（core.channels.config_store），
  不再使用数据库存储 bot_id/secret 等连接信息。
- 支持热加载：修改 configs/channels.json 后自动感知并切换实例。
- 单实例锁：同一 channel_id 只允许一个进程连接（基于文件排他锁），
  多实例部署时只有一个容器连接同一 bot，避免消息重复。

原 DB 逻辑已注释保留（storage.list_enabled_channels 等），可按需恢复。
"""

from __future__ import annotations

import asyncio
import uuid

from agentscope.app.message_bus import MessageBusKeys
from loguru import logger

from core.channels.base import BaseChannel
from core.channels.config_store import (
    DEFAULT_SESSION_IDLE_HOURS,
    ChannelConfigStore,
    ChannelEntry,
)
from core.channels.events import InboundMessage
from core.channels.registry import get_channel_class
from core.channels.session_map import derive_session_id
# [已删除] core.storage_models 的 ChannelRecord / ChannelStatus 随 channels 表一并移除，
# 渠道记录改用 core.channels.config_store.ChannelEntry。


class ChannelManager:
    """渠道管理器 — JSON 文件配置驱动"""

    def __init__(
        self,
        *,
        config_store: ChannelConfigStore,
        bus,
        chat_service,
        object_storage,
        db=None,
        session_mgr=None,
        session_map=None,
        status_tracker=None,
        commands=None,
    ) -> None:
        self._config_store = config_store
        self._bus = bus
        self._chat_service = chat_service
        self._object_storage = object_storage
        self._db_ref = db
        self._session_mgr = session_mgr
        self._session_map = session_map
        self._status_tracker = status_tracker
        # 指令分发（默认惰性构造，测试可注入）
        if commands is None:
            from core.command.service import CommandService
            commands = CommandService()
        self._commands = commands

        # channel_id → 适配器实例 / 后台任务
        self._instances: dict[str, BaseChannel] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # 会话键（跨渠道 chat_id 隔离）
    # ------------------------------------------------------------------

    @staticmethod
    def derive_session_id(user_id: str, channel: str, chat_id: str) -> str:
        """委托 core.channels.session_map.derive_session_id（保留兼容入口）。"""
        return derive_session_id(user_id, channel, chat_id)

    # ------------------------------------------------------------------
    # 启动 / 关闭
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """启动期：从 config_store 加载所有 enabled 渠道并后台运行"""
        entries = self._config_store.get_enabled()
        logger.info("ChannelManager: 从配置文件加载 {} 条启用渠道", len(entries))
        for entry in entries:
            await self._start_channel(entry)

    async def stop_all(self) -> None:
        """关闭期：停止所有运行中的渠道并释放文件锁"""
        for channel_id in list(self._tasks.keys()):
            await self._stop_channel(channel_id)
        # 等待任务收尾
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        # 释放所有文件锁
        self._config_store._release_all_locks()

    async def _start_channel(self, entry: ChannelEntry) -> None:
        """实例化并后台启动单个渠道（含单实例文件锁检查）"""
        cls = get_channel_class(entry.type)
        if cls is None:
            logger.error("ChannelManager: 未知渠道类型 '{}' (id={})", entry.type, entry.id)
            return

        if entry.id in self._instances:
            logger.warning("ChannelManager: 渠道已在运行，跳过 (id={})", entry.id)
            return

        # 单实例锁：同一 channel_id 只允许一个进程连接
        if not self._config_store.try_acquire_lock(entry.id):
            logger.info(
                "ChannelManager: 渠道 '{}' (id={}) 已由其它进程占用，跳过",
                entry.name, entry.id,
            )
            return

        try:
            inst = cls(config=entry.config, manager=self)
            # 注入元数据供适配器使用（与原 ChannelRecord._record 兼容）
            inst._record = _FakeRecord(entry)  # type: ignore[attr-defined]
            self._instances[entry.id] = inst
            task = asyncio.ensure_future(self._run_instance(entry.id, inst))
            self._tasks[entry.id] = task
            logger.info("ChannelManager: 启动渠道 '{}' (type={})", entry.name, entry.type)
        except Exception:
            logger.exception("ChannelManager: 启动渠道失败 (id={})", entry.id)
            self._config_store.release_lock(entry.id)

    async def _run_instance(self, channel_id: str, inst: BaseChannel) -> None:
        """运行单个渠道实例（start() 持续阻塞直到 stop）"""
        try:
            await inst.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("ChannelManager: 渠道运行异常 (id={})", channel_id)
        finally:
            self._instances.pop(channel_id, None)
            self._tasks.pop(channel_id, None)
            # 实例退出时释放文件锁，允许其它进程接管
            self._config_store.release_lock(channel_id)

    async def _stop_channel(self, channel_id: str) -> None:
        """停止并移除单个渠道"""
        inst = self._instances.get(channel_id)
        task = self._tasks.get(channel_id)
        if inst is not None:
            try:
                await inst.stop()
            except Exception:
                logger.exception("ChannelManager: 停止渠道异常 (id={})", channel_id)
        if task is not None and not task.done():
            task.cancel()
        self._instances.pop(channel_id, None)
        self._tasks.pop(channel_id, None)

    # ------------------------------------------------------------------
    # 热加载回调（由 config_store.on_change 触发）
    # ------------------------------------------------------------------

    async def on_config_change(self) -> None:
        """配置文件变更后的热切换逻辑。

        diff 策略：
        - 新增 enabled 且未运行的 → 启动
        - 已运行但配置中 disabled 或删除的 → 停止
        - 已运行且 enabled 的 → 不中断（运行态由进程锁保证）
        """
        logger.info("ChannelManager: 配置文件变更，执行热切换 diff...")
        current_ids = set(self._instances.keys())
        desired_enabled = {
            entry.id: entry
            for entry in self._config_store.get_enabled()
        }

        # 停止：已运行但不在 desired_enabled 中的
        for cid in current_ids - set(desired_enabled.keys()):
            logger.info("ChannelManager: 热切换 — 停止渠道 {}", cid)
            await self._stop_channel(cid)

        # 启动：desired_enabled 中未运行的
        for cid, entry in desired_enabled.items():
            if cid not in self._instances:
                logger.info("ChannelManager: 热切换 — 启动渠道 '{}' (id={})", entry.name, cid)
                await self._start_channel(entry)

    # ------------------------------------------------------------------
    # 入站 → 内核 → 出站 闭环（与原版完全一致）
    # ------------------------------------------------------------------

    async def handle_inbound(self, msg: InboundMessage) -> None:
        """入站消息：触发内核 ChatService.run，并订阅其事件流转发到渠道。

        复用 ChatService 的 fire-and-forget 事件机制 —— run() 把 text_delta /
        reply_end 等事件 publish 到 MessageBusKeys.session_events(session_id)
        总线频道，本方法订阅该频道即可拿到流式输出，无需修改 ChatService。
        """
        inst = self._instances.get(msg.channel_id)
        if inst is None or not inst._running:
            logger.warning("ChannelManager: 渠道实例未运行，丢弃入站 (id={})", msg.channel_id)
            return

        # 渠道会话指针：同一 chat 的消息进入「当前」会话；空闲超时自动轮换新会话
        # （公共能力：所有渠道适配器共用；N 按渠道条目 id 配置；/new 手动轮换仍走 CommandService）
        rotated_from: str | None = None
        # N 单次读取：判定轮换与提示文案共用同一值，避免热加载后文案与实际阈值不一致
        idle_h = self._idle_hours(msg.channel_id)
        if self._session_map is not None:
            session_id, rotated_from = await self._session_map.resolve_or_rotate(
                msg.user_id, msg.channel, msg.chat_id,
                default=self.derive_session_id(msg.user_id, msg.channel, msg.chat_id),
                idle_hours=idle_h,
            )
            if rotated_from is not None and self._session_mgr is not None:
                # 过期旧会话：丢 KV（state + meta）与内存，PG conversations 历史保留
                try:
                    await self._session_mgr.delete_session(msg.user_id, rotated_from)
                except Exception:
                    logger.exception(
                        "ChannelManager: 过期会话 KV 清理失败 (session={})", rotated_from,
                    )
        else:
            session_id = self.derive_session_id(msg.user_id, msg.channel, msg.chat_id)

        # 指令拦截：命中即直接回执，不进 LLM、不发占位（D4/D5/D6）
        if not msg.media:
            cmd_result = await self._commands.try_handle(
                text=msg.content,
                user_id=msg.user_id,
                session_id=session_id,
                transport=msg.channel,
                chat_id=msg.chat_id,
                session_mgr=self._session_mgr,
                session_map=self._session_map,
                status_tracker=self._status_tracker,
                metadata=dict(msg.metadata or {}),
            )
            if cmd_result is not None:
                # 空闲自动轮换的确认语 + 会话ID 不能因指令提前 return 而丢失（表现对齐 /new）。
                # 指令自身又轮换（/new 的 new_session_id）时以指令回执为准，避免两个新会话 ID 打架；
                # 其 sid 随下一条普通消息的 §3.4 推送发出，与手动 /new 的表现一致。
                text = cmd_result.text
                if rotated_from is not None and not cmd_result.new_session_id:
                    # idle_h 传入带自动轮换确认语（与 /new 表现对齐）
                    text = f"{self._new_session_notice(session_id, idle_h)}\n{text}"
                stream_id = uuid.uuid4().hex
                try:
                    await inst.send_delta(
                        msg.chat_id, text,
                        stream_id=stream_id, stream_end=True,
                    )
                except Exception:
                    logger.exception(
                        "ChannelManager: 指令回执发送失败 (chat_id={})", msg.chat_id,
                    )
                logger.info(
                    "ChannelManager: 指令命中 transport={} text={!r} new_session={}",
                    msg.channel, msg.content.strip(), cmd_result.new_session_id,
                )
                return

        # 新会话推送 session_id（§3.4）：仅会话首次创建时推送一次（含 /new 轮换与
        # 空闲自动轮换后的首条消息），先于占位与实际对话发出；失败不阻断正常对话。
        # 空闲自动轮换的表现与 /new 对齐：确认语 + 会话 ID 一并发出。
        # rotated_from 是自动轮换的权威信号：一旦轮换必须发提示（用户要求「有提示有会话ID」），
        # 不得被 session_exists 的保守判定（PG 抖动时抛异常返回 True）吞掉，故短路跳过存在性检查。
        if rotated_from is not None or await self._session_is_new(msg.user_id, session_id):
            # idle_h 仅在自动轮换时带确认语；/new 手动轮换/首次创建走纯会话ID 文案
            body = self._new_session_notice(
                session_id, idle_h if rotated_from is not None else None,
            )
            try:
                await inst.send_delta(
                    msg.chat_id, body,
                    stream_id=uuid.uuid4().hex, stream_end=True,
                )
                logger.info(
                    "ChannelManager: 新会话推送 session_id transport={} session={} rotated_from={}",
                    msg.channel, session_id, rotated_from,
                )
            except Exception:
                logger.exception(
                    "ChannelManager: 会话ID 推送失败 (session={})", session_id,
                )

        # 提取渠道用户名（企微 from.name），用于落库 users.display_name
        sender_name = (msg.metadata or {}).get("sender_name") or None
        logger.info("[DEBUG] manager.handle_inbound sender_name={!r}, metadata={!r}", sender_name, msg.metadata)
        events_key = MessageBusKeys.session_events(session_id)
        stream_id = uuid.uuid4().hex

        # 先订阅内核事件总线，再触发 run —— 避免丢失早期 text_delta
        sub = self._bus.subscribe(events_key)

        # 先发占位，满足企微「收消息后 5s 内须发首片」约束
        try:
            await inst.send_delta(msg.chat_id, "…", stream_id=stream_id, stream_end=False)
        except Exception:
            logger.exception("ChannelManager: 发送占位失败 (chat_id={})", msg.chat_id)

        # 触发内核（后台 task，流式事件经总线回传）
        run_task = asyncio.ensure_future(
            self._safe_run(
                msg.user_id, session_id, msg.content, msg.media, stream_id, msg.chat_id, inst,
                display_name=sender_name, channel=msg.channel,
            )
        )

        full_text = ""
        done = False
        try:
            async for event in sub:
                etype = event.get("type")
                if etype == "text_delta":
                    full_text += event.get("delta", "")
                    # 企微 AI Bot 为「替换」语义：每次发送累积文本，气泡随增长
                    await inst.send_delta(msg.chat_id, full_text, stream_id=stream_id, stream_end=False)
                elif etype == "reply_end":
                    await inst.send_delta(msg.chat_id, full_text, stream_id=stream_id, stream_end=True)
                    done = True
                    break
                elif etype == "error":
                    await inst.send_delta(
                        msg.chat_id, f"\n\n[出错了] {event.get('message', '')}",
                        stream_id=stream_id, stream_end=True,
                    )
                    done = True
                    break
                elif etype == "run_end":
                    if not done:
                        await inst.send_delta(msg.chat_id, full_text, stream_id=stream_id, stream_end=True)
                    done = True
                    break
                # 忽略 thinking_delta / tool_call / busy / interrupted / run_start
        except asyncio.CancelledError:
            run_task.cancel()
        except Exception:
            logger.exception("ChannelManager: 订阅会话事件异常 (session={})", session_id)
        finally:
            try:
                await sub.aclose()
            except Exception:
                pass
            await run_task

    @staticmethod
    def _new_session_notice(session_id: str, idle_h: float | None = None) -> str:
        """新会话提示文案：idle_h 非空时带自动轮换确认语（与 /new 表现对齐）。"""
        body = f"本次对话会话ID: {session_id}\n可用于查询对话历史。"
        if idle_h is not None:
            body = (
                f"✅ 已自动开启新会话（距上次对话超过 {idle_h:g} 小时），"
                f"之前的历史不再携带。\n{body}"
            )
        return body

    def _idle_hours(self, channel_id: str) -> float:
        """会话空闲自动新开阈值（小时），按渠道条目（channels.json 内 id）解析。

        条目缺失或未配置 session_idle_hours 时回退默认 24；条目值 <=0 禁用自动轮换。
        """
        if self._config_store is not None:
            entry = self._config_store.get_by_id(channel_id)
            raw = getattr(entry, "session_idle_hours", None) if entry is not None else None
            if raw is not None:
                return float(raw)
        return DEFAULT_SESSION_IDLE_HOURS

    async def _session_is_new(self, user_id: str, session_id: str) -> bool:
        """会话是否尚未创建 — 新会话推送 session_id 的判定（§3.4）。

        未注入 session_mgr 或检查异常时返回 False（不推送），避免重复打扰。
        """
        if self._session_mgr is None:
            return False
        try:
            return not await self._session_mgr.session_exists(user_id, session_id)
        except Exception:
            logger.exception(
                "ChannelManager: 会话存在性检查失败 (session={})", session_id,
            )
            return False

    async def _safe_run(
        self,
        user_id: str,
        session_id: str,
        content: str,
        media: list[str] | None,
        stream_id: str,
        chat_id: str,
        inst: BaseChannel,
        display_name: str | None = None,
        channel: str = "web",
    ) -> None:
        """调用内核 ChatService.run（fire-and-forget），异常兜底。"""
        try:
            await self._chat_service.run(
                user_id,
                session_id,
                content,
                db=self._db_ref,  # type: ignore[attr-defined]
                device_id=f"channel:{inst.name}",
                object_storage=self._object_storage,
                display_name=display_name,
                channel=channel,
            )
        except Exception:
            logger.exception("ChannelManager: ChatService.run 异常 (session={})", session_id)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    async def get_runtime_status(self) -> dict[str, dict]:
        """返回运行态：{channel_id: {running, state, error, type, name}}"""
        status: dict[str, dict] = {}
        for channel_id, inst in self._instances.items():
            rec = getattr(inst, "_record", None)
            status[channel_id] = {
                "id": channel_id,
                "type": inst.name,
                "name": rec.name if rec is not None else inst.name,
                "running": inst._running,
                "state": "running" if inst._running else "stopped",
                "error": getattr(inst, "_error", None),
            }
        return status


class _FakeRecord:
    """适配器兼容层：基于 ChannelEntry 模拟渠道记录，让适配器的 inst._record 读取不报错"""

    def __init__(self, entry: ChannelEntry) -> None:
        self.id = entry.id
        self.user_id = "unkown_user_id"  # JSON 配置无 user_id 概念
        self.name = entry.name
        self.type = entry.type
        self.enabled = entry.enabled
        self.status = "running"

    def __repr__(self) -> str:
        return f"<FakeRecord id={self.id} type={self.type} name={self.name}>"