# -*- coding: utf-8 -*-
"""NapCat 渠道单测 — 白名单/启动硬校验/入站路由（D3-A 群会话）/群策略/send_delta（D2）/
action 关联与清理/图片下载/配置归一/注册与 API 接线

全部用 Fake 替身（FakeManager / FakeWs / FakeHttp），不依赖真实 NapCat 与网络：
公网地址一律用 IP 字面量（getaddrinfo 数字解析，不发 DNS 查询）。
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from api.channels import ChannelConfigRequest, _entry_to_response
from core.channels.adapters.napcat.config import (
    DEFAULT_MAX_IMAGE_BYTES,
    NapcatSettings,
)
from core.channels.adapters.napcat.runtime import NapcatChannel
from core.channels.catalog import (
    CHANNEL_DEPENDENCIES,
    CHANNEL_ORDER,
    get_channel_catalog,
)
from core.channels.config_store import ChannelEntry
from core.channels.events import InboundMessage, OutboundMessage
from core.channels.registry import REGISTRY

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# 替身
# ============================================================


class FakeManager:
    def __init__(self) -> None:
        self.inbound: list[InboundMessage] = []

    async def handle_inbound(self, msg: InboundMessage) -> None:
        self.inbound.append(msg)


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResp:
    def __init__(self, status: int = 200, chunks: list[bytes] | None = None) -> None:
        self.status = status
        self.content = FakeContent(chunks or [])

    async def __aenter__(self) -> "FakeResp":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeHttp:
    def __init__(self, resp: FakeResp | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._resp = resp or FakeResp()

    def get(self, url: str, **kwargs) -> FakeResp:
        self.calls.append((url, kwargs))
        return self._resp


def make_channel(**overrides) -> tuple[NapcatChannel, FakeManager]:
    cfg: dict = {"ws_url": "ws://127.0.0.1:3001", "allow_from": ["123456"]}
    cfg.update(overrides)
    mgr = FakeManager()
    ch = NapcatChannel(cfg, mgr)
    ch._self_id = 999  # 被 @ 的机器人 QQ 号
    return ch, mgr


def group_ev(**over) -> dict:
    ev: dict = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 100,
        "user_id": 123456,
        "message_id": 1,
        "sender": {"user_id": 123456, "nickname": "Alice", "card": ""},
        "message": [{"type": "text", "data": {"text": "hi"}}],
    }
    ev.update(over)
    return ev


def private_ev(**over) -> dict:
    ev: dict = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 123456,
        "message_id": 2,
        "sender": {"user_id": 123456, "nickname": "Alice", "card": ""},
        "message": [{"type": "text", "data": {"text": "secret"}}],
    }
    ev.update(over)
    return ev


# ============================================================
# D4：鉴权与启动硬校验
# ============================================================


async def test_is_allowed_empty_denies_star_allows_exact_matches():
    ch, _ = make_channel(allow_from=[])
    assert ch.is_allowed("123456") is False  # 空名单 = 拒绝（覆盖基类默认全放行）
    ch2, _ = make_channel(allow_from=["*"])
    assert ch2.is_allowed("任意 QQ") is True
    ch3, _ = make_channel(allow_from=["123456"])
    assert ch3.is_allowed("123456") is True
    assert ch3.is_allowed("999999") is False


async def test_start_refuses_empty_ws_url():
    ch, _ = make_channel(ws_url="")
    await ch.start()
    assert ch._running is False
    assert ch._error and "ws_url" in ch._error
    assert ch._http is None  # 校验失败不建连接资源


async def test_start_refuses_empty_allow_from():
    ch, _ = make_channel(allow_from=[])
    await ch.start()
    assert ch._running is False
    assert ch._error and "allow_from" in ch._error
    assert ch._http is None


async def test_stop_without_connection_is_safe():
    ch, _ = make_channel()
    await ch.stop()
    assert ch._running is False
    assert ch._ws is None


# ============================================================
# 入站：群聊（D3-A 会话归群）与群策略
# ============================================================


async def test_group_mention_routes_to_group_session():
    ch, mgr = make_channel()

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        assert action == "get_group_info"  # 群会话 display_name 查群名称
        return {"status": "ok", "retcode": 0, "data": {"group_name": "测试群"}}

    ch._call_action = fake_call
    ev = group_ev(
        message=[
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "hello"}},
        ]
    )
    await ch._on_message(ev)
    assert len(mgr.inbound) == 1
    msg = mgr.inbound[0]
    # D3-A：user_id 取群维度（整群一个会话）；边界转换后平台侧为 group_100
    assert msg.user_id == "group_100"
    assert msg.chat_id == "group_100"
    assert msg.sender_id == "123456"
    # 发言人包装：QQ 是角色识别唯一依据，昵称仅展示
    assert msg.content == "Alice(123456): hello"
    assert msg.channel == "napcat"
    assert msg.channel_id == "napcat"
    assert msg.metadata["is_group"] is True
    assert msg.metadata["message_id"] == 1
    # display_name = 群名称，不是首个发言人昵称
    assert msg.metadata["sender_name"] == "测试群"
    assert msg.metadata["nickname"] == "Alice"


async def test_group_plain_message_dropped_under_mention_policy():
    ch, mgr = make_channel()  # 默认 group_policy=mention
    await ch._on_message(group_ev())
    assert mgr.inbound == []


async def test_group_policy_override_open_routes_without_mention():
    ch, mgr = make_channel(group_policy_overrides={"100": "open"})
    await ch._on_message(group_ev())
    assert len(mgr.inbound) == 1
    assert mgr.inbound[0].content == "Alice(123456): hi"


async def test_group_sender_without_nickname_falls_back_to_bare_qq():
    """昵称缺失 → 包装回退裸 QQ 号（QQ 是角色识别唯一依据）。"""
    ch, mgr = make_channel(group_policy_overrides={"100": "open"})
    await ch._on_message(group_ev(sender={"user_id": 123456, "nickname": "", "card": ""}))
    assert len(mgr.inbound) == 1
    assert mgr.inbound[0].content == "123456: hi"


async def test_group_policy_override_does_not_leak_to_other_groups():
    ch, mgr = make_channel(group_policy_overrides={"100": "open"})
    await ch._on_message(group_ev(group_id=200))
    assert mgr.inbound == []  # 200 群仍用全局 mention


async def test_reply_to_bot_routes_without_mention():
    ch, mgr = make_channel()
    ch._bot_outbound_ids.append(4242)
    ev = group_ev(
        message=[
            {"type": "reply", "data": {"id": "4242"}},
            {"type": "text", "data": {"text": "再来一次"}},
        ]
    )
    await ch._on_message(ev)
    assert len(mgr.inbound) == 1
    assert "Alice(123456): 再来一次" == mgr.inbound[0].content
    assert mgr.inbound[0].metadata["reply_to"] == 4242


async def test_sender_not_in_whitelist_dropped():
    ch, mgr = make_channel(allow_from=["111111"])
    await ch._on_message(
        group_ev(
            message=[
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": "hello"}},
            ]
        )
    )
    assert mgr.inbound == []


async def test_duplicate_message_id_dropped():
    ch, mgr = make_channel()
    ev = group_ev(
        message=[
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "hello"}},
        ]
    )
    await ch._on_message(ev)
    await ch._on_message(dict(ev))
    assert len(mgr.inbound) == 1


# ============================================================
# 入站：私聊
# ============================================================


async def test_private_message_routes_to_private_session():
    ch, mgr = make_channel()
    await ch._on_message(private_ev())
    assert len(mgr.inbound) == 1
    msg = mgr.inbound[0]
    assert msg.user_id == "123456"
    assert msg.chat_id == "private:123456"
    assert msg.sender_id == "123456"
    assert msg.content == "secret"  # 私聊不加发送者前缀
    assert msg.metadata["is_group"] is False
    # 私聊 display_name = 发言人昵称（单对单，非群名称）
    assert msg.metadata["sender_name"] == "Alice"


async def test_non_message_types_dropped():
    ch, mgr = make_channel()
    await ch._on_message(group_ev(message_type="channel"))
    await ch._on_message({"post_type": "message", "message_type": "group"})  # 缺 user_id
    assert mgr.inbound == []


# ============================================================
# 入站：群通知（迎新）
# ============================================================


async def test_notice_group_increase_welcome():
    ch, mgr = make_channel(allow_from=["*"])

    async def lookup(group_id: int, user_id: int) -> str:
        return "Bob"

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        assert action == "get_group_info"
        return {"status": "ok", "retcode": 0, "data": {"group_name": "测试群"}}

    ch._lookup_member_name = lookup
    ch._call_action = fake_call
    await ch._on_notice(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": 100, "user_id": 555}
    )
    assert len(mgr.inbound) == 1
    msg = mgr.inbound[0]
    assert msg.user_id == "group_100"
    assert "Bob" in msg.content
    assert "测试群" in msg.content
    assert msg.metadata["event"] == "group_increase"
    # 群会话 display_name = 群名称（新成员昵称只进内容）
    assert msg.metadata["sender_name"] == "测试群"


async def test_lookup_group_name_caches_and_falls_back():
    """群名称进程内缓存；查询失败回退 `群{id}` 且不缓存（下条消息重试）。"""
    ch, _ = make_channel()
    calls: list[str] = []

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        calls.append(action)
        return {"status": "ok", "retcode": 0, "data": {"group_name": "猫猫群"}}

    ch._call_action = fake_call
    assert await ch._lookup_group_name(100) == "猫猫群"
    assert await ch._lookup_group_name(100) == "猫猫群"
    assert calls == ["get_group_info"]  # 第二次命中缓存

    async def boom(action: str, params: dict, timeout: float = 20.0) -> dict:
        raise RuntimeError("NapCat 未连接")

    ch._call_action = boom
    assert await ch._lookup_group_name(200) == "群200"  # 失败回退
    assert await ch._lookup_group_name("abc") == "群abc"  # 非法 id 直接回退，不发请求


async def test_notice_invalid_ids_ignored_without_lookup():
    ch, mgr = make_channel(allow_from=["*"])

    async def boom(group_id: int, user_id: int) -> str:
        raise AssertionError("非法 id 不应触发群成员查询")

    ch._lookup_member_name = boom
    await ch._on_notice(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": "abc", "user_id": 555}
    )
    await ch._on_notice(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": 100, "user_id": None}
    )
    assert mgr.inbound == []


async def test_notice_welcome_disabled_no_inbound():
    ch, mgr = make_channel(allow_from=["*"], welcome_new_members=False)
    await ch._on_notice(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": 100, "user_id": 555}
    )
    assert mgr.inbound == []


async def test_notice_new_member_not_whitelisted_dropped():
    ch, mgr = make_channel(allow_from=["123456"])  # 555 不在名单
    await ch._on_notice(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": 100, "user_id": 555}
    )
    assert mgr.inbound == []


# ============================================================
# 帧分发
# ============================================================


async def test_dispatch_resolves_pending_echo():
    ch, _ = make_channel()
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    ch._pending["e1"] = fut
    await ch._dispatch_frame('{"echo":"e1","status":"ok","retcode":0,"data":{}}')
    assert fut.done()
    assert fut.result()["retcode"] == 0
    assert "e1" not in ch._pending


async def test_dispatch_invalid_json_ignored():
    ch, _ = make_channel()
    await ch._dispatch_frame("not-json{{")
    assert ch._pending == {}


async def test_background_task_recycled_on_failure():
    ch, _ = make_channel()

    async def boom() -> None:
        raise RuntimeError("x")

    ch._create_background_task(boom(), "message")
    for _ in range(10):
        await asyncio.sleep(0)
        if not ch._background_tasks:
            break
    assert ch._background_tasks == set()


# ============================================================
# 出站
# ============================================================


async def test_send_raises_when_not_connected():
    ch, _ = make_channel()
    ch._ws = None
    with pytest.raises(RuntimeError, match="未连接"):
        await ch.send(OutboundMessage(channel="napcat", chat_id="group_100", content="x"))


async def test_send_group_builds_send_msg_action():
    ch, _ = make_channel()
    ch._ws = FakeWs()
    calls: list[tuple[str, dict]] = []

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 777}}

    ch._call_action = fake_call
    # 平台侧入参为 group_100（过 _ID_RE），send 入口转回原生 group:100 再 partition
    await ch.send(OutboundMessage(channel="napcat", chat_id="group_100", content="pong"))
    assert len(calls) == 1
    action, params = calls[0]
    assert action == "send_msg"
    assert params["message_type"] == "group"
    assert params["group_id"] == 100
    assert params["message"] == [{"type": "text", "data": {"text": "pong"}}]
    assert 777 in ch._bot_outbound_ids  # 出站记账 → reply 段可识别「回复我」


async def test_send_private_chat_id_passed_through():
    """private:{id} 出站不经转换，仍走 private 分支。"""
    ch, _ = make_channel()
    ch._ws = FakeWs()
    calls: list[tuple[str, dict]] = []

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}

    ch._call_action = fake_call
    await ch.send(OutboundMessage(channel="napcat", chat_id="private:123", content="hi"))
    assert len(calls) == 1
    action, params = calls[0]
    assert action == "send_msg"
    assert params["message_type"] == "private"
    assert params["user_id"] == 123


async def test_send_invalid_chat_id_no_action():
    ch, _ = make_channel()
    ch._ws = FakeWs()
    calls: list[str] = []

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        calls.append(action)
        return {"status": "ok", "retcode": 0, "data": {}}

    ch._call_action = fake_call
    await ch.send(OutboundMessage(channel="napcat", chat_id="bogus-id", content="x"))
    # group_abc → group:abc：target 非数字，记错误且不发请求
    await ch.send(OutboundMessage(channel="napcat", chat_id="group_abc", content="x"))
    assert calls == []


async def test_send_local_image_inlined_as_base64(tmp_path: Path):
    ch, _ = make_channel()
    ch._ws = FakeWs()
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG-fake")
    calls: list[dict] = []

    async def fake_call(action: str, params: dict, timeout: float = 20.0) -> dict:
        calls.append(params)
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}

    ch._call_action = fake_call
    await ch.send(
        OutboundMessage(channel="napcat", chat_id="private:123456", content="", media=[str(img)])
    )
    seg = calls[0]["message"][0]
    expected = "base64://" + base64.b64encode(b"\x89PNG-fake").decode()
    assert seg == {"type": "image", "data": {"file": expected}}


async def test_build_image_segment_public_url_passthrough():
    ch, _ = make_channel()
    seg = await ch._build_image_segment("https://8.8.8.8/a.png")
    assert seg == {"type": "image", "data": {"file": "https://8.8.8.8/a.png"}}


# ============================================================
# D2：send_delta 仅收尾发送
# ============================================================


async def test_send_delta_ignores_non_end_chunks():
    ch, _ = make_channel()
    sent: list[OutboundMessage] = []

    async def fake_send(msg: OutboundMessage) -> None:
        sent.append(msg)

    ch.send = fake_send
    await ch.send_delta("private:1", "占位", stream_id="s1", stream_end=False)
    await ch.send_delta("private:1", "累积文本", stream_id="s1", stream_end=False)
    assert sent == []


async def test_send_delta_end_sends_full_text_once():
    ch, _ = make_channel()
    sent: list[OutboundMessage] = []

    async def fake_send(msg: OutboundMessage) -> None:
        sent.append(msg)

    ch.send = fake_send
    await ch.send_delta("private:1", "最终全文", stream_id="s1", stream_end=True)
    assert len(sent) == 1
    assert sent[0].content == "最终全文"
    assert sent[0].chat_id == "private:1"
    assert sent[0].stream_end is True


async def test_send_delta_empty_end_skipped():
    ch, _ = make_channel()
    sent: list[OutboundMessage] = []

    async def fake_send(msg: OutboundMessage) -> None:
        sent.append(msg)

    ch.send = fake_send
    await ch.send_delta("private:1", "   ", stream_id="s2", stream_end=True)
    assert sent == []


# ============================================================
# action 关联
# ============================================================


async def test_call_action_timeout_clears_pending():
    ch, _ = make_channel()
    ch._ws = FakeWs()  # 永不回包
    with pytest.raises(TimeoutError):
        await ch._call_action("get_status", {}, timeout=0.05)
    assert ch._pending == {}


async def test_call_action_raises_when_not_connected():
    ch, _ = make_channel()
    ch._ws = None
    with pytest.raises(RuntimeError, match="未连接"):
        await ch._call_action("get_status", {})


# ============================================================
# 图片下载：限额
# ============================================================


async def test_download_image_success_saves_file(tmp_path: Path):
    ch, _ = make_channel()
    ch._media_root = tmp_path
    ch._http = FakeHttp(FakeResp(200, [b"img-", b"data"]))
    path = await ch._download_image(
        {"url": "https://8.8.8.8/a.png", "file": "evil/../photo.png", "file_size": 8}
    )
    assert path is not None
    # 文件名经 basename + 白名单清洗，防止路径穿越
    assert Path(path).parent == tmp_path
    assert Path(path).name == "photo.png"
    assert Path(path).read_bytes() == b"img-data"


async def test_download_image_redirect_rejected():
    ch, _ = make_channel()
    http = FakeHttp(FakeResp(302, []))
    ch._http = http
    result = await ch._download_image({"url": "https://8.8.8.8/r.png"})
    assert result is None
    assert http.calls, "应发起过请求"
    assert http.calls[0][1]["allow_redirects"] is False  # 禁跟随跳转


async def test_download_image_declared_size_over_limit_skips_http():
    ch, _ = make_channel()
    http = FakeHttp(FakeResp(200, [b"x"]))
    ch._http = http
    result = await ch._download_image(
        {"url": "https://8.8.8.8/big.png", "file_size": DEFAULT_MAX_IMAGE_BYTES + 1}
    )
    assert result is None
    assert http.calls == []


async def test_download_image_streaming_cap_enforced(tmp_path: Path):
    ch, _ = make_channel(max_image_bytes=4)
    ch._media_root = tmp_path
    ch._http = FakeHttp(FakeResp(200, [b"1234", b"5678"]))  # 共 8 字节 > 4
    result = await ch._download_image({"url": "https://8.8.8.8/c.png"})
    assert result is None
    assert list(tmp_path.iterdir()) == []  # 超限不落盘


# ============================================================
# 配置归一（D5 宽松解析）
# ============================================================


async def test_settings_coerce_normalizes_webui_strings():
    s = NapcatSettings.coerce(
        {
            "ws_url": "  ws://127.0.0.1:3001  ",
            "allow_from": "111, 222\n333",
            "group_policy": "0.2",
            "group_policy_overrides": '{"100":"open","200":"banana","300":0.7}',
            "welcome_new_members": "false",
            "max_image_bytes": "1024",
        }
    )
    assert s.ws_url == "ws://127.0.0.1:3001"
    assert s.allow_from == ["111", "222", "333"]
    assert s.group_policy == pytest.approx(0.2)
    # 非法项 "banana" 丢弃（回退全局策略），合法项保留
    assert s.group_policy_overrides == {"100": "open", "300": pytest.approx(0.7)}
    assert s.welcome_new_members is False
    assert s.max_image_bytes == 1024


async def test_settings_coerce_defaults_and_fallbacks():
    s = NapcatSettings.coerce({})
    assert s.ws_url == ""
    assert s.allow_from == []
    assert s.group_policy == "mention"
    assert s.group_policy_overrides == {}
    assert s.welcome_new_members is True
    assert s.max_image_bytes == DEFAULT_MAX_IMAGE_BYTES

    bad = NapcatSettings.coerce(
        {"group_policy": True, "max_image_bytes": "abc", "welcome_new_members": "nope"}
    )
    assert bad.group_policy == "mention"  # bool 非法（True≡1 会被误读成全回）
    assert bad.max_image_bytes == DEFAULT_MAX_IMAGE_BYTES
    # 计划 §4 D5：welcome_new_members 未知非空串 → 按 true 处理（default=True）
    assert bad.welcome_new_members is True


# ============================================================
# 注册 / 目录 / API 接线
# ============================================================


async def test_registry_catalog_and_api_wiring():
    # registry
    assert REGISTRY.get("napcat") is NapcatChannel

    # catalog
    assert "napcat" in CHANNEL_ORDER
    assert CHANNEL_DEPENDENCIES["napcat"] == [{"name": "aiohttp", "import_name": "aiohttp"}]
    entry = next(e for e in get_channel_catalog() if e["type"] == "napcat")
    assert entry["backend_supported"] is True
    assert entry["fields"], "napcat 创建表单字段不应为空"
    assert entry["display_name"] == "NapCat (QQ)"

    # API：extra="allow" —— napcat 自有配置键不得被剥离
    req = ChannelConfigRequest(ws_url="ws://127.0.0.1:3001", allow_from=["1"], access_token="tok")
    dumped = req.model_dump(exclude_none=True)
    assert dumped["ws_url"] == "ws://127.0.0.1:3001"
    assert dumped["access_token"] == "tok"

    # API：secret 与 access_token 均脱敏
    ce = ChannelEntry(
        id="c1",
        name="qq",
        type="napcat",
        enabled=True,
        config={"ws_url": "ws://x", "access_token": "tok", "secret": "s"},
    )
    resp = _entry_to_response(ce)
    assert resp.config["access_token"] == "********"
    assert resp.config["secret"] == "********"
    assert resp.config["ws_url"] == "ws://x"
