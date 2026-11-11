# -*- coding: utf-8 -*-

"""NapCat 媒体子模块 —— 入站图片下载（限额）与出站图片分段

自 runtime.py 拆出：这两段逻辑只依赖宿主的
`_http` / `config` / `_media_root` / `logger` 四个属性，与 WebSocket 生命周期、
帧分发互不相干，故以 Mixin 形式与 `NapcatChannel` 组合，方法体与拆分前
逐行一致，行为不变。

- 入站 `_download_image`：声明大小 + 流式分块双重限额 → 清洗文件名后落盘
- 出站 `_build_image_segment`：http(s) 原样转发；本地路径 base64 内联
（原 D6 的 SSRF 预检已撤销——SSRF 移出项目范围，2026-11-11 决策）
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from core.channels.adapters.napcat.config import NapcatSettings

_DOWNLOAD_CHUNK = 64 * 1024
_DOWNLOAD_CALL_TIMEOUT = aiohttp.ClientTimeout(total=60)

# 媒体落盘目录（与 wecom 同款：CHANNEL_MEDIA_DIR 可覆盖）
_MEDIA_ROOT = os.environ.get("CHANNEL_MEDIA_DIR") or os.path.join("data", "channels", "media")
# 替换不安全字符为 "_"，保留中文与常见安全标点
_SAFE_NAME_RE = re.compile(r"[^\w.\-()\[\]（）【】一-鿿]+", re.UNICODE)


def _get_media_dir() -> Path:
    """返回本渠道媒体目录并确保存在。"""
    media_dir = Path(_MEDIA_ROOT) / "napcat"
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def _sanitize_filename(name: str, fallback: str = "image.jpg") -> str:
    """清理文件名，避免路径穿越（取 basename + 字符白名单）。"""
    clean = Path(str(name).strip()).name
    clean = _SAFE_NAME_RE.sub("_", clean).strip("._ ")
    return clean or fallback


class NapcatMediaMixin:
    """NapCat 图片出入站 —— 由 `NapcatChannel(NapcatMediaMixin, BaseChannel)` 继承。

    依赖宿主提供的属性：
    - `_http`：aiohttp 客户端会话（出站直传 URL / 入站下载都复用它）
    - `config`：`NapcatSettings`（取 `max_image_bytes` 限额）
    - `_media_root`：媒体落盘目录（测试注入 `tmp_path`）
    - `logger`：loguru 带渠道上下文的日志器（`BaseChannel` 提供）
    """

    _http: aiohttp.ClientSession | None
    _media_root: Path | None
    config: NapcatSettings

    # ------------------------------------------------------------------
    # 出站：本地路径 / URL → OneBot image 段
    # ------------------------------------------------------------------

    async def _build_image_segment(self, ref: str) -> dict[str, Any] | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref.startswith(("http://", "https://")):
            return {"type": "image", "data": {"file": ref}}
        # 本地路径 → base64 内联：NapCat 在别的主机/容器也能发
        path = Path(os.path.expanduser(ref)).resolve()
        if not path.is_file():
            self.logger.warning("NapCat 本地图片不存在：{}", path)
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return {"type": "image", "data": {"file": "base64://" + base64.b64encode(data).decode()}}

    # ------------------------------------------------------------------
    # 入站：图片下载（限额）
    # ------------------------------------------------------------------

    async def _download_image(self, info: dict[str, Any]) -> str | None:
        url = info.get("url")
        if not isinstance(url, str) or self._http is None:
            return None

        max_bytes = self.config.max_image_bytes
        # NapCat 声明的大小超限 → 直接拒（省下载）
        try:
            declared_size = int(info["file_size"])
            if declared_size > max_bytes:
                self.logger.warning(
                    "NapCat 图片声明大小 {} 超过上限 {}：{}", declared_size, max_bytes, url
                )
                return None
        except (TypeError, KeyError, ValueError):
            pass

        try:
            # allow_redirects=False：3xx 一律丢弃，不跟随跳转
            async with self._http.get(
                url, allow_redirects=False, timeout=_DOWNLOAD_CALL_TIMEOUT
            ) as resp:
                if 300 <= resp.status < 400:
                    self.logger.warning("NapCat 图片下载拒绝重定向：{}", url)
                    return None
                if resp.status >= 400:
                    self.logger.warning("NapCat 图片下载失败 status={}：{}", resp.status, url)
                    return None
                # 流式分块读，超限即断——不能用 read(max+1)（chunked 响应会截断）
                buf = bytearray()
                truncated = False
                async for chunk in resp.content.iter_chunked(_DOWNLOAD_CHUNK):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        truncated = True
                        break
                if truncated:
                    self.logger.warning("NapCat 图片超过上限 {}：{}", max_bytes, url)
                    return None
                data = bytes(buf)
        except Exception as e:
            self.logger.warning("NapCat 图片下载异常 {}：{}", url, e)
            return None

        filename_hint = info.get("file")
        if filename_hint:
            name = _sanitize_filename(str(filename_hint))
        else:
            name = f"{int(time.time() * 1000)}.jpg"
        root = self._media_root if self._media_root is not None else _get_media_dir()
        path = root / name
        try:
            await asyncio.to_thread(path.write_bytes, data)
        except OSError as e:
            self.logger.warning("NapCat 图片落盘失败：{}", e)
            return None
        return str(path)
