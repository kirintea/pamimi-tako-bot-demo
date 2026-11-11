# -*- coding: utf-8 -*-

"""NapCat（OneBot v11）渠道适配器

基于 NapCat 的**正向 WebSocket**（forward WS）：适配器作为客户端主动连接
`ws://host:3001`，收事件与发 action 复用同一条连接，无需公网 IP。

参考同类实现 `channels/napcat/runtime.py`（调研与计划
2026-11-08-napcat-channel-plan.md），按本项目决策改造：
- D1: `websockets` 库 → aiohttp `ws_connect`（根依赖，零新增）
- D2: 覆写 send_delta —— 仅 stream_end 时一次性发送（QQ 无消息编辑语义，
      manager 的占位与累积片全部忽略，防刷屏）
- D3-A: 群聊 InboundMessage.user_id 取群维度（整群一个会话），发送者仅作
      内容前缀；私聊 user_id = 发送者 QQ 号
- D4: 覆写 is_allowed —— allow_from 空名单 = 拒绝全部（公网安全默认）；
      启动硬校验 ws_url / allow_from，不满足置 _error 不连接
- D6（已撤销 2026-11-11）: 原「图片 URL 出入站过 core.security.network SSRF 守卫」
  ——SSRF 移出项目范围，出入站不再做内网校验（残余风险由部署层承接）

改造：
- 继承本项目的 core.channels.base.BaseChannel（manager 注入而非 bus）
- 入站产出 core.channels.events.InboundMessage → self.handle_inbound()
  （is_allowed 闸 + 转发内核）；出站经 _call_action("send_msg") 下发
- D7（本项目相对参考实现的唯一差异）：边界 ID 转换——入站 `group:{id}` →
  `group_{id}`（平台 _ID_RE 不允许冒号），出站转回 `group:{id}`；原生构造与
  出站 partition(":") 解析保持参考实现原样，private:{id} 不转换
- D8: 群聊入站 metadata 附带 `session_title` = `{群名(截断)}-{YYYY-MM-DD}`（日期
  到「日」为止），供平台层按渠道约定落会话标题；群会话 `sender_name` = 群名称
  （平台层已有通路 → users.display_name）

模块拆分：图片出入站（入站下载 + 出站分段，D6）在同包 `napcat_media.py` 的
`NapcatMediaMixin`，本类以 `NapcatChannel(NapcatMediaMixin, BaseChannel)` 组合，
方法体未改。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiohttp
from loguru import logger

from core.channels.adapters.napcat.config import NapcatSettings
from core.channels.adapters.napcat.napcat_media import NapcatMediaMixin
from core.channels.base import BaseChannel
from core.channels.events import InboundMessage, OutboundMessage

_WS_HANDSHAKE_TIMEOUT = 30.0
_ACTION_TIMEOUT = 20.0
_WS_HEARTBEAT = 30.0

# `"mention"`（仅 @我/回复我）| `"open"`（全部）| float p∈[0,1]（概率回复）
GroupPolicy = Literal["mention", "open"] | float


def _id_to_platform(value: str) -> str:
    """入站边界：napcat 原生 `group:{id}` → 平台 `group_{id}`。

    仅前缀 `"group:"` 整体互转为 `"group_"`（禁止裸 ":"↔"_" 全局替换，会误伤
    id 其它位置的分隔符且反向不可逆）；`private:{id}` 与纯数字 id 原样返回。
    """
    return "group_" + value[len("group:") :] if value.startswith("group:") else value


def _id_to_native(value: str) -> str:
    """出站边界：平台 `group_{id}` → napcat 原生 `group:{id}`（交给原 partition 解析）。

    仅前缀 `"group_"` 整体互转回 `"group:"`；其余格式原样返回。
    """
    return "group:" + value[len("group_") :] if value.startswith("group_") else value


# 群会话标题里的群名截断长度：20 字 + 省略号 + "-" + 10 字日期 ≈ 32 字符，
# 既不撑爆侧边栏单行，也远在会话标题列 VARCHAR(256) 之内
_GROUP_TITLE_NAME_MAX = 20


def _truncate_group_name(name: str, limit: int = _GROUP_TITLE_NAME_MAX) -> str:
    """群名截断：超长补省略号，空名回退「群」。"""
    clean = (name or "").strip()
    if not clean:
        return "群"
    return clean if len(clean) <= limit else clean[:limit] + "…"


def build_group_session_title(group_name: str, today: str | None = None) -> str:
    """群聊会话标题：`{群名(截断)}-{YYYY-MM-DD}`（日期到「日」为止）。

    日期取消息当天（缺省用本机时钟，测试可注入 `today` 固定断言）。本方法
    只负责**按条生成**（每条群消息都算一次），是否落库、落库时机（如仅首条
    消息那天写入）由消费 metadata.session_title 的平台层决定。
    """
    day = today or datetime.now().strftime("%Y-%m-%d")
    return f"{_truncate_group_name(group_name)}-{day}"


class NapcatChannel(NapcatMediaMixin, BaseChannel):
    """NapCat / OneBot v11 渠道 —— 正向 WebSocket 客户端"""

    name = "napcat"
    display_name = "NapCat (QQ)"

    def __init__(self, config: Any, manager: Any) -> None:
        super().__init__(config, manager)
        self.config: NapcatSettings = NapcatSettings.coerce(config)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._media_root: Path | None = None  # 测试可注入 tmp_path
        self._self_id: int | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._processed_ids: deque[int] = deque(maxlen=2000)
        self._bot_outbound_ids: deque[int] = deque(maxlen=2000)
        self._background_tasks: set[asyncio.Task[None]] = set()
        # 群名称缓存（group_id → group_name）：群名很少变，进程内缓存避免每条消息查
        self._group_name_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 鉴权（D4：空名单 = 拒绝，覆盖基类「空 = 全放行」的内网默认）
    # ------------------------------------------------------------------

    def is_allowed(self, sender_id: str) -> bool:
        allow = self.config.allow_from or []
        if not allow:
            return False
        if "*" in allow:
            return True
        return str(sender_id) in allow

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.config.ws_url:
            self.logger.error("ws_url 未配置，NapCat 渠道不启动")
            self._error = "ws_url 未配置"
            self._running = False
            return
        if not self.config.allow_from:
            # D4：公网渠道拒绝「空名单 = 全放行」，宁可不起也不静默裸奔
            self.logger.error(
                "allow_from 未配置（安全默认：留空拒绝所有），NapCat 渠道不启动。"
                "请在渠道配置中填写 QQ 号白名单或 * 后重试"
            )
            self._error = "allow_from 未配置（白名单为空）"
            self._running = False
            return

        self._running = True
        self._error = None
        # total=None：全局不限时——下载与握手按调用单独限时，避免长连接 WS 被
        # 会话级 total 超时误杀；trust_env=False：不读系统代理，内网 ws 直连
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None),
            trust_env=False,
        )

        backoff = iter((5, 10))  # 之后固定 30s
        try:
            while self._running:
                try:
                    await self._run_once()
                    backoff = iter((5, 10))  # 干净会话结束后重置退避
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.warning("NapCat 连接中断：{}", e)
                if self._running:
                    await asyncio.sleep(next(backoff, 30))
        finally:
            self._running = False

    async def _run_once(self) -> None:
        """单次连接会话：握手（get_login_info）→ 常驻帧分发；断开向上抛。"""
        assert self._http is not None, "HTTP 会话未初始化"
        headers: dict[str, str] = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"

        self.logger.info("连接 NapCat：{}", self.config.ws_url)
        async with self._http.ws_connect(
            self.config.ws_url,
            headers=headers,
            timeout=_WS_HANDSHAKE_TIMEOUT,
            heartbeat=_WS_HEARTBEAT,
        ) as ws:
            self._ws = ws
            self.logger.info("NapCat WebSocket 已连接")
            try:
                # 握手：等 get_login_info 的 echo 响应。NapCat 可能在响应前夹带
                # meta_event 等帧——非匹配帧照常分发，不能傻等。
                echo = uuid.uuid4().hex
                await ws.send_str(
                    json.dumps(
                        {"action": "get_login_info", "params": {}, "echo": echo},
                        ensure_ascii=False,
                    )
                )
                deadline = time.monotonic() + _ACTION_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("get_login_info 超时")
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        raise ConnectionError("握手期间 NapCat 连接关闭")
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    raw = msg.data
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("echo") == echo:
                        data = payload.get("data")
                        login = data if isinstance(data, dict) else {}
                        self.logger.info(
                            "NapCat 登录：{}（user_id={}）",
                            login.get("nickname"),
                            login.get("user_id"),
                        )
                        break
                    await self._dispatch_frame(raw)

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    await self._dispatch_frame(msg.data)
            finally:
                self._ws = None
                self._fail_pending(RuntimeError("NapCat WebSocket 已断开"))

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:
                pass
            self._http = None
        self._fail_pending(RuntimeError("NapCat 渠道已停止"))
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self.logger.info("NapCat 渠道已停止")

    def _fail_pending(self, err: BaseException) -> None:
        """断开时让所有挂起的 action 调用立刻失败，不悬挂到超时。"""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()

    # ------------------------------------------------------------------
    # 帧分发
    # ------------------------------------------------------------------

    async def _dispatch_frame(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.debug("NapCat 忽略非 JSON 帧")
            return
        if not isinstance(payload, dict):
            return
        frame: dict[str, Any] = payload

        # action 响应：有 echo 且无 post_type
        if "echo" in frame and frame.get("post_type") is None:
            echo = frame.get("echo")
            fut = self._pending.pop(echo, None) if isinstance(echo, str) else None
            if fut and not fut.done():
                fut.set_result(frame)
            return

        if (sid := frame.get("self_id")) is not None:
            try:
                self._self_id = int(sid)
            except (TypeError, ValueError):
                pass

        post_type = frame.get("post_type")
        if post_type == "message":
            self._create_background_task(self._on_message(frame), "message")
        elif post_type == "notice":
            self._create_background_task(self._on_notice(frame), "notice")

    def _create_background_task(self, coro: Any, kind: str) -> None:
        """事件处理器跑在后台 task：单个失败只记日志，不炸分发循环。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.warning("NapCat {} 处理失败：{}", kind, e)

        task.add_done_callback(_done)

    # ------------------------------------------------------------------
    # 入站：消息
    # ------------------------------------------------------------------

    async def _on_message(self, ev: dict[str, Any]) -> None:
        msg_id = ev.get("message_id")
        if isinstance(msg_id, int):
            if msg_id in self._processed_ids:
                return
            self._processed_ids.append(msg_id)

        message_type = ev.get("message_type")
        user_id = ev.get("user_id")
        if user_id is None or message_type not in ("group", "private"):
            return

        segments = self._normalize_segments(ev.get("message"))
        text, images, mentioned_self, reply_to_id = self._parse_segments(segments)

        sender_raw = ev.get("sender")
        sender = sender_raw if isinstance(sender_raw, dict) else {}
        nickname = sender.get("card") or sender.get("nickname")

        is_group = message_type == "group"
        group_id: Any = None
        if is_group:
            group_id = ev.get("group_id")
            if group_id is None:
                return
            replying_to_bot = (
                isinstance(reply_to_id, int) and reply_to_id in self._bot_outbound_ids
            )
            # 群策略先于白名单/下载：不回的群消息不做任何下载开销
            if not self._should_reply_in_group(
                group_id=group_id,
                mentioned_self=mentioned_self,
                replying_to_bot=replying_to_bot,
            ):
                return

        # D4 前置白名单检查：不放行的发送者不下载媒体、不进内核
        # （base.handle_inbound 还会再查一次，此处仅为省下载与 action 开销）
        if not self.is_allowed(str(user_id)):
            self.logger.debug("sender 不在白名单，忽略：{}", user_id)
            return

        media_paths: list[str] = []
        for info in images:
            if local := await self._download_image(info):
                media_paths.append(local)

        if is_group:
            chat_id = f"group:{group_id}"
            # D3-A：会话归属群维度（整群共享一个 session），发送者只作内容前缀
            session_user_id = f"group:{group_id}"
            content = self._format_group_content(
                text=text,
                nickname=nickname,
                user_id=user_id,
            )
            # 群会话 display_name = 群名称（非首个发言人昵称）；nickname 仅进内容前缀
            sender_name = await self._lookup_group_name(group_id)
            # 群会话标题固定格式：{群名(截断)}-{日期}（日期到日为止）
            session_title = build_group_session_title(sender_name)
        else:
            chat_id = f"private:{user_id}"
            session_user_id = str(user_id)
            content = text
            sender_name = nickname
            # 私聊不指定标题：沿用「首条消息前 30 字」的默认规则
            session_title = None

        if not content and not media_paths:
            return

        await self._emit_inbound(
            InboundMessage(
                channel=self.name,
                channel_id=self._channel_id(),
                user_id=session_user_id,
                chat_id=chat_id,
                sender_id=str(user_id),
                content=content,
                media=media_paths or None,
                metadata={
                    "message_id": msg_id,
                    "is_group": is_group,
                    "chat_type": "group" if is_group else "private",
                    "nickname": nickname,
                    "sender_name": sender_name,
                    "session_title": session_title,
                    "reply_to": reply_to_id,
                },
            )
        )

    async def _emit_inbound(self, msg: InboundMessage) -> None:
        """入站唯一出口：先做边界 ID 转换（group: → group_），再交内核。

        所有入站事件（消息/通知/未来新增）必须走此处，防止遗漏转换。
        """
        msg.user_id = _id_to_platform(msg.user_id)
        msg.chat_id = _id_to_platform(msg.chat_id)
        await self.handle_inbound(msg)

    def _channel_id(self) -> str:
        """渠道条目 id（manager 启动时经 _record 注入；缺省回退类型名）。"""
        rec = getattr(self, "_record", None)
        return str(getattr(rec, "id", None) or self.name)

    @staticmethod
    def _normalize_segments(message: Any) -> list[dict[str, Any]]:
        # NapCat 默认输出数组段格式。字符串整条当单个 text 段——刻意不解析
        # CQ 码（那条路径脆弱，且可配置 NapCat 输出数组）。
        if isinstance(message, list):
            return [seg for seg in message if isinstance(seg, dict)]
        if isinstance(message, str) and message:
            return [{"type": "text", "data": {"text": message}}]
        return []

    def _parse_segments(
        self, segments: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]], bool, int | None]:
        parts: list[str] = []
        images: list[dict[str, Any]] = []
        mentioned_self = False
        reply_to: int | None = None
        self_id_str = str(self._self_id) if self._self_id is not None else None

        for seg in segments:
            stype = seg.get("type")
            raw_data = seg.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
            if stype == "text":
                if txt := data.get("text"):
                    parts.append(str(txt))
            elif stype == "image":
                # OneBot 暴露可下载的 url；NapCat 另有 file（如 <md5>.png）
                # 与 file_size（字节，有时是字符串）
                url = data.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    images.append(
                        {
                            "url": url,
                            "file": data.get("file"),
                            "file_size": data.get("file_size"),
                        }
                    )
                else:
                    self.logger.warning("NapCat 图片 url 非法：{}", url)
            elif stype == "at":
                qq = str(data.get("qq", ""))
                if self_id_str and qq == self_id_str:
                    mentioned_self = True
                else:
                    parts.append(f"@{qq}")
            elif stype == "reply":
                rid = data.get("id")
                try:
                    reply_to = int(rid) if rid is not None else None
                except (TypeError, ValueError):
                    pass
            elif stype == "face":
                parts.append(f"[face:{data.get('id', '')}]")

        text = " ".join(p.strip() for p in parts if p.strip()).strip()
        return text, images, mentioned_self, reply_to

    def _should_reply_in_group(
        self, *, group_id: Any, mentioned_self: bool, replying_to_bot: bool
    ) -> bool:
        """@我 或 回复我的消息 恒触发；否则按群策略（mention/open/概率）。"""
        if mentioned_self or replying_to_bot:
            return True
        policy: str | float = self.config.group_policy_overrides.get(
            str(group_id), self.config.group_policy
        )
        if policy == "open":
            return True
        if policy == "mention":
            return False
        try:
            p = float(policy)
        except (TypeError, ValueError):
            return False
        return random.random() < p

    @staticmethod
    def _format_group_content(*, text: str, nickname: Any, user_id: Any) -> str:
        """群聊 user message 包装：`昵称(QQ): text`。

        QQ 号是角色识别的唯一依据（昵称会变、可重名，不作身份）；昵称仅作展示，
        缺失时回退裸 QQ 号。整群共享会话（D3-A），LLM 靠此前缀区分发言人。
        """
        if nickname:
            return f"{nickname}({user_id}): {text}"
        return f"{user_id}: {text}"

    # ------------------------------------------------------------------
    # 入站：群通知（新成员迎新）
    # ------------------------------------------------------------------

    async def _on_notice(self, ev: dict[str, Any]) -> None:
        if ev.get("notice_type") != "group_increase" or not self.config.welcome_new_members:
            return

        group_id = ev.get("group_id")
        user_id = ev.get("user_id")
        if group_id is None or user_id is None:
            return

        try:
            group_id_int = int(group_id)
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            self.logger.warning(
                "NapCat 群成员事件 id 非法 group_id={} user_id={}", group_id, user_id
            )
            return

        # 与普通消息同一信任模型（D4）：新成员不在白名单则不迎新
        if not self.is_allowed(str(user_id_int)):
            return

        nickname = await self._lookup_member_name(group_id_int, user_id_int)
        # 群会话 display_name = 群名称（新成员昵称只进事件内容）
        group_name = await self._lookup_group_name(group_id_int)

        await self._emit_inbound(
            InboundMessage(
                channel=self.name,
                channel_id=self._channel_id(),
                user_id=f"group:{group_id_int}",
                chat_id=f"group:{group_id_int}",
                sender_id=str(user_id_int),
                content=f"[群事件] 新成员 {nickname} 加入群 {group_name}",
                metadata={
                    "is_group": True,
                    "chat_type": "group",
                    "event": "group_increase",
                    "nickname": nickname,
                    "sender_name": group_name,
                    "session_title": build_group_session_title(group_name),
                },
            )
        )

    async def _lookup_member_name(self, group_id: int, user_id: int) -> str:
        """查询群成员昵称/群名片，失败回退裸 QQ 号。"""
        try:
            resp = await self._call_action(
                "get_group_member_info",
                {"group_id": group_id, "user_id": user_id, "no_cache": True},
            )
            data = resp.get("data")
            data = data if isinstance(data, dict) else {}
            return data.get("card") or data.get("nickname") or str(user_id)
        except Exception as e:
            self.logger.warning("NapCat get_group_member_info 失败：{}", e)
            return str(user_id)

    async def _lookup_group_name(self, group_id: Any) -> str:
        """查询群名称（OneBot `get_group_info`），进程内缓存，失败回退 `群{id}`。

        群会话的 `sender_name`（→ `users.display_name`）应是群名称，
        不能用首个发言人的昵称（昵称会变、可重名，且整群共享一个用户记录）。
        同一返回值也作为 metadata.session_title 的群名部分。
        """
        key = str(group_id)
        cached = self._group_name_cache.get(key)
        if cached:
            return cached
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            return f"群{key}"
        try:
            resp = await self._call_action(
                "get_group_info",
                {"group_id": gid},
            )
            data = resp.get("data")
            data = data if isinstance(data, dict) else {}
            name = str(data.get("group_name") or "").strip()
        except Exception as e:
            self.logger.warning("NapCat get_group_info 失败：{}", e)
            name = ""
        if not name:
            return f"群{gid}"  # 不缓存失败结果，下条消息重试
        self._group_name_cache[key] = name
        return name

    # ------------------------------------------------------------------
    # 出站
    # ------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        if self._ws is None:
            raise RuntimeError("NapCat 未连接")

        # 出站边界：平台 group_{id} → 原生 group:{id}，其后 partition 解析与参考实现一致
        chat_id = _id_to_native(msg.chat_id)
        kind, _, target = chat_id.partition(":")
        if kind not in ("private", "group") or not target:
            self.logger.error("NapCat chat_id 非法：{}", msg.chat_id)
            return
        try:
            target_id = int(target)
        except (TypeError, ValueError):
            # 如 group_abc → group:abc：target 非数字，记错误不发请求
            self.logger.error("NapCat chat_id 非法：{}", msg.chat_id)
            return

        segments: list[dict[str, Any]] = []
        for ref in msg.media or []:
            if seg := await self._build_image_segment(ref):
                segments.append(seg)
        if text := (msg.content or "").strip():
            segments.append({"type": "text", "data": {"text": text}})
        if not segments:
            return

        params: dict[str, Any] = {"message": segments}
        if kind == "group":
            params["message_type"] = "group"
            params["group_id"] = target_id
        else:
            params["message_type"] = "private"
            params["user_id"] = target_id

        resp = await self._call_action("send_msg", params)
        data = resp.get("data")
        data = data if isinstance(data, dict) else {}
        if (mid := data.get("message_id")) is not None:
            try:
                # 记账：入站 reply 段命中该集合 = 「回复机器人消息」→ 恒触发
                self._bot_outbound_ids.append(int(mid))
            except (TypeError, ValueError):
                pass

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        *,
        stream_id: str,
        stream_end: bool = False,
    ) -> None:
        """D2：仅在 stream_end 一次性发送最终全文。

        QQ 消息无「编辑气泡」语义；manager 投递节奏为占位「…」→ 每片累积
        full_text（均 stream_end=False）→ 收尾片（=全量文本）。非 end 片
        全部忽略，避免把占位与每片增长文本逐条刷进 QQ。命令回执 / 错误提示 /
        会话 ID 推送天然 stream_end=True，单次直达。
        """
        if not stream_end:
            return
        if not (delta or "").strip():
            return
        await self.send(
            OutboundMessage(
                channel=self.name,
                chat_id=chat_id,
                content=delta,
                stream_id=stream_id,
                stream_end=True,
            )
        )

    async def _call_action(
        self,
        action: str,
        params: dict[str, Any],
        timeout: float = _ACTION_TIMEOUT,
    ) -> dict[str, Any]:
        """经同一条 WS 调用 OneBot action：uuid echo 关联响应 + 超时 + retcode 校验。"""
        if self._ws is None:
            raise RuntimeError("NapCat 未连接")
        echo = uuid.uuid4().hex
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await self._ws.send_str(
                json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False)
            )
            resp = await asyncio.wait_for(fut, timeout=timeout)
            status = resp.get("status")
            retcode = resp.get("retcode")
            if (status and status != "ok") or (retcode not in (None, 0)):
                raise RuntimeError(
                    f"NapCat action {action} 失败 status={status!r} retcode={retcode!r}"
                )
            return resp
        finally:
            self._pending.pop(echo, None)
