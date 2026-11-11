/**
 * 筛选条 —— 统计周期（预设 chips + 自定义起止日期）/ 渠道下拉 / 导出报表
 *
 * 粒度切换已砍（锁按天）。渠道选项动态来自 API（conversations.channel 的
 * DISTINCT 值），显示名走 useChannelLabel（wecom → 企业微信）。
 */

import type { ReactNode } from 'react';

import { Download } from 'lucide-react';

import type { StatsQuery, StatsRange } from '@/api/stats';

import { useChannelLabel } from './channelLabel';
import {
	CHANNEL_CATEGORY_ORDER,
	channelCategory,
	useChannelCategoryLabel,
	type ChannelCategoryKey,
} from './channelCategory';

const RANGES: { value: StatsRange; label: string }[] = [
	{ value: '7d', label: '近7天' },
	{ value: '30d', label: '近30天' },
	{ value: '90d', label: '近90天' },
];

interface FilterBarProps {
	query: StatsQuery;
	onChange: (next: StatsQuery) => void;
	channelOptions: { value: string; label: string }[];
	onExport: () => void;
}

function Chip({
	active,
	onClick,
	children,
}: {
	active: boolean;
	onClick: () => void;
	children: ReactNode;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			className={
				active
					? 'rounded-full bg-stats-chip-on px-3.5 py-1.5 text-[13px] text-white'
					: 'rounded-full bg-stats-chip-off px-3.5 py-1.5 text-[13px] text-stats-body hover:bg-stats-chip-off-hover'
			}
		>
			{children}
		</button>
	);
}

function GroupLabel({ children }: { children: ReactNode }) {
	return <span className="text-[13px] font-semibold text-stats-meta">{children}</span>;
}

const INPUT_CLS =
	'rounded-lg border border-stats-border bg-stats-panel px-3 py-1.5 text-[13px] text-stats-body';

export function FilterBar({ query, onChange, channelOptions, onExport }: FilterBarProps) {
	const channelLabel = useChannelLabel();
	const categoryLabel = useChannelCategoryLabel();

	// 按类分组（展示层）：「全部」在组外，其余进 optgroup，模板类在前
	const grouped = new Map<ChannelCategoryKey, { value: string; label: string }[]>();
	for (const c of channelOptions) {
		const cat = channelCategory(c.value);
		const arr = grouped.get(cat);
		if (arr) arr.push(c);
		else grouped.set(cat, [c]);
	}

	return (
		<div className="flex flex-wrap items-center gap-x-5 gap-y-3">
			<div className="flex items-center gap-2">
				<GroupLabel>统计周期</GroupLabel>
				{RANGES.map((r) => (
					<Chip
						key={r.value}
						active={query.range === r.value}
						onClick={() => onChange({ ...query, range: r.value, start: undefined, end: undefined })}
					>
						{r.label}
					</Chip>
				))}
			</div>

			<div className="flex items-center gap-2">
				<GroupLabel>日期范围</GroupLabel>
				<input
					type="date"
					className={INPUT_CLS}
					value={query.start ?? ''}
					max={query.end}
					onChange={(e) =>
						onChange({ ...query, range: null, start: e.target.value || undefined })
					}
				/>
				<span className="text-[13px] text-stats-muted">~</span>
				<input
					type="date"
					className={INPUT_CLS}
					value={query.end ?? ''}
					min={query.start}
					onChange={(e) =>
						onChange({ ...query, range: null, end: e.target.value || undefined })
					}
				/>
			</div>

			<div className="flex items-center gap-2">
				<GroupLabel>渠道</GroupLabel>
				<select
					className={INPUT_CLS}
					value={query.channel}
					onChange={(e) => onChange({ ...query, channel: e.target.value })}
				>
					<option value="all">全部</option>
					{CHANNEL_CATEGORY_ORDER.filter((cat) => grouped.get(cat)?.length).map(
						(cat) => (
							<optgroup key={cat} label={categoryLabel(cat)}>
								{grouped.get(cat)!.map((c) => (
									<option key={c.value} value={c.value}>
										{channelLabel(c.value, c.label)}
									</option>
								))}
							</optgroup>
						),
					)}
				</select>
			</div>

			<button
				type="button"
				onClick={onExport}
				className="ml-auto flex items-center gap-1.5 rounded-lg bg-stats-chip-on px-4 py-2 text-[13px] font-semibold text-white hover:bg-stats-chip-on-hover"
			>
				<Download className="size-3.5" />
				导出报表
			</button>
		</div>
	);
}
