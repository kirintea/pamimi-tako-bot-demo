# -*- coding: utf-8 -*-

"""build_message_with_media — 渠道本地媒体文件 → ContentPart 列表

渠道适配器（企微）把媒体下载到本地路径；内核多模态入口只认
ImageContent(key=...)（对象存储 key）。本函数在两者之间搭桥：
图片上传对象存储并生成 ImageContent，其余文件跳过（保留文本占位）。
"""

import pytest

from core.multimodal import (
    ImageContent,
    TextContent,
    build_message_with_media,
)
from core.object_storage.local import LocalStorage

pytestmark = pytest.mark.asyncio(loop_scope="session")

_PNG = b"\x89PNG\r\n\x1a\n-fake-"


def _storage(tmp_path) -> LocalStorage:
    return LocalStorage(base_dir=str(tmp_path / "oss"))


async def test_image_uploaded_and_keyed(tmp_path):
    """图片文件：上传对象存储、返回 TextContent + ImageContent，bytes 可回读"""
    storage = _storage(tmp_path)
    img = tmp_path / "企业微信截图_17916000603921.png"
    img.write_bytes(_PNG)

    msg = await build_message_with_media(
        "[image: 企业微信截图_17916000603921.png]", [str(img)], storage, "u1",
    )

    assert isinstance(msg, list)
    assert msg[0] == TextContent(text="[image: 企业微信截图_17916000603921.png]")
    assert isinstance(msg[1], ImageContent)
    assert msg[1].key.endswith(".png")
    assert await storage.get_object_bytes(msg[1].key) == _PNG


async def test_non_image_file_skipped(tmp_path):
    """非图片附件不转 ContentPart，无图片时原样返回 str（纯文本零开销路径）"""
    storage = _storage(tmp_path)
    doc = tmp_path / "报表.pdf"
    doc.write_bytes(b"%PDF-fake")

    msg = await build_message_with_media("[file: 报表.pdf]", [str(doc)], storage, "u1")

    assert msg == "[file: 报表.pdf]"


async def test_missing_file_skipped(tmp_path):
    """读不到的文件跳过不炸；一个都没转成时回退 str"""
    storage = _storage(tmp_path)

    msg = await build_message_with_media(
        "[image: ghost.png]", [str(tmp_path / "ghost.png")], storage, "u1",
    )

    assert msg == "[image: ghost.png]"


async def test_no_media_passthrough(tmp_path):
    """media 为空 → 原样返回 content"""
    storage = _storage(tmp_path)

    assert await build_message_with_media("你好", None, storage, "u1") == "你好"
    assert await build_message_with_media("你好", [], storage, "u1") == "你好"


async def test_multiple_images_in_order(tmp_path):
    """多图：按 media 顺序生成多个 ImageContent"""
    storage = _storage(tmp_path)
    a = tmp_path / "a.png"
    b = tmp_path / "b.jpg"
    a.write_bytes(_PNG)
    b.write_bytes(_PNG)

    msg = await build_message_with_media("看两张", [str(a), str(b)], storage, "u1")

    assert isinstance(msg, list)
    assert [p.type for p in msg] == ["text", "image", "image"]
    assert msg[1].key.endswith(".png")
    assert msg[2].key.endswith(".jpg")
