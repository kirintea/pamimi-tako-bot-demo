#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""MySQL 健康检查 — 连接 / 版本 / 必需表 / 基本查询

用法:
    python health_check/check_mysql.py
    (或 TEST_MYSQL_URL=... / DATABASE_URL=mysql://... 前缀指定目标库)

结构对齐 check_postgres.py：_run_async 做检查，run 同步包装（返回 bool）。
"""

from __future__ import annotations

import asyncio
import re
import sys

# utils 先导入（内部向 sys.path 插入项目根，之后才能 import core.*）
from utils import CheckReport, load_config

from core.db.ddl import REQUIRED_TABLES
from core.db.mysql_backend import MySQLBackend

MIN_VERSION = (5, 7, 22)


def _parse_version(version_str: str) -> tuple[int, ...] | None:
    """解析 '8.0.36-0ubuntu0.24.04.1' → (8, 0, 36)；失败返回 None"""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version_str or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


async def _run_async() -> bool:
    report = CheckReport()
    import os

    url = os.environ.get("TEST_MYSQL_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        cfg = load_config()
        url = getattr(cfg.database, "url", "") if cfg else ""
    if not url or not url.startswith(("mysql://", "mariadb://")):
        report.add("MySQL 配置", False, error="DATABASE_URL 未配置为 mysql:// URL")
        return report.print_report()

    backend = MySQLBackend(url)
    try:
        await backend.connect()
        report.add("连接", True)

        ver = await backend.fetchval("SELECT VERSION()")
        parsed = _parse_version(str(ver or ""))
        if parsed is None:
            report.add("版本", False, detail=f"无法解析: {ver}")
        elif parsed < MIN_VERSION:
            report.add(
                "版本", False,
                detail=f"{ver} < 最低要求 {'.'.join(map(str, MIN_VERSION))}",
            )
        else:
            report.add("版本", True, detail=str(ver))

        missing = await backend.missing_tables(REQUIRED_TABLES)
        if missing:
            report.add("必需表", False, detail=f"缺失: {missing}")
        else:
            report.add("必需表", True, detail=f"{len(REQUIRED_TABLES)} 张齐全")

        one = await backend.fetchval("SELECT 1")
        report.add("基本查询", one == 1, detail=f"SELECT 1 → {one}")
    except Exception as e:  # noqa: BLE001
        report.add("连接", False, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            await backend.close()
        except Exception:  # noqa: BLE001
            pass

    return report.print_report()


def run() -> bool:
    """运行 MySQL 检查"""
    return asyncio.run(_run_async())


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
