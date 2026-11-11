# -*- coding: utf-8 -*-

"""按应用凭证纯函数 — 哈希 / 生成 / 校验（缺口 2，2026-11-11 子计划）

- 库内只存 SHA-256 key_hash + 展示用 key_prefix，明文 key 仅创建时回显一次（D1）
- verify_credential 命中返回凭证对象，未命中返回 None，
  DB 异常向上抛——调用方转 503（HTTP）/ 4503（WS），D3
"""

from __future__ import annotations

import hashlib
import secrets


def hash_api_key(key: str) -> str:
    """SHA-256 十六进制——库内只存哈希（D1）。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """生成新 key：ak_ 前缀 + 32 位 urlsafe 随机（总长 ≥16 满足管理面校验）。"""
    return "ak_" + secrets.token_urlsafe(24)


async def verify_credential(db, key: str) -> dict | None:
    """按哈希查活跃凭证。

    命中返回 {id, cred_type, app_id, channel, scopes}；
    未命中返回 None；DB 异常向上抛（调用方转 503/4503，D3）。
    """
    row = await db.fetch_named("get_credential_by_hash", hash_api_key(key))
    if not row:
        return None
    return {
        "id": row["id"],
        "cred_type": row["cred_type"],
        "app_id": row["app_id"],
        "channel": row["channel"],
        "scopes": row["scopes"],
    }


def resolve_channel(request, body_channel: str, logger=None) -> str:
    """D2：凭证存在时强制覆盖 body/query 的 channel（防第三方冒用他人渠道归因）。

    - 无凭证（未带 key / 主 key 旁路）→ 原样返回
    - 有凭证且与 body 不一致 → 以凭证为准并打 INFO 日志
    - 仅作用于被动接入入口（三路对话 + POST /users）；wecom 等主动适配
      渠道进程内直调、不经 HTTP/WS，天然豁免
    """
    cred = request.scope.get("state", {}).get("credential")
    if not cred:
        return body_channel
    if body_channel != cred["channel"] and logger is not None:
        logger.info(
            "channel 由凭证覆盖: body={!r} → {!r}（app_id={}）",
            body_channel, cred["channel"], cred.get("app_id"),
        )
    return cred["channel"]
