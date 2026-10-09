# -*- coding: utf-8 -*-

"""WeCom (企业微信) AI Bot 渠道适配器

基于企业微信 AI 机器人 WebSocket 长连接（wss://openws.work.weixin.qq.com）：
- 无需公网 IP / 回调地址，SDK 负责重连与心跳
- 原生流式回复：reply_stream(frame, stream_id, content, finish)
- 媒体（图片/文件）经 SDK 下载（带 aeskey 解密）后落本地，URL 文本化后入内核

改造：
- 继承本项目的 core.channels.base.BaseChannel（manager 注入而非 bus）
- 入站产出 core.channels.events.InboundMessage → self.handle_inbound()（鉴权 + 转发内核）
- 出站重写 send_delta，直接走 SDK reply_stream（累积「替换」语义，适配企微气泡）
- owner user_id / channel_id 取自 manager 注入的 record，用于会话键隔离
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from loguru import logger

from core.channels.adapters.wecom.config import WecomSettings
from core.channels.base import BaseChannel
from core.channels.events import InboundMessage, OutboundMessage

WECOM_AVAILABLE = importlib.util.find_spec("wecom_aibot_sdk") is not None
WECOM_WEBSOCKET_HOST = "openws.work.weixin.qq.com"

# 入站媒体安全上限（与 QQ 频道默认一致）
WECOM_DOWNLOAD_MAX_BYTES = 1024 * 1024 * 200  # 200MB

# 媒体落盘目录（可用环境变量覆盖）
WECOM_MEDIA_DIR = os.environ.get("CHANNEL_MEDIA_DIR") or os.path.join(
    "data", "channels", "media"
)

# 替换不安全字符为 "_"，保留中文与常见安全标点
_SAFE_NAME_RE = re.compile(r"[^\w.\-()\[\]（）【】\u4e00-\u9fff]+", re.UNICODE)


def _bypass_system_proxy(host: str) -> None:
    """让 WeCom SDK 的 WebSocket 走直连（websockets 15+ 默认走系统代理）。"""
    for key in ("NO_PROXY", "no_proxy"):
        entries = [e.strip() for e in os.environ.get(key, "").split(",") if e.strip()]
        if host not in entries:
            os.environ[key] = ",".join([*entries, host])


def _sanitize_filename(name: str, fallback: str = "unnamed") -> str:
    """清理文件名，避免路径穿越与异常字符。"""

    def _clean(value: str) -> str:
        value = (value or "").strip()
        value = Path(value).name
        return _SAFE_NAME_RE.sub("_", value).strip("._ ")

    return _clean(name) or _clean(fallback) or "unnamed"


def _get_media_dir() -> Path:
    """返回本渠道媒体目录并确保存在。"""
    media_dir = Path(WECOM_MEDIA_DIR) / "wecom"
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


# 消息类型展示映射
MSG_TYPE_MAP = {
    "image": "[image]",
    "voice": "[voice]",
    "file": "[file]",
    "mixed": "[mixed content]",
}


class WecomChannel(BaseChannel):
    """企业微信 AI 机器人渠道 —— WebSocket 长连接，无需公网 IP。"""

    name = "wecom"
    display_name = "WeCom"

    def __init__(self, config: Any, manager: Any) -> None:
        super().__init__(config, manager)
        self.config: WecomSettings = WecomSettings.coerce(config)
        self._client: Any = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generate_req_id: Callable[[str], str] | None = None
        # 每个 chat 缓存最近一条 frame，用于 reply / reply_stream
        self._chat_frames: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """建立 WebSocket 长连接并持续运行直到 stop()"""
        if not WECOM_AVAILABLE:
            self.logger.error("wecom_aibot_sdk 未安装，无法启动企微渠道（pip install -r requirements-channels.txt）")
            self._running = False
            return

        if not self.config.bot_id or not self.config.secret:
            self.logger.error("bot_id / secret 未配置，无法启动企微渠道")
            self._running = False
            return

        # websockets 15+ 默认走系统代理；企微 SDK 不暴露代理参数，要求直连
        _bypass_system_proxy(WECOM_WEBSOCKET_HOST)

        from wecom_aibot_sdk import WSClient, generate_req_id

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._generate_req_id = generate_req_id

        ws_client = cast(Any, WSClient)
        self._client = ws_client(
            {
                "bot_id": self.config.bot_id,
                "secret": self.config.secret,
                "reconnect_interval": 1000,
                "max_reconnect_attempts": -1,  # 无限重连
                "heartbeat_interval": 30000,
            }
        )

        # 注册事件处理器
        self._client.on("connected", self._on_connected)
        self._client.on("authenticated", self._on_authenticated)
        self._client.on("disconnected", self._on_disconnected)
        self._client.on("error", self._on_error)
        self._client.on("message.text", self._on_text_message)
        self._client.on("message.image", self._on_image_message)
        self._client.on("message.voice", self._on_voice_message)
        self._client.on("message.file", self._on_file_message)
        self._client.on("message.mixed", self._on_mixed_message)
        self._client.on("event.enter_chat", self._on_enter_chat)

        self.logger.info("企微 bot 启动（WebSocket 长连接，无需公网 IP）")

        # 连接（SDK 内部负责重连）；其后保持协程存活直至 stop
        await self._client.connect_async()
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """断开连接并结束运行循环。"""
        self._running = False
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                self.logger.exception("企微断开连接异常")
        self.logger.info("企微 bot 已停止")

    # ------------------------------------------------------------------
    # SDK 事件回调
    # ------------------------------------------------------------------

    async def _on_connected(self, frame: Any) -> None:
        self.logger.info("WebSocket 已连接")

    async def _on_authenticated(self, frame: Any) -> None:
        self.logger.info("鉴权成功")

    async def _on_disconnected(self, frame: Any) -> None:
        reason = frame.body if hasattr(frame, "body") else str(frame)
        self.logger.warning("WebSocket 断开：{}", reason)

    async def _on_error(self, frame: Any) -> None:
        self.logger.error("企微错误：{}", frame)

    async def _on_text_message(self, frame: Any) -> None:
        await self._process_message(frame, "text")

    async def _on_image_message(self, frame: Any) -> None:
        await self._process_message(frame, "image")

    async def _on_voice_message(self, frame: Any) -> None:
        await self._process_message(frame, "voice")

    async def _on_file_message(self, frame: Any) -> None:
        await self._process_message(frame, "file")

    async def _on_mixed_message(self, frame: Any) -> None:
        await self._process_message(frame, "mixed")

    async def _on_enter_chat(self, frame: Any) -> None:
        """用户进入会话：可选下发欢迎语。"""
        try:
            if hasattr(frame, "body"):
                body: Any = frame.body or {}
            elif isinstance(frame, dict):
                body = cast(dict[str, Any], frame).get("body", frame)
            else:
                body = {}

            body_dict = cast(dict[str, Any], body) if isinstance(body, dict) else {}
            chat_id = cast(str, body_dict.get("chatid", ""))

            if chat_id and not self.is_allowed(chat_id):
                return

            if chat_id and self.config.welcome_message:
                await self._client.reply_welcome(
                    frame,
                    {
                        "msgtype": "text",
                        "text": {"content": self.config.welcome_message},
                    },
                )
        except Exception:
            self.logger.exception("处理 enter_chat 异常")

    # ------------------------------------------------------------------
    # 入站处理
    # ------------------------------------------------------------------

    async def _process_message(self, frame: Any, msg_type: str) -> None:
        """解析平台消息 → 入站媒体下载/文本化 → 转发内核。"""
        try:
            if hasattr(frame, "body"):
                body: Any = frame.body or {}
            elif isinstance(frame, dict):
                body = cast(dict[str, Any], frame).get("body", frame)
            else:
                body = {}

            if not isinstance(body, dict):
                self.logger.warning("无效的 body 类型：{}", type(cast(object, body)))
                return
            body = cast(dict[str, Any], body)

            # 消息去重
            msg_id = cast(str, body.get("msgid", ""))
            if not msg_id:
                msg_id = f"{body.get('chatid', '')}_{body.get('sendertime', '')}"

            from_info = body.get("from", {})
            sender_id = (
                cast(str, cast(dict[str, Any], from_info).get("userid", "unknown"))
                if isinstance(from_info, dict)
                else "unknown"
            )

            # 鉴权（白名单）。未通过直接丢弃，避免下载/转发开销。
            if not self.is_allowed(sender_id):
                self.logger.debug("发送者不在白名单，忽略：{}", sender_id)
                return

            if msg_id in self._processed_message_ids:
                return
            self._processed_message_ids[msg_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            chat_type = cast(str, body.get("chattype", "single"))
            chat_id = cast(str, body.get("chatid", sender_id))

            content_parts: list[str] = []
            media_paths: list[str] = []

            if msg_type == "text":
                text_info = cast(dict[str, Any], body.get("text", {}))
                text = cast(str, text_info.get("content", ""))
                if text:
                    content_parts.append(text)

            elif msg_type == "image":
                image_info = cast(dict[str, Any], body.get("image", {}))
                file_url = cast(str, image_info.get("url", ""))
                aes_key = cast(str, image_info.get("aeskey", ""))
                if file_url and aes_key:
                    file_path = await self._download_and_save_media(file_url, aes_key, "image")
                    if file_path:
                        content_parts.append(f"[image: {os.path.basename(file_path)}]")
                        media_paths.append(file_path)
                    else:
                        content_parts.append("[image: 下载失败]")
                else:
                    content_parts.append("[image: 下载失败]")

            elif msg_type == "voice":
                voice_info = cast(dict[str, Any], body.get("voice", {}))
                voice_content = cast(str, voice_info.get("content", ""))
                if voice_content:
                    content_parts.append(f"[voice] {voice_content}")
                else:
                    content_parts.append("[voice]")

            elif msg_type == "file":
                file_info = cast(dict[str, Any], body.get("file", {}))
                file_url = cast(str, file_info.get("url", ""))
                aes_key = cast(str, file_info.get("aeskey", ""))
                file_name = cast(str | None, file_info.get("name") or None)
                if file_url and aes_key:
                    file_path = await self._download_and_save_media(
                        file_url, aes_key, "file", file_name
                    )
                    if file_path:
                        content_parts.append(f"[file: {os.path.basename(file_path)}]")
                        media_paths.append(file_path)
                    else:
                        content_parts.append(f"[file: {file_name or 'unknown'}: 下载失败]")
                else:
                    content_parts.append(f"[file: {file_name or 'unknown'}: 下载失败]")

            elif msg_type == "mixed":
                mixed_info = cast(dict[str, Any], body.get("mixed", {}))
                msg_items = cast(list[Any], mixed_info.get("msg_item", []))
                for raw_item in msg_items:
                    item = cast(dict[str, Any], raw_item)
                    item_type = cast(str, item.get("msgtype", ""))
                    if item_type == "text":
                        text_info = cast(dict[str, Any], item.get("text", {}))
                        text = cast(str, text_info.get("content", ""))
                        if text:
                            content_parts.append(text)
                    elif item_type == "image":
                        image_info = cast(dict[str, Any], item.get("image", {}))
                        file_url = cast(str, image_info.get("url", ""))
                        aes_key = cast(str, image_info.get("aeskey", ""))
                        if file_url and aes_key:
                            file_path = await self._download_and_save_media(file_url, aes_key, "image")
                            if file_path:
                                content_parts.append(f"[image: {os.path.basename(file_path)}]")
                                media_paths.append(file_path)
                    else:
                        content_parts.append(MSG_TYPE_MAP.get(item_type, f"[{item_type}]"))

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            content = "\n".join(content_parts) if content_parts else ""
            if not content:
                return

            # 缓存该 chat 的 frame，供出站 reply / reply_stream 使用
            self._chat_frames[chat_id] = frame

            # 归属信息取自 manager 注入的 record（渠道记录 owner）
            rec = getattr(self, "_record", None)
            channel_id = getattr(rec, "id", None) or "wecom"
            owner_user_id = getattr(rec, "user_id", None) or "unknown"

            await self.handle_inbound(
                InboundMessage(
                    channel="wecom",
                    channel_id=channel_id,
                    user_id=owner_user_id,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    content=content,
                    media=media_paths or None,
                    metadata={
                        "message_id": msg_id,
                        "msg_type": msg_type,
                        "chat_type": chat_type,
                    },
                )
            )
        except Exception:
            self.logger.exception("处理消息异常")

    async def _download_and_save_media(
        self,
        file_url: str,
        aes_key: str,
        media_type: str,
        filename: str | None = None,
    ) -> str | None:
        """下载并解密企微媒体，落本地。失败返回 None。"""
        try:
            data, fname = await self._client.download_file(file_url, aes_key)
            if not data:
                self.logger.warning("媒体下载失败")
                return None

            if len(data) > WECOM_DOWNLOAD_MAX_BYTES:
                self.logger.warning(
                    "入站媒体过大：{} bytes（上限 {}）", len(data), WECOM_DOWNLOAD_MAX_BYTES
                )
                return None

            media_dir = _get_media_dir()
            fallback_name = fname or f"{media_type}_{hash(file_url) % 100000}"
            filename = _sanitize_filename(cast(str, filename or fallback_name), fallback=fallback_name)

            file_path = media_dir / filename
            await asyncio.to_thread(file_path.write_bytes, data)
            self.logger.debug("已下载 {} → {}", media_type, file_path)
            return str(file_path)
        except Exception:
            self.logger.exception("下载媒体异常")
            return None

    # ------------------------------------------------------------------
    # 出站
    # ------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        """一次性发送（含媒体上传；兜底路径）。异常上抛交由 manager 重试。"""
        if not self._client:
            raise RuntimeError("企微客户端未初始化")

        try:
            content = (msg.content or "").strip()

            frame = self._chat_frames.get(msg.chat_id)

            # 媒体先经 WS 上传，再 reply / 主动 send_message
            for file_path in msg.media or []:
                if not os.path.isfile(file_path):
                    self.logger.warning("媒体文件不存在：{}", file_path)
                    continue
                try:
                    upload = await self._client.upload_media(file_path)
                except Exception:
                    self.logger.exception("媒体上传失败：{}", file_path)
                    content += f"\n[文件上传失败: {os.path.basename(file_path)}]"
                    continue

                media_type = upload.media_type
                media_body = {
                    "msgtype": media_type,
                    media_type: {"media_id": upload.media_id},
                }
                if frame:
                    await self._client.reply(frame, media_body)
                else:
                    await self._client.send_message(msg.chat_id, media_body)
                self.logger.debug("已发送 {} → {}", media_type, msg.chat_id)

            if not content:
                return

            if frame:
                # 原生流式路径：用 SDK 生成 stream_id，finish=True 收尾
                generate_req_id = self._generate_req_id
                if generate_req_id is None:
                    raise RuntimeError("企微请求 ID 生成器未初始化")
                stream_id = generate_req_id("stream")
                await self._client.reply_stream(frame, stream_id, content, finish=True)
                self.logger.debug("消息已发送 → {}", msg.chat_id)
            else:
                # 无 frame（如主动推送）：仅支持 markdown
                await self._client.send_message(
                    msg.chat_id,
                    {"msgtype": "markdown", "markdown": {"content": content}},
                )
                self.logger.info("主动推送 → {}", msg.chat_id)
        except Exception:
            self.logger.exception("发送消息异常（chat_id={}）", msg.chat_id)
            raise

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        *,
        stream_id: str,
        stream_end: bool = False,
    ) -> None:
        """原生流式发送（覆盖基类默认）。

        企微 AI Bot 为「替换」语义：每次发送累积全文，气泡随增长。manager 已
        在每次 text_delta 时传入累积 full_text，本方法直接 reply_stream。
        """
        if not self._client:
            raise RuntimeError("企微客户端未初始化")
        if not delta and not stream_end:
            return

        frame = self._chat_frames.get(chat_id)
        if frame is None:
            # 无上下文帧（异常态）：仅在收尾时兜底 markdown 下发
            if stream_end and delta:
                await self._client.send_message(
                    chat_id,
                    {"msgtype": "markdown", "markdown": {"content": delta}},
                )
            return

        try:
            await self._client.reply_stream(frame, stream_id, delta, finish=stream_end)
        except Exception:
            self.logger.exception("流式发送异常（chat_id={}）", chat_id)
            raise
