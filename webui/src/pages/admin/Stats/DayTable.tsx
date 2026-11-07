/**
 * 明细表 · 按天 —— 趋势图的 table view（CVD 对比度 WARN 的必备补救）
 *
 * 占比列 = 当日轮次 ÷ 区间总轮次（UV 跨日不可加，不用 UV）。
 * 浅色表头 + 交替行底色 + 占比进度条。
 */

import type { StatsBucket } from '@/api/stats';

export function DayTable({ series, totalTurns }: { series: StatsBucket[]; totalTurns: number }) {
	return (
		<div className="rounded-xl border border-stats-border bg-stats-panel p-5">
			<h2 className="mb-3 text-[16px] font-bold text-stats-title">明细数据（按天）</h2>
			{series.length === 0 ? (
				<div className="py-8 text-center text-[13px] text-stats-muted">该区间暂无数据</div>
			) : (
				<table className="w-full text-[13px]">
					<thead>
						<tr className="bg-stats-zebra text-left text-stats-meta">
							<th className="rounded-l-lg px-3 py-2.5 font-semibold">日期</th>
							<th className="px-3 py-2.5 font-semibold">使用人次</th>
							<th className="px-3 py-2.5 font-semibold">活跃会话</th>
							<th className="px-3 py-2.5 font-semibold">新增会话</th>
							<th className="px-3 py-2.5 font-semibold">对话轮次</th>
							<th className="rounded-r-lg px-3 py-2.5 font-semibold">占比</th>
						</tr>
					</thead>
					<tbody>
						{series.map((row, i) => {
							const share = totalTurns > 0 ? row.turns / totalTurns : 0;
							return (
								<tr
									key={row.bucket}
									className={
										i % 2 === 1 ? 'bg-stats-zebra text-stats-body' : 'text-stats-body'
									}
								>
									<td className="px-3 py-2.5">{row.bucket.slice(5)}</td>
									<td className="px-3 py-2.5">{row.uv.toLocaleString()}</td>
									<td className="px-3 py-2.5">{row.sessions_active.toLocaleString()}</td>
									<td className="px-3 py-2.5">{row.sessions_new.toLocaleString()}</td>
									<td className="px-3 py-2.5">{row.turns.toLocaleString()}</td>
									<td className="px-3 py-2.5">
										<div className="flex items-center gap-2">
											<div className="h-1.5 w-16 overflow-hidden rounded-full bg-stats-track">
												<div
													className="h-full rounded-full bg-stats-bar"
													style={{ width: `${Math.round(share * 100)}%` }}
												/>
											</div>
											<span className="text-[12px] text-stats-faint">
												{Math.round(share * 100)}%
											</span>
										</div>
									</td>
								</tr>
							);
						})}
					</tbody>
				</table>
			)}
		</div>
	);
}
