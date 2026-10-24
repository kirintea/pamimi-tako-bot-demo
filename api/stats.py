# -*- coding: utf-8 -*-

"""运营数据统计看板 BFF —— root 专属，只读聚合

端点：
- GET /admin/stats            — 概览（totals / prev_totals / series / series_by_channel / channels / channel_options）
- GET /admin/stats/export.csv — 两段式明细 CSV（按天 + 按天×渠道，UTF-8 BOM）

口径见 .draft/2026-10-23-AI 助手运营数据看板前端优化-design.md。只读代理，不写任何数据。
``viewer`` 为查询参数，信任模型与 api/observe.py 一致（demo 阶段的调用者
标识，生产应改由认证 token 解析）。
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from api.chat import _resolve_viewer_role
from core.stats import CHANNEL_ALL, CHANNEL_LABELS, compute_stats, custom_window, range_window

router = APIRouter(prefix="/admin/stats", tags=["stats"])

_CSV_COLUMNS = ["日期", "使用人数", "活跃会话", "新增会话", "对话轮次"]
_CSV_BC_COLUMNS = ["日期", "渠道", "使用人数", "活跃会话", "新增会话", "对话轮次"]


async def _require_root_and_db(request: Request, viewer: str | None):
    """root 门控 + 数据库可用性检查（范式同 api/observe.py）"""
    role = await _resolve_viewer_role(request, viewer)
    if role != "root":
        raise HTTPException(status_code=403, detail="需要 root 角色")
    db = getattr(request.app.state, "database_manager", None)
    if not db or not db.is_initialized:
        raise HTTPException(status_code=503, detail="数据库未配置")
    return db


def _resolve_tz(db) -> str:
    """取 database.timezone（与 DatabaseManager.now() 同源），未配置时回落 Asia/Shanghai"""
    cfg = getattr(db, "_config", None)
    return getattr(cfg, "timezone", None) or "Asia/Shanghai"


def _resolve_window(
    tz: str, stat_range: str | None, start: date | None, end: date | None,
) -> tuple[Any, Any, str]:
    """range 预设与 start/end 自定义二选一，返回 (start, end, range_key)"""
    if stat_range is not None and (start is not None or end is not None):
        raise HTTPException(status_code=422, detail="range 与 start/end 二选一，不可同时提供")
    if (start is None) != (end is None):
        raise HTTPException(status_code=422, detail="start/end 必须成对提供")
    if start is not None and end is not None:
        try:
            s, e = custom_window(tz, start, end)
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err))
        return s, e, "custom"
    try:
        s, e = range_window(tz, stat_range or "30d")
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    return s, e, stat_range or "30d"


async def _load(
    request: Request,
    viewer: str | None,
    stat_range: str | None,
    start: date | None,
    end: date | None,
    granularity: str,
    channel: str | None,
) -> dict[str, Any]:
    db = await _require_root_and_db(request, viewer)
    tz = _resolve_tz(db)
    s, e, range_key = _resolve_window(tz, stat_range, start, end)
    return await compute_stats(
        db,
        start=s,
        end=e,
        tz=tz,
        granularity=granularity,
        channel=channel,
        range_key=range_key,
    )


@router.get("")
async def stats_overview(
    request: Request,
    viewer: str | None = None,
    stat_range: str | None = Query(None, alias="range", pattern="^(7d|30d|90d)$"),
    start: date | None = Query(None),
    end: date | None = Query(None),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    channel: str | None = Query(None, max_length=64),
) -> dict[str, Any]:
    """看板概览（root 专属）

    Args:
        viewer: 调用者身份（查询参数，同现有约定；缺省无角色 → 403）
        stat_range: 时间窗 7d / 30d / 90d（URL 参数名 range；与 start/end 互斥）
        start/end: 自定义区间 YYYY-MM-DD（含首尾，1–366 天；与 range 互斥）
        granularity: 分桶粒度 day / week / month（缺省 day，前端已锁按天）
        channel: 渠道过滤，缺省 / 'all' = 全部
    """
    return await _load(request, viewer, stat_range, start, end, granularity, channel)


def _span_tag(stat_range: str | None, start: date | None, end: date | None) -> str:
    if start is not None and end is not None:
        return f"custom-{start.isoformat()}-{end.isoformat()}"
    return stat_range or "30d"


@router.get("/export.csv")
async def stats_export_csv(
    request: Request,
    viewer: str | None = None,
    stat_range: str | None = Query(None, alias="range", pattern="^(7d|30d|90d)$"),
    start: date | None = Query(None),
    end: date | None = Query(None),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    channel: str | None = Query(None, max_length=64),
) -> Response:
    """两段式明细 CSV 导出（UTF-8 带 BOM，Excel 打开中文不乱码）

    第一段按天汇总，空行后第二段按天×渠道（渠道列取 CHANNEL_LABELS 兜底显示名）。
    """
    data = await _load(request, viewer, stat_range, start, end, granularity, channel)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for row in data["series"]:
        writer.writerow([
            row["bucket"], row["uv"], row["sessions_active"],
            row["sessions_new"], row["turns"],
        ])
    writer.writerow([])
    writer.writerow(_CSV_BC_COLUMNS)
    for row in data["series_by_channel"]:
        label = CHANNEL_LABELS.get(row["channel"], row["channel"])
        writer.writerow([
            row["bucket"], label, row["uv"], row["sessions_active"],
            row["sessions_new"], row["turns"],
        ])

    ch_tag = channel or CHANNEL_ALL
    filename = f"ops-stats-{_span_tag(stat_range, start, end)}-{granularity}-{ch_tag}.csv"
    return Response(
        content=b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
