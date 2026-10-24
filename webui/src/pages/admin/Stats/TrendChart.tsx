/**
 * 趋势分析 —— 三系列合并折线（Recharts，单 Y 轴）
 *
 * 蓝=使用人数 / 绿=会话数量 / 橙=对话轮次（固定映射，dataviz 校验器已过；
 * 绿↔橙 CVD ΔE 6.9 落地板带，由图例 + 末端直标 + 明细表次级编码兜底）。
 * 每系列：2px 线 + ≥8px 白环数据点 + 12%→0 垂直渐变淡填充。
 * 悬停：统一竖直 cursor + tooltip 同列展示三指标。仅末端点直标数值。
 * 7d/30d/自定义共用布局；x 轴标签按点数抽稀（minTickGap）。
 */

import {
	Area,
	CartesianGrid,
	ComposedChart,
	LabelList,
	Legend,
	Line,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from 'recharts';

import type { StatsBucket } from '@/api/stats';

const SERIES = [
	{ key: 'uv', name: '使用人数', color: '#2563eb', gradId: 'grad-uv' },
	{ key: 'sessions_active', name: '会话数量', color: '#16a34a', gradId: 'grad-sessions' },
	{ key: 'turns', name: '对话轮次', color: '#ea580c', gradId: 'grad-turns' },
] as const;

type SeriesKey = (typeof SERIES)[number]['key'];

interface ChartRow {
	bucket: string; // ISO 日期，tooltip 标签
	uv: number;
	sessions_active: number;
	turns: number;
}

/** 仅末点渲染的数值直标（LabelList content） */
function EndLabel(props: {
	x?: number;
	y?: number;
	index?: number;
	value?: number;
	total?: number;
}) {
	const { x, y, index, value, total } = props;
	// Number(undefined)=NaN 会让 == null 守卫失效，故用 isFinite 判坐标缺失
	if (index !== (total ?? 0) - 1 || !Number.isFinite(x) || !Number.isFinite(y)) return null;
	return (
		<text x={(x as number) + 8} y={(y as number) + 4} fontSize={11} fill="#475569">
			{value?.toLocaleString()}
		</text>
	);
}

function ChartTooltip({
	active,
	payload,
	label,
}: {
	active?: boolean;
	payload?: Array<{ dataKey?: string | number; value?: number | string }>;
	label?: string;
}) {
	if (!active || !payload?.length) return null;
	return (
		<div className="rounded-lg border border-[#e2e8f0] bg-white px-3.5 py-2.5 shadow-[0_4px_16px_rgba(15,23,42,0.08)]">
			<div className="text-[12px] font-semibold text-[#0f172a]">{label}</div>
			{SERIES.map((s) => {
				const hit = payload.find((p) => p.dataKey === s.key);
				return (
					<div key={s.key} className="mt-1 flex items-center gap-2 text-[12px]">
						<span
							className="inline-block size-2.5 shrink-0 rounded-[2px]"
							style={{ backgroundColor: s.color }}
						/>
						<span className="text-[#475569]">{s.name}</span>
						<span className="ml-auto font-semibold text-[#0f172a]">
							{Number(hit?.value ?? 0).toLocaleString()}
						</span>
					</div>
				);
			})}
		</div>
	);
}

export function TrendChart({ series }: { series: StatsBucket[] }) {
	if (series.length === 0) {
		return (
			<div className="rounded-xl border border-[#e2e8f0] bg-white p-5">
				<h2 className="text-[16px] font-bold text-[#0f172a]">趋势分析</h2>
				<div className="py-10 text-center text-[13px] text-[#94a3b8]">该区间暂无数据</div>
			</div>
		);
	}

	const rows: ChartRow[] = series.map((s) => ({
		bucket: s.bucket,
		uv: s.uv,
		sessions_active: s.sessions_active,
		turns: s.turns,
	}));
	const total = rows.length;

	return (
		<div className="rounded-xl border border-[#e2e8f0] bg-white p-5">
			<h2 className="mb-3 text-[16px] font-bold text-[#0f172a]">趋势分析</h2>
			<div className="h-[320px] w-full">
				<ResponsiveContainer width="100%" height="100%">
					<ComposedChart data={rows} margin={{ top: 16, right: 56, bottom: 4, left: 0 }}>
						<defs>
							{SERIES.map((s) => (
								<linearGradient key={s.gradId} id={s.gradId} x1="0" y1="0" x2="0" y2="1">
									<stop offset="0%" stopColor={s.color} stopOpacity={0.12} />
									<stop offset="100%" stopColor={s.color} stopOpacity={0} />
								</linearGradient>
							))}
						</defs>
						<CartesianGrid stroke="#f1f5f9" vertical={false} />
						<XAxis
							dataKey="bucket"
							tickFormatter={(v: string) => v.slice(5)}
							tick={{ fill: '#94a3b8', fontSize: 11 }}
							axisLine={{ stroke: '#cbd5e1' }}
							tickLine={false}
							minTickGap={28}
						/>
						<YAxis
							tick={{ fill: '#94a3b8', fontSize: 11 }}
							axisLine={{ stroke: '#cbd5e1' }}
							tickLine={false}
							width={44}
						/>
						<Tooltip
							content={<ChartTooltip />}
							cursor={{ stroke: '#94a3b8', strokeWidth: 1, strokeDasharray: '4 4' }}
						/>
						<Legend
							verticalAlign="top"
							align="left"
							iconType="circle"
							iconSize={8}
							formatter={(value: string) => (
								<span className="text-[12px] text-[#475569]">{value}</span>
							)}
						/>
						{SERIES.map((s) => (
							<Area
								key={`area-${s.key}`}
								type="monotone"
								dataKey={s.key as SeriesKey}
								name={s.name}
								// Area 只是淡渐变填充层，必须退出图例——
								// 否则与同名 Line 各出一条，图例每项翻倍
								legendType="none"
								stroke="none"
								fill={`url(#${s.gradId})`}
								isAnimationActive={false}
								activeDot={false}
							/>
						))}
						{SERIES.map((s) => (
							<Line
								key={`line-${s.key}`}
								type="monotone"
								dataKey={s.key as SeriesKey}
								name={s.name}
								stroke={s.color}
								strokeWidth={2}
								fill="none"
								dot={{ r: 4, fill: s.color, stroke: '#ffffff', strokeWidth: 2 }}
								activeDot={{ r: 5, fill: s.color, stroke: '#ffffff', strokeWidth: 2 }}
								isAnimationActive={false}
							>
								{/* content 回调：recharts 注入的 x/y 可能是 string，统一转 number 后交给 EndLabel */}
								<LabelList
									dataKey={s.key as SeriesKey}
									content={(props: {
										x?: string | number;
										y?: string | number;
										index?: number;
										value?: unknown;
									}) => (
										<EndLabel
											x={Number(props.x)}
											y={Number(props.y)}
											index={props.index}
											value={Number(props.value)}
											total={total}
										/>
									)}
								/>
							</Line>
						))}
					</ComposedChart>
				</ResponsiveContainer>
			</div>
		</div>
	);
}
