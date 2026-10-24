/**
 * JSON 展示工具 — JsonViewer / 工具节点内联 I/O / 工具摘要共用。
 *
 * 抽出来是因为三处都要面对同一种脏数据：tool_call 的 `result` 在 PG metadata
 * 里是**字符串**（内嵌 JSON），而 `tool_args` 是对象。展示前先解一层，
 * 避免出现 "{\n  \"found\": true..." 这种双重转义。
 */

/** 把字符串里包着的 JSON 解出来；其余原样返回。 */
function unwrap(value: unknown): unknown {
	if (typeof value !== 'string') return value;
	const s = value.trim();
	if (s.startsWith('{') || s.startsWith('[')) {
		try {
			return JSON.parse(s);
		} catch {
			/* 不是合法 JSON 字符串 */
		}
	}
	return value;
}

/**
 * 把任意值转成可展示文本。pretty=true 时缩进格式化。**永不抛**。
 *
 * - 字符串里装的是 JSON（tool result 常见）→ 先解析再序列化，避免双重转义
 * - 循环引用 / BigInt 等 JSON.stringify 会抛的情况 → 回退 String(value)
 */
export function toDisplayText(value: unknown, pretty: boolean): string {
	const encode = (v: unknown): string =>
		pretty ? JSON.stringify(v, null, 2) : JSON.stringify(v);
	try {
		if (typeof value === 'string') {
			const s = value.trim();
			if (s.startsWith('{') || s.startsWith('[')) {
				try {
					return encode(JSON.parse(s)) ?? s;
				} catch {
					/* 不是合法 JSON 字符串，原样展示 */
				}
			}
			return value;
		}
		return encode(value ?? null) ?? 'null';
	} catch {
		try {
			return String(value);
		} catch {
			return '[unserializable]';
		}
	}
}

/**
 * result 的一行摘要 —— 顶层级 `k=v`，数组 `k=[n]`，最多 4 项。
 *
 * 长字符串截断进摘要而不是丢掉（key 常常就是重点，比如 `query=SELECT…`），
 * 嵌套对象收成 `{…}`。折叠行本身有 truncate，超长由 CSS 兜底。
 * 没有可摘要的结构就退回纯文本首行。
 */
export function resultSummary(value: unknown): string {
	const parsed = unwrap(value);

	if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
		const parts: string[] = [];
		for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
			if (parts.length >= 4) break;
			if (v === null) parts.push(`${k}=null`);
			else if (typeof v === 'boolean' || typeof v === 'number') parts.push(`${k}=${v}`);
			else if (typeof v === 'string') {
				parts.push(v.length <= 24 ? `${k}=${v}` : `${k}=${v.slice(0, 24)}…`);
			} else if (Array.isArray(v)) parts.push(`${k}=[${v.length}]`);
			else parts.push(`${k}={…}`);
		}
		if (parts.length) return parts.join(' · ');
	}

	if (Array.isArray(parsed)) return `[…${parsed.length}]`;

	const text = typeof value === 'string' ? value : toDisplayText(value, false);
	const one = text.split('\n')[0].trim();
	if (!one || one === 'null') return '（空）';
	return one.length > 80 ? `${one.slice(0, 80)}…` : one;
}
