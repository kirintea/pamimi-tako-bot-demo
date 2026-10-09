/**
 * 上下文用量指示器
 *
 * 显示当前会话的 token 用量、状态颜色、压缩按钮。
 * 嵌入在对话输入框上方。
 *
 * 数据来源：优先用后端 /context 接口（reply_end 后落库的精确用量）；
 * 当后端 agent state 尚未就绪（典型场景：仅查看历史会话、尚未产生新轮次对话）时，
 * 退化为「前端基于已加载消息的兜底估算」，使进度条在任意会话下都能即时、有意义地显示，
 * 不再出现「一直 0% · 0K/128K· 0 条」的问题。
 */

import { Loader2, Minimize2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/api/types';

interface ContextInfo {
	estimated_tokens: number;
	context_window: number;
	usage_ratio: number;
	status: 'healthy' | 'warning' | 'critical';
	message_count: number;
}

interface Props {
	userId: string;
	sessionId: string | null;
	/** 当前会话消息（后端 agent state 未就绪时，前端据此兜底估算用量） */
	messages?: ChatMessage[];
	/** 外部刷新信号：值变化时立即重新拉取用量（如一轮回复结束后） */
	refreshSignal?: number;
	className?: string;
}

/** 粗略 token 估算：中文约 1.5 字符/token、英文约 4 字符/token，折中按 ~2 字符/token 计 */
function estimateTokens(msgs?: ChatMessage[]): number {
	if (!msgs || msgs.length === 0) return 0;
	let chars = 0;
	for (const m of msgs) {
		if (typeof m.content === 'string') {
			chars += m.content.length;
		}
	}
	return Math.ceil(chars / 2);
}

/** token 数值格式化：<1K 显示到个位数；≥1K 保留两位小数并以 K 计 */
function formatTokens(n: number): string {
	if (n < 1000) return `${Math.round(n)}`;
	return `${(n / 1000).toFixed(2)}K`;
}

/** 上下文窗口（模型固定上限）格式化：整数 K */
function formatWindow(w: number): string {
	return `${Math.round(w / 1000)}K`;
}

export function ContextIndicator({ userId, sessionId, messages, refreshSignal, className }: Props) {
	const [info, setInfo] = useState<ContextInfo | null>(null);
	const [compressing, setCompressing] = useState(false);

	// 前端兜底估算（基于已加载消息）
	const clientTokens = useMemo(() => estimateTokens(messages), [messages]);
	const clientCount = messages?.length ?? 0;

	// 拉取上下文用量（后端精确值，reply_end 后落库）
	const fetchContext = useCallback(async () => {
		if (!sessionId) {
			setInfo(null);
			return;
		}
		try {
			const resp = await fetch(`/sessions/${userId}/${sessionId}/context`);
			if (resp.ok) {
				const data = await resp.json();
				setInfo(data);
			}
		} catch {
			// 静默失败：退化为前端估算
		}
	}, [userId, sessionId]);

	// 切换会话 / 外部信号刷新
	useEffect(() => {
		fetchContext();
		if (!sessionId) return;
		const timer = setInterval(fetchContext, 30000);
		return () => clearInterval(timer);
	}, [fetchContext, sessionId]);

	useEffect(() => {
		if (refreshSignal && sessionId) {
			fetchContext();
		}
	}, [refreshSignal, sessionId, fetchContext]);

	// 手动压缩
	const handleCompress = useCallback(async () => {
		if (!sessionId || compressing) return;
		setCompressing(true);
		try {
			const resp = await fetch(
				`/sessions/${userId}/${sessionId}/compress`,
				{ method: 'POST' },
			);
			if (resp.ok) {
				await fetchContext();
			}
		} catch {
			// 静默失败
		} finally {
			setCompressing(false);
		}
	}, [userId, sessionId, compressing, fetchContext]);

	// 无会话且无任何消息时不渲染
	if (!sessionId && clientCount === 0) return null;

	// 合并：后端有数据优先，否则用前端估算
	const backendReady = !!info && (info.estimated_tokens > 0 || info.message_count > 0);
	const contextWindow = info?.context_window ?? 128000;
	const effectiveTokens = backendReady ? info!.estimated_tokens : clientTokens;
	const effectiveCount = backendReady ? Math.max(info!.message_count, clientCount) : clientCount;
	const percent = Math.min(100, Math.round((effectiveTokens / contextWindow) * 100));
	const status: 'healthy' | 'warning' | 'critical' =
		percent < 35 ? 'healthy' : percent < 45 ? 'warning' : 'critical';
	const showCompress = status === 'warning' || status === 'critical';

	return (
		<div
			className={cn(
				'flex items-center gap-3 px-5 py-1.5 text-[12px] text-muted-foreground',
				className,
			)}
		>
			{/* 进度条 */}
			<div className="flex-1 flex items-center gap-2">
				<div className="flex-1 h-0.5 bg-muted rounded-full overflow-hidden">
					<div
						className={cn(
							'h-full rounded-full transition-all duration-300',
							status === 'healthy' && 'bg-green-500',
							status === 'warning' && 'bg-yellow-500',
							status === 'critical' && 'bg-red-500',
						)}
						style={{ width: `${percent}%` }}
					/>
				</div>
				<span className="font-mono tabular-nums">
					{percent}% · {formatTokens(effectiveTokens)}/{formatWindow(contextWindow)}
				</span>
				<span>· {effectiveCount} 条</span>
			</div>

			{/* 状态提示 */}
			{status !== 'healthy' && (
				<span
					className={cn(
						'font-medium',
						status === 'warning' && 'text-yellow-600',
						status === 'critical' && 'text-red-600',
					)}
				>
					{status === 'warning' ? '上下文较长' : '建议开新会话'}
				</span>
			)}

			{/* 压缩按钮 */}
			{showCompress && (
				<Button
					variant="outline"
					size="sm"
					className="h-6 px-2.5 text-xs rounded-full"
					onClick={handleCompress}
					disabled={compressing}
				>
					{compressing ? (
						<Loader2 className="size-3 animate-spin" />
					) : (
						<Minimize2 className="size-3" />
					)}
					压缩上下文
				</Button>
			)}
		</div>
	);
}
