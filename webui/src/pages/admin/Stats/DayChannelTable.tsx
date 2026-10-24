/**
 * 明细表 · 按天×按渠道 —— series_by_channel 展开
 *
 * 渠道列带图标 + 显示名（useChannelLabel，中文 wecom → 企业微信）。
 * 浅色表头 + 交替行底色。
 */

import type { StatsBucketChannel } from '@/api/stats';

import { ChannelBrandLogo } from '@/components/ChannelBrandLogo';

import { useChannelLabel } from './channelLabel';

export function DayChannelTable({ rows }: { rows: StatsBucketChannel[] }) {
	const channelLabel = useChannelLabel();
	return (
		<div className="rounded-xl border border-[#e2e8f0] bg-white p-5">
			<h2 className="mb-3 text-[16px] font-bold text-[#0f172a]">明细数据（按天·按渠道）</h2>
			{rows.length === 0 ? (
				<div className="py-8 text-center text-[13px] text-[#94a3b8]">该区间暂无数据</div>
			) : (
				<table className="w-full text-[13px]">
					<thead>
						<tr className="bg-[#f8fafc] text-left text-[#475569]">
							<th className="rounded-l-lg px-3 py-2.5 font-semibold">日期</th>
							<th className="px-3 py-2.5 font-semibold">渠道</th>
							<th className="px-3 py-2.5 font-semibold">使用人数</th>
							<th className="px-3 py-2.5 font-semibold">活跃会话</th>
							<th className="px-3 py-2.5 font-semibold">新增会话</th>
							<th className="rounded-r-lg px-3 py-2.5 font-semibold">对话轮次</th>
						</tr>
					</thead>
					<tbody>
						{rows.map((row, i) => (
							<tr
								key={`${row.bucket}-${row.channel}`}
								className={i % 2 === 1 ? 'bg-[#f8fafc] text-[#334155]' : 'text-[#334155]'}
							>
								<td className="px-3 py-2.5">{row.bucket.slice(5)}</td>
								<td className="px-3 py-2.5">
									<span className="flex items-center gap-1.5">
										<ChannelBrandLogo type={row.channel} size={14} />
										{channelLabel(row.channel)}
									</span>
								</td>
								<td className="px-3 py-2.5">{row.uv.toLocaleString()}</td>
								<td className="px-3 py-2.5">{row.sessions_active.toLocaleString()}</td>
								<td className="px-3 py-2.5">{row.sessions_new.toLocaleString()}</td>
								<td className="px-3 py-2.5">{row.turns.toLocaleString()}</td>
							</tr>
						))}
					</tbody>
				</table>
			)}
		</div>
	);
}
