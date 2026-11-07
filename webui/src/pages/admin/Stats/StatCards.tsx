/**
 * KPI 三卡 —— 使用人次 / 会话数量 / 对话轮次
 *
 * 图标彩底圆 + 渐变迷你柱（该指标系列色）+ 右下环比徽标。
 * 「使用人次」= 去重(user, 天) 后各天求和，跨天重复计（事实表口径），勿写成整窗去重。
 * 「会话数量」卡主数字 = 活跃会话数，副文案带新增数（口径见设计文档）。
 * 环比 = (本窗 - 前窗) / 前窗，前窗为 0 显示「新增」，双 0 显示「—」。
 * 徽标不只靠颜色：带 ↑/↓ 箭头形状 + 百分比文案（dataviz status 规范）。
 */

import type { ComponentType, SVGProps } from 'react';

import { ArrowRightLeft, MessageSquare, Users } from 'lucide-react';

import type { StatsBucket, StatsTotals } from '@/api/stats';
import { chart } from '@/lib/themeColors';

const SERIES_COLOR = {
	uv: chart.uv,
	sessions: chart.sessions,
	turns: chart.turns,
} as const;

function GradientSparkline({ id, points, color }: { id: string; points: number[]; color: string }) {
	if (points.length === 0) return null;
	const w = 112;
	const h = 44;
	const gap = 2;
	const barW = Math.max(2, (w - gap * (points.length - 1)) / points.length);
	const max = Math.max(...points, 1);
	// 渐变 id 必须是纯 CSS 标识符，绝不能由 color 推导：color 现在是 var(--chart-xxx)，
	// 拼出来的 url(#spark-var(--chart-uv)) 里那个 ')' 会当场打断 url() 解析 →
	// fill 非法 → 回落到 SVG 初始值黑（三根迷你柱全黑就是这么来的）。
	const gid = `spark-${id}`;

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
		return <span className="text-[12px] font-semibold text-stats-muted">—</span>;
	}
	if (prev === 0) {
		return (
			<span className="rounded-full bg-stats-up-bg px-2 py-0.5 text-[12px] font-semibold text-stats-up-fg">
				↑ 新增
			</span>
		);
	}
	const raw = ((now - prev) / prev) * 100;
	// 亚 1% 变动保留一位小数，避免被舍入成「→ 0%」掩盖实际涨跌
	const pct = Math.abs(raw) < 1 ? raw.toFixed(1) : String(Math.round(raw));
	if (raw > 0) {
		return (
			<span className="rounded-full bg-stats-up-bg px-2 py-0.5 text-[12px] font-semibold text-stats-up-fg">
				↑ {pct}%
			</span>
		);
	}
	if (raw < 0) {
		return (
			<span className="rounded-full bg-stats-down-bg px-2 py-0.5 text-[12px] font-semibold text-stats-down-fg">
				↓ {Math.abs(Number(pct))}%
			</span>
		);
	}
	return (
		<span className="rounded-full bg-stats-neutral-bg px-2 py-0.5 text-[12px] font-semibold text-stats-neutral-fg">
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
			// 12% 淡底。不能写 `${color}1f` —— 透明度后缀拼在十六进制上合法（#2563eb1f），
			// 拼在 var(--chart-xxx) 上就是非法值 → 透明。color-mix 派生，随主题自动跟着换色。
			style={{ backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)` }}
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
			label: '使用人次',
			value: totals.uv,
			prev: prev.uv,
			sub: `活跃用户 · 跨天各计一次 · ${rangeLabel}`,
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
				<div key={c.key} className="rounded-xl border border-stats-border bg-stats-panel p-5">
					<div className="flex items-start justify-between gap-4">
						<div>
							<div className="flex items-center gap-2.5">
								<IconCircle Icon={c.Icon} color={c.color} />
								<span className="text-[14px] text-stats-faint">{c.label}</span>
							</div>
							<div className="mt-2 text-[36px] font-bold leading-none text-stats-title">
								{c.value.toLocaleString()}
							</div>
							<div className="mt-2 text-[12px] text-stats-muted">{c.sub}</div>
						</div>
						<div className="flex flex-col items-end gap-2">
							<GradientSparkline id={c.key} points={c.points} color={c.color} />
							<GrowthBadge now={c.value} prev={c.prev} />
						</div>
					</div>
				</div>
			))}
		</div>
	);
}
