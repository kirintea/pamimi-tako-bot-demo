/**
 * 主题读写 —— 启动引导 / ThreadHeader / GeneralPage 三处共用一套口径。
 *
 * localStorage['theme']: 'light' | 'dark'，不存 = 跟随系统。
 * <html class="dark"> 是唯一的视觉开关。
 *
 * index.html 里有一段内联脚本在首帧前按同一口径回放，所以刷新不会闪回浅色；
 * 那段脚本跑在 bundle 之前、无法 import 本模块 —— 两边的判定逻辑改动必须同步。
 */

export type Theme = 'light' | 'dark';

/** 存储态：未设置 = 'system'（跟随系统） */
export function getStoredTheme(): Theme | 'system' {
	const stored = localStorage.getItem('theme');
	return stored === 'light' || stored === 'dark' ? stored : 'system';
}

/** 解析后的实际主题（'system' 落到当前系统偏好） */
export function readTheme(): Theme {
	const stored = getStoredTheme();
	if (stored !== 'system') return stored;
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** 写入偏好并立刻生效；'system' = 清掉存储、跟随系统 */
export function setTheme(mode: Theme | 'system'): void {
	if (mode === 'system') localStorage.removeItem('theme');
	else localStorage.setItem('theme', mode);
	document.documentElement.classList.toggle('dark', readTheme() === 'dark');
}
