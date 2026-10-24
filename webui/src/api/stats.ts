/**
 * 运营数据统计看板 API —— root 专属
 *
 * 口径见 .draft/2026-10-23-AI 助手运营数据看板前端优化-design.md
 */

import { ApiError, client } from './client';

export type StatsRange = '7d' | '30d' | '90d';
export type StatsRangeKey = StatsRange | 'custom';
export type StatsGranularity = 'day' | 'week' | 'month';

export interface StatsTotals {
	uv: number;
	sessions_active: number;
	sessions_new: number;
	turns: number;
}

export interface StatsBucket {
	bucket: string;
	uv: number;
	sessions_active: number;
	sessions_new: number;
	turns: number;
}

export interface StatsBucketChannel extends StatsBucket {
	channel: string;
}

export interface StatsChannelRow {
	channel: string;
	label: string;
	uv: number;
	sessions_active: number;
	sessions_new: number;
	turns: number;
	uv_share: number;
}

export interface StatsOverview {
	range: {
		start: string;
		end: string;
		granularity: StatsGranularity;
		range_key: StatsRangeKey;
		channel: string;
		timezone: string;
	};
	totals: StatsTotals;
	prev_totals: StatsTotals;
	series: StatsBucket[];
	series_by_channel: StatsBucketChannel[];
	channels: StatsChannelRow[];
	channel_options: { value: string; label: string }[];
}

export interface StatsQuery {
	range: StatsRange | null; // null = 自定义区间（走 start/end）
	start?: string; // YYYY-MM-DD
	end?: string;
	channel: string; // 'all' 或具体渠道值
}

function qs(q: StatsQuery): Record<string, string> {
	const out: Record<string, string> = { channel: q.channel };
	if (q.range) out.range = q.range;
	else {
		if (q.start) out.start = q.start;
		if (q.end) out.end = q.end;
	}
	return out;
}

function spanTag(q: StatsQuery): string {
	return q.range ? q.range : `custom-${q.start ?? ''}-${q.end ?? ''}`;
}

export const statsApi = {
	overview: (q: StatsQuery, viewer: string) =>
		client.get<StatsOverview>('/admin/stats', { ...qs(q), viewer }),

	/** 触发 CSV 下载（走 client.raw 统一鉴权头，勿改回 <a href> 直连） */
	async exportCsv(q: StatsQuery, viewer: string): Promise<void> {
		const res = await client.raw('/admin/stats/export.csv', { ...qs(q), viewer });
		if (!res.ok) throw new ApiError(res.status, await res.text());
		const blob = await res.blob();
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `ops-stats-${spanTag(q)}-day-${q.channel}.csv`;
		document.body.appendChild(a);
		a.click();
		a.remove();
		URL.revokeObjectURL(url);
	},
};
