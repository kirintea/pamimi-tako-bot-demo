# -*- coding: utf-8 -*-

"""对象存储抽象基类

所有存储后端（Local / S3 / OSS / R2）实现此接口。
上层代码只依赖此基类，不关心具体实现。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class UploadCredential:
    """上传凭证"""
    upload_url: str            # 上传地址（presigned PUT URL 或直传 endpoint）
    key: str                   # 对象 key
    expires_in: int            # 有效期（秒）
    headers: dict[str, str] | None = None  # 上传时需要的额外 headers


@dataclass
class AccessUrl:
    """访问凭证"""
    url: str                   # 可访问的 URL（presigned GET URL 或公开 URL）
    expires_in: int            # 有效期（秒）


class ObjectStorageBase(ABC):
    """对象存储抽象基类"""

    @abstractmethod
    def generate_upload_credential(
        self,
        user_id: str,
        content_type: str,
        size: int,
        *,
        prefix: str = "images/",
    ) -> UploadCredential:
        """生成上传凭证

        Args:
            user_id: 用户标识（用于 key 路径隔离）
            content_type: MIME 类型（如 image/png）
            size: 文件大小（字节）
            prefix: key 前缀

        Returns:
            UploadCredential（upload_url + key + expires_in）
        """
        ...

    @abstractmethod
    def generate_access_url(self, key: str, *, ttl: int = 300) -> AccessUrl:
        """生成访问 URL（用于历史消息加载时前端展示图片）

        Args:
            key: 对象 key
            ttl: URL 有效期（秒）

        Returns:
            AccessUrl（url + expires_in）
        """
        ...

    @abstractmethod
    async def get_object_bytes(self, key: str) -> bytes:
        """读取对象内容（Agent 构建 DataBlock 时使用）

        Args:
            key: 对象 key

        Returns:
            对象的原始字节
        """
        ...

    @abstractmethod
    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """写入对象（本地存储模式下前端通过后端代理上传）

        Args:
            key: 对象 key
            data: 文件字节
            content_type: MIME 类型
        """
        ...

    @abstractmethod
    async def delete_object(self, key: str) -> None:
        """删除对象

        Args:
            key: 对象 key
        """
        ...

    # ------------------------------------------------------------------
    # 通用工具方法（子类可覆盖）
    # ------------------------------------------------------------------

    def generate_key(
        self,
        user_id: str,
        content_type: str,
        *,
        prefix: str = "images/",
    ) -> str:
        """生成对象 key

        规则：{prefix}{user_id}/{yyyy}/{mm}/{dd}/{uuid}.{ext}
        """
        ext = content_type.split("/")[-1].replace("jpeg", "jpg")
        now = datetime.now()
        uid = uuid.uuid4().hex[:12]
        return f"{prefix}{user_id}/{now:%Y/%m/%d}/{uid}.{ext}"
