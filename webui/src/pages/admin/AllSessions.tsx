/**
 * 管理员视图 — 全部用户会话（root 只读查看，跨用户扫描）。
 *
 * 三栏布局：
 *  ① 用户列表（按 user_id 分组，来自 GET /admin/sessions?viewer=root）
 *  ② 选中用户的会话列表
 *  ③ 选中会话的只读消息流（GET /sessions/{owner}/{sid}/messages?viewer=root）
 *
 * 边界：root 只读、不可回复、不写回他人会话（见方案 §8.6）。
 */

import { Crown, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { sessionApi } from '@/api/session';
import { rebuildChatMessages } from '@/lib/rebuildMessages';
import type { ChatMessage, SessionInfo } from '@/api/types';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { cn, formatDateTime } from '@/lib/utils';
import { wsManager } from '@/api/ws';

/** channel → 中文映射 */
const CHANNEL_LABELS: Record<string, string> = {
	web: 'Web',
	wecom: '企业微信',
};

/**
 * 消息时间展示：直接截断 DB datetime 字符串到秒，不做日期换算。
 * 2026-10-09 09:37:36.904 → 2026-10-09 09:37:36（兼容 T 分隔符）
 */
const formatTime = (timeStr?: string): string | undefined => {
	if (!timeStr) return undefined;
	return timeStr.replace('T', ' ').substring(0, 19);
};

export function AllSessions() {
	const viewer = wsManager.getUserId();

	const [allSessions, setAllSessions] = useState<SessionInfo[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const [userQuery, setUserQuery] = useState('');
	const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
	const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [messagesLoading, setMessagesLoading] = useState(false);
	const [messagesError, setMessagesError] = useState<string | null>(null);

	// ① 拉取全部用户会话（仅 root 可访问）
	useEffect(() => {
		if (!viewer) {
			setLoading(false);
			return;
		}
		let cancelled = false;
		setLoading(true);
		sessionApi
			.listAllSessions(viewer)
			.then((res) => {
				if (cancelled) return;
				const list = res.sessions || [];
				setAllSessions(list);
				const firstUser = list[0]?.user_id ?? null;
				setSelectedUserId(firstUser);
				if (firstUser) {
					const firstSid = list.find((s) => s.user_id === firstUser)?.session_id ?? null;
					setSelectedSessionId(firstSid);
				}
			})
			.catch((e) => {
				if (!cancelled) {
					setError(e?.response?.data?.detail || e?.message || '加载会话列表失败（需 root 权限）');
				}
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [viewer]);

	// 按 user_id 分组，合并 display_name
	const users = useMemo(() => {
		const map = new Map<string, { count: number; display_name: string | null }>();
		for (const s of allSessions) {
			const existing = map.get(s.user_id);
			if (existing) {
				existing.count += 1;
			} else {
				map.set(s.user_id, { count: 1, display_name: s.display_name ?? null });
			}
		}
		return Array.from(map.entries()).map(([user_id, { count, display_name }]) => ({
			user_id,
			display_name,
			count,
		}));
	}, [allSessions]);

	// 搜索同时匹配 user_id、display_name 和 session_id
	const filteredUsers = useMemo(() => {
		const q = userQuery.trim().toLowerCase();
		if (!q) return users;
		return users.filter(
			(u) =>
				u.user_id.toLowerCase().includes(q) ||
				(u.display_name?.toLowerCase().includes(q) ?? false) ||
				allSessions.some(
					(s) => s.user_id === u.user_id && s.session_id.toLowerCase().includes(q),
				),
		);
	}, [users, allSessions, userQuery]);

	const userSessions = useMemo(
		() => allSessions.filter((s) => s.user_id === selectedUserId),
		[allSessions, selectedUserId],
	);

	// 会话列表跟随搜索：查询命中 user_id / display_name 时展示全部会话，否则按 session_id 过滤
	const visibleSessions = useMemo(() => {
		const q = userQuery.trim().toLowerCase();
		if (!q) return userSessions;
		const userMatched =
			!!selectedUserId &&
			(selectedUserId.toLowerCase().includes(q) ||
				(users.find((x) => x.user_id === selectedUserId)?.display_name?.toLowerCase().includes(q) ??
					false));
		if (userMatched) return userSessions;
		return userSessions.filter((s) => s.session_id.toLowerCase().includes(q));
	}, [userSessions, userQuery, selectedUserId, users]);

	// 搜索时自动跟随选中（选中项不在过滤结果中则跳到首个匹配的用户/会话）
	useEffect(() => {
		const q = userQuery.trim().toLowerCase();
		if (!q || filteredUsers.length === 0) return;
		const userStillVisible = filteredUsers.some((u) => u.user_id === selectedUserId);
		const targetUser = userStillVisible ? selectedUserId! : filteredUsers[0].user_id;
		const targetLabel = filteredUsers.find((u) => u.user_id === targetUser);
		const matchedByUser =
			targetUser.toLowerCase().includes(q) ||
			(targetLabel?.display_name?.toLowerCase().includes(q) ?? false);
		const candidates = allSessions.filter(
			(s) =>
				s.user_id === targetUser &&
				(matchedByUser || s.session_id.toLowerCase().includes(q)),
		);
		const targetSid = candidates.some((s) => s.session_id === selectedSessionId)
			? selectedSessionId
			: (candidates[0]?.session_id ?? null);
		if (targetUser !== selectedUserId) setSelectedUserId(targetUser);
		if (targetSid !== selectedSessionId) setSelectedSessionId(targetSid);
	}, [userQuery, filteredUsers, allSessions, selectedUserId, selectedSessionId]);

	// 选中用户的展示名（优先 display_name，回退 user_id）
	const selectedUserLabel = useMemo(() => {
		if (!selectedUserId) return '—';
		const u = users.find((x) => x.user_id === selectedUserId);
		return u?.display_name || selectedUserId;
	}, [selectedUserId, users]);

	// ③ 拉取选中会话的消息（只读）
	useEffect(() => {
		if (!viewer || !selectedUserId || !selectedSessionId) {
			setMessages([]);
			return;
		}
		let cancelled = false;
		setMessagesLoading(true);
		setMessagesError(null);
		sessionApi
			.messages(selectedUserId, selectedSessionId, { viewer })
			.then((res) => {
				if (!cancelled) setMessages(rebuildChatMessages(res));
			})
			.catch((e) => {
				if (!cancelled) {
					setMessagesError(
						e?.response?.status === 403
							? '无权查看该会话（需 root 权限）'
							: e?.response?.data?.detail || e?.message || '加载消息失败',
					);
				}
			})
			.finally(() => {
				if (!cancelled) setMessagesLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, [viewer, selectedUserId, selectedSessionId]);

	return (
		<div className="flex h-full flex-col bg-canvas">
			{/* 顶栏 */}
			<div className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-5">
				<Crown className="size-4 text-primary" />
				<span className="text-[15px] font-semibold text-foreground">全部用户会话</span>
				<span className="rounded-full bg-warning/15 px-2.5 py-0.5 text-[12px] font-semibold text-warning">
					🔒 root 只读视图
				</span>
			</div>

			{error ? (
				<div className="flex flex-1 items-center justify-center text-[14px] text-destructive">{error}</div>
			) : (
				<div className="flex min-h-0 flex-1">
					{/* ① 用户列表 */}
					<div className="w-[300px] shrink-0 border-r border-border">
						<div className="flex h-11 items-center gap-2 border-b border-border px-3">
							<Search className="size-4 text-text-tertiary" />
							<input
								value={userQuery}
								onChange={(e) => setUserQuery(e.target.value)}
								placeholder="搜索用户ID、用户名或会话ID…"
								className="w-full bg-transparent text-[13px] outline-none placeholder:text-nav-label"
							/>
						</div>
						<div className="h-[calc(100%-2.75rem)] overflow-y-auto p-2">
							{loading ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">加载中…</div>
							) : filteredUsers.length === 0 ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">无用户</div>
							) : (
								filteredUsers.map((u) => {
									const label = u.display_name || u.user_id;
									return (
										<button
											key={u.user_id}
											type="button"
											onClick={() => {
												setSelectedUserId(u.user_id);
												const firstSid =
													allSessions.find((s) => s.user_id === u.user_id)?.session_id ?? null;
												setSelectedSessionId(firstSid);
											}}
											className={cn(
												'flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-2.5 text-left text-[13px] transition-colors',
												selectedUserId === u.user_id
													? 'bg-primary-light font-medium text-primary'
													: 'font-normal text-nav-label hover:bg-row-hover',
											)}
										>
											<span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">
												{label.slice(0, 2).toUpperCase()}
											</span>
											<span className="flex min-w-0 flex-col">
												<span className="truncate">{label}</span>
												<span className="text-[11px] text-text-tertiary">{u.count} 个会话</span>
											</span>
										</button>
									);
								})
							)}
						</div>
					</div>

					{/* ② 会话列表 */}
					<div className="w-[280px] shrink-0 border-r border-border">
						<div className="flex h-11 items-center border-b border-border px-3 text-[12px] font-semibold text-text-tertiary">
							{selectedUserLabel} 的会话
						</div>
						<div className="h-[calc(100%-2.75rem)] overflow-y-auto p-2">
							{visibleSessions.length === 0 ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">暂无会话</div>
							) : (
								visibleSessions.map((s) => (
									<button
										key={s.session_id}
										type="button"
										onClick={() => setSelectedSessionId(s.session_id)}
										className={cn(
											'flex w-full flex-col gap-0.5 rounded-[8px] px-2.5 py-2 text-left transition-colors',
											selectedSessionId === s.session_id
												? 'bg-primary-light'
												: 'hover:bg-row-hover',
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
													{CHANNEL_LABELS[s.channel] || s.channel}
												</span>
											)}
										</div>
										<span className="text-[11px] text-text-tertiary">
											{s.message_count} 条 · {formatDateTime(s.last_active)}
										</span>
									</button>
								))
							)}
						</div>
					</div>

					{/* ③ 只读消息流 */}
					<div className="flex min-w-0 flex-1 flex-col">
						<div className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-warning/10 px-4">
							<span className="text-[12px] font-semibold text-warning">
								只读查看 · {selectedUserLabel} 的会话（root 视图，不可回复）
							</span>
						</div>
						<div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
							{messagesLoading ? (
								<div className="text-[13px] text-text-tertiary">加载消息中…</div>
							) : messagesError ? (
								<div className="text-[13px] text-destructive">{messagesError}</div>
							) : messages.length === 0 ? (
								<div className="text-[13px] text-text-tertiary">该会话暂无消息</div>
							) : (
								<div className="mx-auto flex max-w-[760px] flex-col gap-5">
									{messages.map((m) => (
										<MessageBubble
											key={m.id}
											message={m}
											timeText={formatTime(m.created_at)}
										/>
									))}
								</div>
							)}
						</div>
						<div className="flex h-16 shrink-0 items-center border-t border-border px-4">
							<div className="flex h-9 w-full items-center rounded-[10px] bg-secondary px-3 text-[13px] text-text-tertiary">
								只读模式，无法回复
							</div>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}