/**
 * 运营数据统计看板 —— root 专属
 *
 * 布局：标题 → 筛选行 → KPI 三卡 → 左趋势图/右渠道卡 → 左按天表/右按天×渠道表。
 * 支持预设时间窗（7d/30d/90d）与自定义起止日期（二者互斥，后端 422 兜底）。
 * 非 root 重定向 /chat，与观测台同范式。
 */

import { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { toast } from 'sonner';

import type { StatsOverview, StatsQuery, StatsRangeKey } from '@/api/stats';
import { statsApi } from '@/api/stats';
import { wsManager } from '@/api/ws';
import { useRole } from '@/hooks/useRole';

import { ChannelCards } from './ChannelCards';
import { useChannelLabel } from './channelLabel';
import { DayChannelTable } from './DayChannelTable';
import { DayTable } from './DayTable';
import { FilterBar } from './FilterBar';
import { StatCards } from './StatCards';
import { TrendChart } from './TrendChart';

const RANGE_LABEL: Record<StatsRangeKey, string> = {
	'7d': '近7天',
	'30d': '近30天',
	'90d': '近90天',
	custom: '自定义',
};

/** 自定义区间起止未配齐时勿标「自定义」——画面上还是上一份数据 */
function spanLabel(q: StatsQuery): string {
	if (q.range) return RANGE_LABEL[q.range];
	if (q.start && q.end) return `${q.start} ~ ${q.end}`;
	return '选择日期区间';
}

export function StatsPage() {
	// useRole 只返回 { isRoot, loading }；viewer 走 wsManager（同 Observe/index.tsx）
	const { isRoot, loading: roleLoading } = useRole();
	const channelLabelOf = useChannelLabel();
	const viewer = wsManager.getUserId();
	const [query, setQuery] = useState<StatsQuery>({
		range: '30d',
		channel: 'all',
	});
	const [data, setData] = useState<StatsOverview | null>(null);
	const [loading, setLoading] = useState(false);

	const load = useCallback(async () => {
		if (!viewer) return;
		// 自定义区间必须起止齐全才发请求
		if (query.range === null && !(query.start && query.end)) return;
		setLoading(true);
		try {
			setData(await statsApi.overview(query, viewer));
		} catch (e) {
			toast.error(e instanceof Error ? e.message : '加载运营数据失败');
		} finally {
			setLoading(false);
		}
	}, [query, viewer]);

	useEffect(() => {
		void load();
	}, [load]);

	if (roleLoading) {
		return (
			<div className="flex h-full items-center justify-center bg-[#f1f5f9] text-[13px] text-[#94a3b8]">
				加载中…
			</div>
		);
	}
	if (!isRoot) return <Navigate to="/chat" replace />;

	const channelLabel =
		query.channel === 'all'
			? '全部'
			: channelLabelOf(
					query.channel,
					data?.channel_options.find((c) => c.value === query.channel)?.label,
				);

	return (
		<div className="flex h-full flex-col overflow-y-auto bg-[#f1f5f9]">
			<div className="mx-auto w-full max-w-[1440px] px-8 py-6">
				<h1 className="text-[22px] font-bold text-[#0f172a]">AI 助手运营数据看板</h1>
				<p className="mt-1 text-[13px] text-[#64748b]">
					全面了解 AI 助手的使用情况与运营效果，助力产品持续优化
				</p>

				<div className="mt-5">
					<FilterBar
						query={query}
						onChange={setQuery}
						channelOptions={data?.channel_options ?? []}
						onExport={() => {
							// 起止未配齐时不发请求——否则后端静默回落 30d，导出文件名却带 custom
							if (query.range === null && !(query.start && query.end)) {
								toast.error('请选择完整日期区间');
								return;
							}
							void statsApi
								.exportCsv(query, viewer ?? '')
								.catch((e) =>
									toast.error(e instanceof Error ? e.message : '导出失败'),
								);
						}}
					/>
				</div>
				<div className="mt-2 text-[12px] text-[#94a3b8]">
					统计区间：{spanLabel(query)} · 渠道：{channelLabel}
				</div>

				{loading && !data ? (
					<div className="py-20 text-center text-[13px] text-[#94a3b8]">加载中…</div>
				) : data ? (
					<div className="mt-6 flex flex-col gap-5">
						<StatCards
							totals={data.totals}
							prev={data.prev_totals}
							series={data.series}
							rangeLabel={spanLabel(query)}
						/>
						<div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
							<div className="lg:col-span-2">
								<TrendChart series={data.series} />
							</div>
							<ChannelCards rows={data.channels} />
						</div>
						<div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
							<DayTable series={data.series} totalTurns={data.totals.turns} />
							<DayChannelTable rows={data.series_by_channel} />
						</div>
					</div>
				) : (
					<div className="py-20 text-center text-[13px] text-[#94a3b8]">暂无数据</div>
				)}
			</div>
		</div>
	);
}
