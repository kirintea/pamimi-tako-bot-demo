# -*- coding: utf-8 -*-

"""总入口 — 一键运行所有健康检查

可单独执行: python health_check/check_all.py
"""

from __future__ import annotations

import sys
import os

# 确保 health_check/ 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_http import run as check_http
from check_redis import run as check_redis
from check_postgres import run as check_postgres
from check_mysql import run as check_mysql
from check_llm import run as check_llm
from utils import load_config


def _check_database():
    """按配置的 URL scheme 分派 PostgreSQL / MySQL 检查"""
    try:
        cfg = load_config()
        backend = getattr(cfg.database, "backend", "auto") if cfg else "auto"
        url = (getattr(cfg.database, "url", "") or "") if cfg else ""
        if backend == "mysql" or (
            backend == "auto" and url.startswith(("mysql://", "mariadb://"))
        ):
            return check_mysql()
        if backend == "postgres":
            return check_postgres()
        return check_postgres()
    except Exception:  # noqa: BLE001 — 配置加载失败时回退到 PG 检查
        return check_postgres()


def main():
    print("=" * 50)
    print(" AgentScope Platform Server - Health Check")
    print("=" * 50)

    results = []

    print("\n[1] HTTP + API")
    results.append(check_http())

    print("\n[2] Redis")
    results.append(check_redis())

    print("\n[3] 数据库")
    results.append(_check_database())

    print("\n[4] LLM API")
    results.append(check_llm())

    # 汇总
    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 50)
    color = "\033[32m" if passed == total else "\033[31m"
    print(f" 组件检查: {color}{passed}/{total}\033[0m 通过")
    print("=" * 50)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
