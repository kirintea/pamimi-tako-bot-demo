/**
 * KPI 三卡 —— 使用人数 / 会话数量 / 对话轮次
 *
 * 图标彩底圆 + 渐变迷你柱（该指标系列色）+ 右下环比徽标。
 * 「会话数量」卡主数字 = 活跃会话数，副文案带新增数（口径见设计文档）。
 * 环比 = (本窗 - 前窗) / 前窗，前窗为 0 显示「新增」，双 0 显示「—」。
 * 徽标不只靠颜色：带 ↑/↓ 箭头形状 + 百分比文案（dataviz status 规范）。
 */

import type { ComponentType, SVGProps } from 'react';

import { ArrowRightLeft, MessageSquare, Users } from 'lucide-react';

import type { StatsBucket, StatsTotals } from '@/api/stats';

const SERIES_COLOR = {
	uv: '#2563eb',
	sessions: '#16a34a',
	turns: '#ea580c',
} as const;

function GradientSparkline({ points, color }: { points: number[]; color: string }) {
	if (points.length === 0) return null;
	const w = 112;
	const h = 44;
	const gap = 2;
	const barW = Math.max(2, (w - gap * (points.length - 1)) / points.length);
	const max = Math.max(...points, 1);
	const gid = `spark-${color.replace('#', '')}`;

	return (
		<svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
			<defs>
				<linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stopColor={color} stopOpacity={0.85} />
					<stop offset="100%" stopColor={color} stopOpacity={0.15} />
				</linearGradient>
			</defs>
			{points.map((v, i) => {
				const bh = Math.max(2, (v / max) * h);
				const x = i * (barW + gap);
				return <rect key={i} x={x} y={h - bh} width={barW} height={bh} rx={2} fill={`url(#${gid})`} />;
			})}
		</svg>
	);
}

function GrowthBadge({ now, prev }: { now: number; prev: number }) {
	if (now === 0 && prev === 0) {
		return <span className="text-[12px] font-semibold text-[#94a3b8]">—</span>;
	}
	if (prev === 0) {
		return (
			<span className="rounded-full bg-[#dcfce7] px-2 py-0.5 text-[12px] font-semibold text-[#15803d]">
				↑ 新增
			</span>
		);
	}
	const raw = ((now - prev) / prev) * 100;
	// 亚 1% 变动保留一位小数，避免被舍入成「→ 0%」掩盖实际涨跌
	const pct = Math.abs(raw) < 1 ? raw.toFixed(1) : String(Math.round(raw));
	if (raw > 0) {
		return (
			<span className="rounded-full bg-[#dcfce7] px-2 py-0.5 text-[12px] font-semibold text-[#15803d]">
				↑ {pct}%
			</span>
		);
	}
	if (raw < 0) {
		return (
			<span className="rounded-full bg-[#fee2e2] px-2 py-0.5 text-[12px] font-semibold text-[#b91c1c]">
				↓ {Math.abs(Number(pct))}%
			</span>
		);
	}
	return (
		<span className="rounded-full bg-[#f1f5f9] px-2 py-0.5 text-[12px] font-semibold text-[#64748b]">
			→ 0%
		</span>
	);
}

function IconCircle({
	Icon,
	color,
}: {
	Icon: ComponentType<SVGProps<SVGSVGElement>>;
	color: string;
}) {
	return (
		<span
			className="flex size-10 shrink-0 items-center justify-center rounded-full"
			style={{ backgroundColor: `${color}1f` }} // 12% 淡底
		>
			<Icon className="size-5" style={{ color }} />
		</span>
	);
}

interface StatCardsProps {
	totals: StatsTotals;
	prev: StatsTotals;
	series: StatsBucket[];
	rangeLabel: string;
}

export function StatCards({ totals, prev, series, rangeLabel }: StatCardsProps) {
	const cards = [
		{
			key: 'uv',
			label: '使用人数',
			value: totals.uv,
			prev: prev.uv,
			sub: `去重活跃用户 · ${rangeLabel}`,
			color: SERIES_COLOR.uv,
			Icon: Users,
			points: series.map((s) => s.uv),
		},
		{
			key: 'sessions',
			label: '会话数量',
			value: totals.sessions_active,
			prev: prev.sessions_active,
			sub: `活跃会话 · 其中新增 ${totals.sessions_new.toLocaleString()}`,
			color: SERIES_COLOR.sessions,
			Icon: MessageSquare,
			points: series.map((s) => s.sessions_active),
		},
		{
			key: 'turns',
			label: '对话轮次',
			value: totals.turns,
			prev: prev.turns,
			sub: '用户↔助手交互次数',
			color: SERIES_COLOR.turns,
			Icon: ArrowRightLeft,
			points: series.map((s) => s.turns),
		},
	];

	return (
		<div className="grid grid-cols-1 gap-5 md:grid-cols-3">
			{cards.map((c) => (
				<div key={c.key} className="rounded-xl border border-[#e2e8f0] bg-white p-5">
					<div className="flex items-start justify-between gap-4">
						<div>
							<div className="flex items-center gap-2.5">
								<IconCircle Icon={c.Icon} color={c.color} />
								<span className="text-[14px] text-[#64748b]">{c.label}</span>
							</div>
							<div className="mt-2 text-[36px] font-bold leading-none text-[#0f172a]">
								{c.value.toLocaleString()}
							</div>
							<div className="mt-2 text-[12px] text-[#94a3b8]">{c.sub}</div>
						</div>
						<div className="flex flex-col items-end gap-2">
							<GradientSparkline points={c.points} color={c.color} />
							<GrowthBadge now={c.value} prev={c.prev} />
						</div>
					</div>
				</div>
			))}
		</div>
	);
}
