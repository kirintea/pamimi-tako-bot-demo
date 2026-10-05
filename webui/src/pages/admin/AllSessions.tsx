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
import { cn } from '@/lib/utils';
import { wsManager } from '@/api/ws';

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
				// 默认选中第一个用户及其首条会话
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

	// 按 user_id 分组
	const users = useMemo(() => {
		const map = new Map<string, number>();
		for (const s of allSessions) {
			map.set(s.user_id, (map.get(s.user_id) ?? 0) + 1);
		}
		return Array.from(map.entries()).map(([user_id, count]) => ({ user_id, count }));
	}, [allSessions]);

	const filteredUsers = useMemo(() => {
		const q = userQuery.trim().toLowerCase();
		if (!q) return users;
		return users.filter((u) => u.user_id.toLowerCase().includes(q));
	}, [users, userQuery]);

	const userSessions = useMemo(
		() => allSessions.filter((s) => s.user_id === selectedUserId),
		[allSessions, selectedUserId],
	);

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
								placeholder="搜索用户…"
								className="w-full bg-transparent text-[13px] outline-none placeholder:text-nav-label"
							/>
						</div>
						<div className="h-[calc(100%-2.75rem)] overflow-y-auto p-2">
							{loading ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">加载中…</div>
							) : filteredUsers.length === 0 ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">无用户</div>
							) : (
								filteredUsers.map((u) => (
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
											{u.user_id.slice(0, 2).toUpperCase()}
										</span>
										<span className="flex min-w-0 flex-col">
											<span className="truncate">{u.user_id}</span>
											<span className="text-[11px] text-text-tertiary">{u.count} 个会话</span>
										</span>
									</button>
								))
							)}
						</div>
					</div>

					{/* ② 会话列表 */}
					<div className="w-[280px] shrink-0 border-r border-border">
						<div className="flex h-11 items-center border-b border-border px-3 text-[12px] font-semibold text-text-tertiary">
							{selectedUserId || '—'} 的会话
						</div>
						<div className="h-[calc(100%-2.75rem)] overflow-y-auto p-2">
							{userSessions.length === 0 ? (
								<div className="px-3 py-2 text-[12px] text-text-tertiary">暂无会话</div>
							) : (
								userSessions.map((s) => (
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
										<span className="text-[11px] text-text-tertiary">
											{s.message_count} 条 · {new Date(s.last_active * 1000).toLocaleDateString()}
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
								只读查看 · {selectedUserId} 的会话（root 视图，不可回复）
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
										<MessageBubble key={m.id} message={m} />
									))}
								</div>
							)}
						</div>
						{/* 只读输入占位 */}
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
