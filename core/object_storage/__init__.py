# -*- coding: utf-8 -*-

"""对象存储模块 — 抽象基类 + 工厂函数

使用方式：
    from core.object_storage import create_object_storage
    storage = create_object_storage("local", base_dir="./uploads")
"""

from core.object_storage.base import AccessUrl, ObjectStorageBase, UploadCredential

__all__ = [
    "ObjectStorageBase",
    "UploadCredential",
    "AccessUrl",
    "create_object_storage",
]


def create_object_storage(backend: str, **kwargs) -> ObjectStorageBase:
    """工厂函数：根据 backend 名称创建存储实例

    Args:
        backend: 存储后端名称（"local" / "s3" / "aliyun_oss"）
        **kwargs: 传递给具体实现的参数

    Returns:
        ObjectStorageBase 实例
    """
    if backend == "local":
        from core.object_storage.local import LocalStorage

        return LocalStorage(**kwargs)
    elif backend == "s3":
        from core.object_storage.s3 import S3Storage

        return S3Storage(**kwargs)
    elif backend == "aliyun_oss":
        from core.object_storage.aliyun_oss import AliyunOSSStorage

        return AliyunOSSStorage(**kwargs)
    else:
        raise ValueError(f"不支持的存储后端: {backend}")
