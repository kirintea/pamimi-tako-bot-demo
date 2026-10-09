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
import hashlib
import uuid

from agentscope.app.message_bus import MessageBusKeys
from loguru import logger

from core.channels.base import BaseChannel
from core.channels.config_store import ChannelConfigStore, ChannelEntry
from core.channels.events import InboundMessage
from core.channels.registry import get_channel_class
# [已删除] core.storage_models 的 ChannelRecord / ChannelStatus 随 channels 表一并移除，
# 渠道记录改用 core.channels.config_store.ChannelEntry。


class ChannelManager:
    """渠道管理器 — JSON 文件配置驱动"""

    def __init__(
        self,
        *,
        # storage,         # DB storage（已注释，改用 config_store）
        # db,              # DB 连接（已注释）
        config_store: ChannelConfigStore,
        bus,
        chat_service,
        object_storage,
    ) -> None:
        # self._storage = storage      # DB storage — 已注释
        # self._db = db                # DB — 已注释
        self._config_store = config_store
        self._bus = bus
        self._chat_service = chat_service
        self._object_storage = object_storage

        # channel_id → 适配器实例 / 后台任务
        self._instances: dict[str, BaseChannel] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # 会话键（跨渠道 chat_id 隔离）
    # ------------------------------------------------------------------

    @staticmethod
    def derive_session_id(user_id: str, channel: str, chat_id: str) -> str:
        """为 (user_id, channel, chat_id) 生成稳定且唯一的会话 id（≤ 64 字符）

        使用 sha256 截断，避免 chat_id 过长或含特殊字符破坏 sessions.id 约束。
        """
        raw = f"{user_id}|{channel}|{chat_id}".encode("utf-8")
        return "ch-" + hashlib.sha256(raw).hexdigest()[:24]

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

        session_id = self.derive_session_id(msg.user_id, msg.channel, msg.chat_id)
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

    async def _safe_run(
        self,
        user_id: str,
        session_id: str,
        content: str,
        media: list[str] | None,
        stream_id: str,
        chat_id: str,
        inst: BaseChannel,
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
        self.user_id = "config-file"  # JSON 配置无 user_id 概念
        self.name = entry.name
        self.type = entry.type
        self.enabled = entry.enabled
        self.status = "running"

    def __repr__(self) -> str:
        return f"<FakeRecord id={self.id} type={self.type} name={self.name}>"