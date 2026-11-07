/**
 * 渠道维度对比 —— tab 卡片列表（替代表格）
 *
 * 三 tab（用户数/会话数量/对话轮次）切换占比分母指标；
 * share 前端现算：行指标 ÷ channels 各行该指标合计（uv_share 保留不删，备用）。
 * 渠道图标：品牌 logo（ChannelBrandLogo，无资源时两字母缩写）。
 * 显示名走 useChannelLabel（中文 wecom → 企业微信）。
 */

import { useState } from 'react';

import type { StatsChannelRow } from '@/api/stats';

import { ChannelBrandLogo } from '@/components/ChannelBrandLogo';
import { chart } from '@/lib/themeColors';

import { useChannelLabel } from './channelLabel';

type MetricKey = 'uv' | 'sessions_active' | 'turns';

const TABS: { key: MetricKey; label: string; color: string }[] = [
	{ key: 'uv', label: '用户数', color: chart.uv },
	{ key: 'sessions_active', label: '会话数量', color: chart.sessions },
	{ key: 'turns', label: '对话轮次', color: chart.turns },
];

export function ChannelCards({ rows }: { rows: StatsChannelRow[] }) {
	const channelLabel = useChannelLabel();
	const [tab, setTab] = useState<MetricKey>('uv');
	const active = TABS.find((t) => t.key === tab) ?? TABS[0];
	const total = rows.reduce((sum, r) => sum + r[tab], 0);

	return (
		<div className="rounded-xl border border-stats-border bg-stats-panel p-5">
			<h2 className="mb-3 text-[16px] font-bold text-stats-title">渠道维度对比</h2>
			{rows.length === 0 ? (
				<div className="py-8 text-center text-[13px] text-stats-muted">该区间暂无数据</div>
			) : (
				<>
					<div className="mb-4 flex gap-2">
						{TABS.map((t) => (
							<button
								key={t.key}
								type="button"
								onClick={() => setTab(t.key)}
								className={
									tab === t.key
										? 'rounded-full bg-stats-chip-on px-3.5 py-1.5 text-[13px] text-white'
										: 'rounded-full bg-stats-chip-off px-3.5 py-1.5 text-[13px] text-stats-body hover:bg-stats-chip-off-hover'
								}
							>
								{t.label}
							</button>
						))}
					</div>
					<div className="flex flex-col gap-3">
						{rows.map((r) => {
							const share = total > 0 ? r[tab] / total : 0;
							return (
								<div key={r.channel || 'unknown'} className="rounded-xl border border-stats-border p-4">
									<div className="flex items-center gap-3">
										<ChannelBrandLogo type={r.channel} size={36} />
										<div className="min-w-0">
											<div className="truncate text-[14px] font-semibold text-stats-title">
												{channelLabel(r.channel, r.label)}
											</div>
											<div className="mt-0.5 flex gap-3 text-[12px] text-stats-faint">
												<span>用户 {r.uv.toLocaleString()}</span>
												<span>会话 {r.sessions_active.toLocaleString()}</span>
												<span>轮次 {r.turns.toLocaleString()}</span>
											</div>
										</div>
									</div>
									<div className="mt-3">
										<div className="flex items-center justify-between text-[12px]">
											<span className="text-stats-muted">{active.label}占比</span>
											<span className="font-semibold text-stats-body">
												{Math.round(share * 100)}%
											</span>
										</div>
										<div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-stats-track">
											<div
												className="h-full rounded-full"
												style={{
													width: `${Math.round(share * 100)}%`,
													backgroundColor: active.color,
												}}
											/>
										</div>
									</div>
								</div>
							);
						})}
					</div>
				</>
			)}
		</div>
	);
}
