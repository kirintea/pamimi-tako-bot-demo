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
					? 'rounded-full bg-[#2563eb] px-3.5 py-1.5 text-[13px] text-white'
					: 'rounded-full bg-[#f1f5f9] px-3.5 py-1.5 text-[13px] text-[#334155] hover:bg-[#e2e8f0]'
			}
		>
			{children}
		</button>
	);
}

function GroupLabel({ children }: { children: ReactNode }) {
	return <span className="text-[13px] font-semibold text-[#475569]">{children}</span>;
}

const INPUT_CLS =
	'rounded-lg border border-[#e2e8f0] bg-white px-3 py-1.5 text-[13px] text-[#334155]';

export function FilterBar({ query, onChange, channelOptions, onExport }: FilterBarProps) {
	const channelLabel = useChannelLabel();

	const channels = [{ value: 'all', label: '全部' }, ...channelOptions];

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
				<span className="text-[13px] text-[#94a3b8]">~</span>
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
					{channels.map((c) => (
						<option key={c.value} value={c.value}>
							{c.value === 'all' ? c.label : channelLabel(c.value, c.label)}
						</option>
					))}
				</select>
			</div>

			<button
				type="button"
				onClick={onExport}
				className="ml-auto flex items-center gap-1.5 rounded-lg bg-[#2563eb] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[#1d4ed8]"
			>
				<Download className="size-3.5" />
				导出报表
			</button>
		</div>
	);
}
