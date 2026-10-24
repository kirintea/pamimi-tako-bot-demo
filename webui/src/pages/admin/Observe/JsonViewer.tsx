/**
 * JSON 查看器 — 格式化 / 原始 切换 + 行号 + 复制 + 可鼠标局部选中
 *
 * 外层用 div 而不是 pre：pre 里塞块级 div 会破坏跨行鼠标选择。
 * 行号 select-none，正文 select-text（默认可选，这里显式钉住）。
 */

import { useState } from 'react';

import { toDisplayText } from './jsonText';

export function JsonViewer({ value }: { value: unknown }) {
	const [formatted, setFormatted] = useState(true);
	const [copied, setCopied] = useState(false);

	const display = toDisplayText(value, formatted);
	const lines = display.split('\n');

	const copy = () => {
		navigator.clipboard.writeText(display).then(
			() => {
				setCopied(true);
				setTimeout(() => setCopied(false), 1500);
			},
			() => {
				/* 剪贴板被拒/不可用，静默忽略（只读观测台，不打断） */
			},
		);
	};

	return (
		<div className="rounded-[8px] border border-border">
			<div className="flex items-center gap-2 border-b border-border px-2 py-1">
				<button
					type="button"
					onClick={() => setFormatted(true)}
					className={`rounded px-1.5 py-0.5 text-[11px] ${formatted ? 'bg-primary-light text-primary' : 'text-text-tertiary hover:bg-row-hover'}`}
				>
					格式化
				</button>
				<button
					type="button"
					onClick={() => setFormatted(false)}
					className={`rounded px-1.5 py-0.5 text-[11px] ${!formatted ? 'bg-primary-light text-primary' : 'text-text-tertiary hover:bg-row-hover'}`}
				>
					原始 JSON
				</button>
				<button
					type="button"
					onClick={copy}
					className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-text-tertiary hover:bg-row-hover"
				>
					{copied ? '已复制' : '复制'}
				</button>
			</div>
			<div className="max-h-64 select-text overflow-auto bg-secondary/40 p-2 text-[11px] leading-[1.5]">
				{lines.map((line, i) => (
					<div key={i} className="flex">
						<span className="w-7 shrink-0 select-none pr-2 text-right text-text-tertiary">
							{i + 1}
						</span>
						<span className="select-text whitespace-pre-wrap break-all font-mono">
							{line}
						</span>
					</div>
				))}
			</div>
		</div>
	);
}
