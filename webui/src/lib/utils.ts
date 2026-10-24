import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

/**
 * 将 DB 返回的 datetime 值格式化为 "YYYY-MM-DD HH:mm:ss"。
 * - 字符串输入：归一 'T' → ' '，截断到 19 位（去掉毫秒）
 * - 数字输入（KV 降级路径 epoch 秒）：先转 Date 再格式化
 * - null/undefined/其他：返回空字符串
 */
export function formatDateTime(value: number | string | null | undefined): string {
	if (value == null) return '';
	if (typeof value === 'number') {
		const d = new Date(value * 1000);
		if (isNaN(d.getTime())) return '';
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
	}
	const normalized = String(value).replace('T', ' ');
	return normalized.slice(0, 19);
}
