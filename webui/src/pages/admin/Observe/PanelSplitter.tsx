/**
 * 中栏「对话详情」/ 右栏「节点追踪」之间的可拖拽分隔条
 *
 * 纯 Pointer Events，不引第三方依赖（鼠标 / 触屏 / 笔同一套）。
 * 拖动时用 pointer capture，指针移出元素也不丢事件；同时给 body 挂
 * `select-none` + `cursor-col-resize`，避免拖拽过程中把中栏正文选中。
 *
 * 键盘可达：← 把右栏调宽、→ 调窄、Home/End 到极值、双击复位。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

function clamp(v: number, min: number, max: number): number {
	return Math.min(Math.max(v, min), max);
}

interface Props {
	/** 右栏当前宽度（px） */
	value: number;
	min: number;
	max: number;
	/** 拖动/键盘回调，值已 clamp */
	onChange: (width: number) => void;
	/** 双击复位到这个宽度 */
	defaultValue: number;
	ariaLabel?: string;
}

const STEP = 24;

export function PanelSplitter({
	value,
	min,
	max,
	onChange,
	defaultValue,
	ariaLabel = '调整节点追踪面板宽度',
}: Props) {
	const [dragging, setDragging] = useState(false);
	// 起拖瞬间的指针 x 与右栏宽度，拖动过程只做增量，避免依赖每次 render 的 value
	const startRef = useRef({ x: 0, width: 0 });

	useEffect(() => {
		if (!dragging) return;
		const prevCursor = document.body.style.cursor;
		const prevSelect = document.body.style.userSelect;
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
		return () => {
			document.body.style.cursor = prevCursor;
			document.body.style.userSelect = prevSelect;
		};
	}, [dragging]);

	const handlePointerDown = useCallback(
		(e: React.PointerEvent<HTMLDivElement>) => {
			// 不 preventDefault 会让触屏开始滚动手势；但要先拿到 capture
			(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
			startRef.current = { x: e.clientX, width: value };
			setDragging(true);
		},
		[value],
	);

	const handlePointerMove = useCallback(
		(e: React.PointerEvent<HTMLDivElement>) => {
			if (!dragging) return;
			// 分隔条左移 = 右栏变宽
			const next = startRef.current.width + (startRef.current.x - e.clientX);
			onChange(clamp(next, min, max));
		},
		[dragging, min, max, onChange],
	);

	const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
		(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
		setDragging(false);
	}, []);

	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLDivElement>) => {
			const map: Record<string, number> = {
				ArrowLeft: value + STEP,
				ArrowRight: value - STEP,
				Home: max,
				End: min,
			};
			if (!(e.key in map)) return;
			e.preventDefault();
			onChange(clamp(map[e.key], min, max));
		},
		[value, min, max, onChange],
	);

	return (
		<div
			role="separator"
			aria-orientation="vertical"
			aria-label={ariaLabel}
			aria-valuenow={Math.round(value)}
			aria-valuemin={Math.round(min)}
			aria-valuemax={Math.round(max)}
			tabIndex={0}
			onPointerDown={handlePointerDown}
			onPointerMove={handlePointerMove}
			onPointerUp={handlePointerUp}
			onPointerCancel={handlePointerUp}
			onKeyDown={handleKeyDown}
			onDoubleClick={() => onChange(clamp(defaultValue, min, max))}
			className="group relative w-[7px] shrink-0 cursor-col-resize touch-none bg-transparent outline-none"
		>
			{/* 竖线：常态极淡，hover / 拖拽 / 聚焦时变主色加粗 */}
			<div
				className={`pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${
					dragging ? 'bg-primary' : 'bg-border group-hover:bg-primary/50 group-focus-visible:bg-primary/50'
				}`}
			/>
			{/* 拖拽热区中间的抓手小竖条，提示这里可以拖 */}
			<div
				className={`pointer-events-none absolute top-1/2 left-1/2 h-8 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full transition-colors ${
					dragging ? 'bg-primary' : 'bg-transparent group-hover:bg-primary/40'
				}`}
			/>
		</div>
	);
}
