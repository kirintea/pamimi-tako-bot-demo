# -*- coding: utf-8 -*-

"""本地文件系统存储（开发/测试用）

上传通过服务端代理（前端 POST 文件到后端，后端写磁盘），
访问通过 FastAPI 的 /images/files/{key} 静态路由。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from core.object_storage.base import (
    AccessUrl,
    ObjectStorageBase,
    UploadCredential,
)
from core.validators import coerce_id


class LocalStorage(ObjectStorageBase):
    """本地文件系统存储（开发/测试用）"""

    def __init__(self, base_dir: str = "workspaces/user_spaces") -> None:
        self._base_dir = os.path.abspath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)

    def generate_key(
        self,
        user_id: str,
        content_type: str,
        *,
        prefix: str = "uploads/",
    ) -> str:
        """生成对象 key（用户私有域布局）

        规则：``{user_id}/uploads/{yyyy}/{mm}/{dd}/{uuid}.{ext}``

        将上传文件归入用户私有域（user_spaces/{user_id}/uploads/...），
        与双域权限模型一致；user_id 经 coerce_id 规范化，杜绝路径穿越。
        """
        user_id = coerce_id(user_id)
        ext = content_type.split("/")[-1].replace("jpeg", "jpg")
        now = datetime.now()
        uid = uuid.uuid4().hex[:12]
        return f"{user_id}/{prefix}{now:%Y/%m/%d}/{uid}.{ext}"

    def generate_upload_credential(
        self,
        user_id: str,
        content_type: str,
        size: int,
        *,
        prefix: str = "uploads/",
    ) -> UploadCredential:
        key = self.generate_key(user_id, content_type, prefix=prefix)
        # 本地模式：upload_url 指向后端代理接口
        return UploadCredential(
            upload_url=f"/images/upload/{key}",
            key=key,
            expires_in=0,  # 本地不过期
        )

    def generate_access_url(self, key: str, *, ttl: int = 300) -> AccessUrl:
        return AccessUrl(url=f"/images/files/{key}", expires_in=0)

    async def get_object_bytes(self, key: str) -> bytes:
        path = os.path.join(self._base_dir, key)
        with open(path, "rb") as f:
            return f.read()

    async def put_object(self, key: str, data: bytes, content_type: str) -> None:
        path = os.path.join(self._base_dir, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    async def delete_object(self, key: str) -> None:
        path = os.path.join(self._base_dir, key)
        if os.path.exists(path):
            os.remove(path)
