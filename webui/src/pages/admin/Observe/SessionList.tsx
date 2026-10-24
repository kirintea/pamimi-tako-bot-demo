/**
 * 观测台左栏 — 会话列表（搜索 + 卡片）
 *
 * 卡片与搜索照抄 AllSessions.tsx 范式：标题 + 渠道徽标 + N 条 · 时间。
 * 搜索是前端过滤（user_id / display_name / session_id / channel）。
 */

import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';

import type { SessionInfo } from '@/api/types';
import { channelLabel } from '@/lib/channel';
import { cn, formatDateTime } from '@/lib/utils';

interface Props {
	sessions: SessionInfo[];
	loading: boolean;
	error: string | null;
	selectedSessionId: string | null;
	onSelect: (userId: string, sessionId: string) => void;
}

export function SessionList({ sessions, loading, error, selectedSessionId, onSelect }: Props) {
	const [query, setQuery] = useState('');

	const filtered = useMemo(() => {
		const q = query.trim().toLowerCase();
		if (!q) return sessions;
		return sessions.filter(
			(s) =>
				s.user_id.toLowerCase().includes(q) ||
				(s.display_name?.toLowerCase().includes(q) ?? false) ||
				s.session_id.toLowerCase().includes(q) ||
				(s.channel?.toLowerCase().includes(q) ?? false),
		);
	}, [sessions, query]);

	// 宽度随断点收缩：小屏把空间让给「对话详情 / 节点追踪」，否则整行会顶出画面
	return (
		<div className="flex h-full w-[200px] shrink-0 flex-col border-r border-border lg:w-[240px] xl:w-[280px]">
			<div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
				<span className="text-[12px] font-semibold text-text-tertiary">会话列表</span>
				<span className="rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-text-tertiary">
					{filtered.length}
				</span>
			</div>
			<div className="border-b border-border p-2">
				<div className="flex items-center gap-1.5 rounded-[8px] bg-secondary px-2 py-1">
					<Search className="size-3.5 text-text-tertiary" />
					<input
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						placeholder="搜索用户 / session / 渠道"
						className="w-full bg-transparent text-[13px] outline-none placeholder:text-nav-label"
					/>
				</div>
			</div>
			<div className="min-h-0 flex-1 overflow-y-auto p-2">
				{loading ? (
					<div className="px-3 py-2 text-[12px] text-text-tertiary">加载中…</div>
				) : error ? (
					<div className="px-3 py-2 text-[12px] text-destructive">加载失败：{error}</div>
				) : filtered.length === 0 ? (
					<div className="px-3 py-2 text-[12px] text-text-tertiary">无匹配会话</div>
				) : (
					filtered.map((s) => (
						<button
							key={`${s.user_id}:${s.session_id}`}
							type="button"
							onClick={() => onSelect(s.user_id, s.session_id)}
							className={cn(
								'mb-1 flex w-full flex-col gap-0.5 rounded-[8px] px-2.5 py-2 text-left transition-colors',
								selectedSessionId === s.session_id ? 'bg-primary-light' : 'hover:bg-row-hover',
							)}
						>
							<div className="flex items-center gap-1.5">
								<span
									className={cn(
										'truncate text-[13px]',
										selectedSessionId === s.session_id
											? 'font-medium text-primary'
											: 'text-nav-label',
									)}
								>
									{s.title || s.session_id.slice(0, 8)}
								</span>
								{s.channel && (
									<span className="shrink-0 rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-text-tertiary">
										{channelLabel(s.channel)}
									</span>
								)}
							</div>
							<span className="text-[11px] text-text-tertiary">
								{s.message_count} 条 · {formatDateTime(s.last_active)}
							</span>
							<span className="truncate text-[11px] text-text-tertiary">
								{s.display_name || s.user_id}
							</span>
						</button>
					))
				)}
			</div>
		</div>
	);
}
