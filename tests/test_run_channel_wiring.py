# -*- coding: utf-8 -*-

"""channel 透传 wiring 测试 — 渠道对话入库不再硬编码 channel='web'

Bug：ChatService.run() 的 PG 双写 3 处 insert_conversation 硬编码 channel="web"，
且 ChannelManager 未把 InboundMessage.channel 传入内核，导致企微等渠道的
对话记录 channel 清一色 'web'。

约定（与 display_name 透传同路径）：
    ChannelManager.handle_inbound → _safe_run(channel=msg.channel)
      → ChatService.run(channel=...) → db.insert_conversation(channel=...)
web 路径（/chat/stream、/ws/chat、/chat/）不传 channel，保持默认 'web'。
"""

from types import SimpleNamespace

import pytest

from agentscope.event import EventType
from agentscope.app.message_bus import MessageBusKeys
from core.channels.manager import ChannelManager
from core.chat_service import ChatService
from core.message_bus import InMemoryMessageBus
from core.object_storage.local import LocalStorage

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# 假对象
# ============================================================


class _TextAgent:
    """产出一个 text delta 的假 Agent（走 v3 turn_rows 落库路径）"""

    async def reply_stream(self, msg):
        yield SimpleNamespace(type=EventType.TEXT_BLOCK_DELTA, delta="你好")
        yield SimpleNamespace(type=EventType.REPLY_END, finished_reason="stop")


class _RecordingSessionMgr:
    """只实现 ChatService.run() 用到的三个方法"""

    def __init__(self, agent) -> None:
        self._agent = agent

    async def get_or_create(self, user_id, session_id, display_name=None, channel=None):
        return self._agent

    async def refresh_state(self, user_id, session_id) -> None:
        pass

    async def save(self, user_id, session_id) -> None:
        pass


class _RecordingDB:
    """记录 insert_conversation 落库参数的假 DatabaseManager"""

    is_initialized = True

    def __init__(self) -> None:
        self.inserts: list[dict] = []

    async def insert_conversation(
        self, user_id, session_id, role, content,
        metadata=None, channel="web", turn_id=None, turn_seq=None,
    ):
        self.inserts.append({"role": role, "content": content, "channel": channel})
        return len(self.inserts)

    async def get_session_title(self, user_id, session_id):
        return "已有标题"

    async def upsert_session_title(self, user_id, session_id, title):
        pass

    async def archive_conversation_rows(self, ids):
        pass


async def _run_once(channel: str | None = None) -> _RecordingDB:
    bus = InMemoryMessageBus()
    db = _RecordingDB()
    service = ChatService(_RecordingSessionMgr(_TextAgent()), bus)
    kwargs = {"db": db} if channel is None else {"db": db, "channel": channel}
    await service.run("u1", "s1", "在吗", **kwargs)
    await bus.aclose()
    return db


# ============================================================
# ChatService.run — 落库 channel
# ============================================================


async def test_run_persists_explicit_channel_on_all_rows():
    """channel='wecom' 时 user/assistant 各行都落 'wecom'（不再硬编码 web）"""
    db = await _run_once(channel="wecom")
    assert db.inserts, "应有落库记录"
    assert {row["channel"] for row in db.inserts} == {"wecom"}
    assert db.inserts[0]["role"] == "user"
    assert any(row["role"] == "assistant" for row in db.inserts), "assistant 行应落库"


async def test_run_defaults_to_web():
    """web 路径不传 channel → 保持默认 'web'"""
    db = await _run_once()
    assert db.inserts
    assert {row["channel"] for row in db.inserts} == {"web"}


# ============================================================
# ChatService.run — 多模态 message 的状态预览
# ============================================================


class _RecordingStatusTracker:
    """记录 set_generating 预览参数的假 tracker"""

    def __init__(self) -> None:
        self.previews: list = []

    async def set_generating(self, session_id, owner, user_msg=""):
        self.previews.append(user_msg)

    async def set_idle(self, session_id) -> None:
        pass

    async def get_status(self, session_id) -> dict:
        return {"state": "idle"}

    async def should_cancel(self, session_id) -> bool:
        return False


async def test_run_serializes_multimodal_preview():
    """message 为 list[ContentPart] 时 set_generating 必须收到可 JSON 化的 str

    真实 SessionStatusTracker.set_generating 会对预览做 json.dumps
    （core/session_status.py），裸 pydantic 列表会 TypeError 炸掉整个 run。
    """
    import json

    from core.multimodal import TextContent

    bus = InMemoryMessageBus()
    tracker = _RecordingStatusTracker()
    service = ChatService(_RecordingSessionMgr(_TextAgent()), bus, status_tracker=tracker)
    await service.run("u1", "s1", [TextContent(text="看图")], channel="wecom")
    await bus.aclose()

    assert tracker.previews, "set_generating 应被调用"
    preview = tracker.previews[0]
    assert isinstance(preview, str)
    assert json.loads(preview)[0]["text"] == "看图"


# ============================================================
# ChannelManager — 入站 channel 透传
# ============================================================


class _RecordingChatService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})


class _FakeInst:
    name = "wecom"
    _running = True

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
        self.sent.append((chat_id, delta, stream_end))


def _make_manager(chat_service) -> ChannelManager:
    return ChannelManager(
        config_store=None,
        bus=InMemoryMessageBus(),
        chat_service=chat_service,
        object_storage=None,
        db=None,
    )


async def test_safe_run_forwards_channel():
    """_safe_run 把 channel 原样传给 ChatService.run"""
    svc = _RecordingChatService()
    mgr = _make_manager(svc)
    inst = _FakeInst()
    await mgr._safe_run(
        "u1", "s1", "hi", None, "sid", "chat1", inst,
        display_name="张三", channel="wecom",
    )
    assert svc.calls and svc.calls[0].get("channel") == "wecom"
    await mgr._bus.aclose()


async def test_handle_inbound_passes_msg_channel():
    """handle_inbound 将 InboundMessage.channel 透传给内核 run"""
    svc = _RecordingChatService()
    mgr = _make_manager(svc)
    inst = _FakeInst()
    mgr._instances["c1"] = inst

    # 用真实 _safe_run 的替身：记录参数并发布 run_end 让 handle_inbound 收尾
    captured: dict = {}

    async def fake_safe_run(user_id, session_id, content, media, stream_id, chat_id,
                            instance, display_name=None, channel="web"):
        captured["channel"] = channel
        await mgr._bus.publish(
            MessageBusKeys.session_events(session_id), {"type": "run_end"},
        )

    mgr._safe_run = fake_safe_run

    from core.channels.events import InboundMessage
    msg = InboundMessage(
        channel="wecom",
        channel_id="c1",
        user_id="u1",
        chat_id="chat1",
        sender_id="s1",
        content="在吗",
    )
    await mgr.handle_inbound(msg)

    assert captured.get("channel") == "wecom"
    await mgr._bus.aclose()


# ============================================================
# ChannelManager — media 转多模态 message
# ============================================================


async def test_safe_run_converts_media_to_content_parts(tmp_path):
    """media 非空时 _safe_run 上传对象存储并以 list[ContentPart] 调内核"""
    storage = LocalStorage(base_dir=str(tmp_path / "oss"))
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG-fake")

    svc = _RecordingChatService()
    mgr = ChannelManager(
        config_store=None,
        bus=InMemoryMessageBus(),
        chat_service=svc,
        object_storage=storage,
        db=None,
    )
    inst = _FakeInst()
    await mgr._safe_run(
        "u1", "s1", "[image: pic.png]", [str(img)], "sid", "chat1", inst,
        display_name="张三", channel="wecom",
    )

    assert svc.calls, "ChatService.run 应被调用"
    message = svc.calls[0]["args"][2]
    assert isinstance(message, list)
    assert [p.type for p in message] == ["text", "image"]
    assert message[1].key.endswith(".png")
    await mgr._bus.aclose()


async def test_safe_run_without_media_passes_str(tmp_path):
    """media 为空 → 仍传纯字符串（回归保护，非图片文本路径不变）"""
    storage = LocalStorage(base_dir=str(tmp_path / "oss"))
    svc = _RecordingChatService()
    mgr = ChannelManager(
        config_store=None,
        bus=InMemoryMessageBus(),
        chat_service=svc,
        object_storage=storage,
        db=None,
    )
    inst = _FakeInst()
    await mgr._safe_run(
        "u1", "s1", "在吗", None, "sid", "chat1", inst, channel="wecom",
    )

    assert svc.calls[0]["args"][2] == "在吗"
    await mgr._bus.aclose()


# ============================================================
# 端到端 — 企微图片 → LLM DataBlock + 落库 JSON
# ============================================================


class _CapturingAgent:
    """捕获 reply_stream 入参 UserMsg 的假 Agent"""

    def __init__(self) -> None:
        self.user_msg = None

    async def reply_stream(self, msg):
        self.user_msg = msg
        yield SimpleNamespace(type=EventType.TEXT_BLOCK_DELTA, delta="好的")
        yield SimpleNamespace(type=EventType.REPLY_END, finished_reason="stop")


async def test_wecom_image_reaches_llm_as_datablock(tmp_path):
    """验收：企微下载的本地图片 → UserMsg 含图片 DataBlock，落库为 ContentPart JSON"""
    import json

    storage = LocalStorage(base_dir=str(tmp_path / "oss"))
    img = tmp_path / "企业微信截图_17916000603921.png"
    img.write_bytes(b"\x89PNG-fake-bytes")

    agent = _CapturingAgent()
    db = _RecordingDB()
    bus = InMemoryMessageBus()
    mgr = ChannelManager(
        config_store=None,
        bus=bus,
        chat_service=ChatService(_RecordingSessionMgr(agent), bus),
        object_storage=storage,
        db=db,
    )
    try:
        await mgr._safe_run(
            "u1", "s1", "[image: 企业微信截图_17916000603921.png]", [str(img)],
            "sid", "chat1", _FakeInst(), channel="wecom",
        )
    finally:
        await mgr._bus.aclose()

    # LLM 侧：UserMsg 由文本块 + 图片 DataBlock 组成
    assert agent.user_msg is not None
    assert len(agent.user_msg.content) == 2
    assert agent.user_msg.content[0].text == "[image: 企业微信截图_17916000603921.png]"
    assert agent.user_msg.content[1].source.media_type == "image/png"

    # 落库侧：user 行是 ContentPart JSON（与 web 图片消息同构），不再是纯占位文本
    user_rows = [r for r in db.inserts if r["role"] == "user"]
    assert user_rows
    stored = json.loads(user_rows[0]["content"])
    assert [p["type"] for p in stored] == ["text", "image"]
    assert stored[1]["key"].endswith(".png")
