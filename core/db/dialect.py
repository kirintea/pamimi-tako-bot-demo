# -*- coding: utf-8 -*-

"""SQL 方言工具 — 占位符翻译 / 状态串格式化 / 后端类型解析

PostgreSQL 使用 $N 位置占位符，MySQL 使用 %s。
本模块提供两者的单向翻译（$N → %s，按出现顺序重排参数），
使注册表与既有 SQL 可以统一用 $N 书写。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# 匹配 $1 / $10 形式的占位符（$.title 等 JSON 路径的 $ 后是 .，不会命中）
_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def translate_placeholders(sql: str, args: tuple) -> tuple[str, tuple]:
    """将 $N 占位符翻译为 %s，并按占位符出现顺序重排参数

    - 无 $N 占位符时原样返回（幂等）
    - 同一 $N 重复出现时，对应参数在结果中重复
    - JSON 路径（如 '$.title'）不受影响

    Args:
        sql: 含 $N 占位符的 SQL（或已无占位符的通用 SQL）
        args: 与 $N 编号一一对应的参数元组（1-based）

    Returns:
        (翻译后的 SQL, 按出现顺序重排的参数元组)
    """
    matches = _PLACEHOLDER_RE.findall(sql)
    if not matches:
        return sql, args
    ordered = tuple(args[int(m) - 1] for m in matches)
    return _PLACEHOLDER_RE.sub("%s", sql), ordered


def format_status(verb: str, rowcount: int) -> str:
    """生成与 asyncpg 兼容的执行状态串

    asyncpg 的 execute() 返回 "INSERT 0 1" / "UPDATE 3" / "DELETE 1"，
    既有代码以 `result.split()[-1]` 与 `"UPDATE 1" in result` 消费，
    MySQL 侧必须保持同样的形状。
    """
    if verb.upper() == "INSERT":
        return f"INSERT 0 {rowcount}"
    return f"{verb.upper()} {rowcount}"


def resolve_backend(url: str, backend: str = "auto") -> str:
    """解析数据库后端类型（postgres / mysql）

    显式配置优先；backend == "auto" 时按 URL scheme 识别。
    无法识别时抛 ValueError（由 initialize 捕获后 fail-fast，见 Task 7）。
    """
    if backend and backend != "auto":
        if backend not in ("postgres", "mysql"):
            raise ValueError(f"不支持的数据库后端: {backend!r}")
        return backend

    scheme = urlparse(url).scheme.lower()
    if scheme in ("postgresql", "postgres"):
        return "postgres"
    if scheme in ("mysql", "mariadb"):
        return "mysql"
    raise ValueError(
        f"无法从 URL 解析数据库后端 (scheme={scheme!r})，请显式配置 database.backend"
    )
