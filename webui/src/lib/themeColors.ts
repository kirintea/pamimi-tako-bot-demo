/**
 * 运营看板图表用色 —— 统一走 CSS 变量字符串。
 *
 * Recharts 的 fill / stroke / stopColor 最终落在 SVG 表现属性上，浏览器会解析
 * var()，所以 .dark 切换后图表自动换色，不需要 JS 感知主题、也不需要重渲染。
 *
 * 不要在这里写死十六进制：那样暗色下图表仍是浅色值，而且 dark: 工具类对 JS
 * 对象里的字符串完全无效。
 *
 * 两个已踩过的坑（值是 var() 之后，十六进制时代的写法会失效）：
 *  1. 透明度不能字符串拼 —— `${color}1f` 得到 var(--x)1f 这个非法值；
 *     要淡底就用 color-mix(in srgb, var(--x) 12%, transparent)。
 *  2. SVG 的 url(#id) 里不能塞进由 color 推导的 id —— var() 里的 ')' 会当场打断
 *     url() 解析，fill 非法、回落成黑；id 必须是稳定的纯 CSS 标识符。
 */
export const chart = {
	uv: 'var(--chart-uv)',
	sessions: 'var(--chart-sessions)',
	turns: 'var(--chart-turns)',
	grid: 'var(--chart-grid)',
	axis: 'var(--chart-axis)',
	tick: 'var(--chart-tick)',
	cursor: 'var(--chart-cursor)',
	ring: 'var(--chart-ring)',
	tooltipBg: 'var(--chart-tooltip-bg)',
	tooltipFg: 'var(--chart-tooltip-fg)',
	tooltipMeta: 'var(--chart-tooltip-meta)',
	tooltipBorder: 'var(--chart-tooltip-border)',
} as const;

export type ChartToken = keyof typeof chart;
