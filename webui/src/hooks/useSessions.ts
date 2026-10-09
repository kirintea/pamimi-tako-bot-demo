/**
 * 会话列表管理 hook
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { sessionApi } from '@/api/session';
import type { SessionInfo } from '@/api/types';

export function useSessions(userId: string) {
	// 服务端返回的会话（权威数据，reply_end 后落库刷新得到）
	const [serverSessions, setServerSessions] = useState<SessionInfo[]>([]);
	// 客户端乐观插入的会话（新会话首条消息发送后立即出现，不等后端落库）
	const [optimisticSessions, setOptimisticSessions] = useState<SessionInfo[]>([]);
	const [loading, setLoading] = useState(false);

	const refresh = useCallback(async () => {
		if (!userId) return;
		setLoading(true);
		try {
			const res = await sessionApi.list(userId);
			setServerSessions(res.sessions);
		} catch (e) {
			console.error('加载会话列表失败:', e);
		} finally {
			setLoading(false);
		}
	}, [userId]);

	useEffect(() => {
		refresh();
	}, [refresh]);

	/**
	 * 乐观插入/更新会话：新会话首条消息发送后立即出现在「最近」列表。
	 * 与 serverSessions 按 session_id 去重合并——服务端数据已存在时以服务端为准，
	 * 否则（后端尚未落库）用乐观条目顶在列表最前，保持其可见直到真实数据到达。
	 */
	const upsertSessionOptimistic = useCallback((session: SessionInfo) => {
		setOptimisticSessions((prev) => {
			const exists = prev.some((s) => s.session_id === session.session_id);
			if (exists) {
				return prev.map((s) =>
					s.session_id === session.session_id ? { ...s, ...session } : s,
				);
			}
			return [session, ...prev];
		});
	}, []);

	// 合并：乐观条目在前（置顶为最新），服务端条目在后；同 id 去重（服务端优先）
	const sessions = useMemo(() => {
		const serverIds = new Set(serverSessions.map((s) => s.session_id));
		const onlyOptimistic = optimisticSessions.filter(
			(s) => !serverIds.has(s.session_id),
		);
		return [...onlyOptimistic, ...serverSessions];
	}, [serverSessions, optimisticSessions]);

	const renameSession = useCallback(async (sessionId: string, title: string) => {
		try {
			await sessionApi.rename(userId, sessionId, title);
			await refresh();
		} catch (e) {
			console.error('重命名失败:', e);
		}
	}, [userId, refresh]);

	const deleteSession = useCallback(async (sessionId: string) => {
		try {
			await sessionApi.delete(userId, sessionId);
			await refresh();
		} catch (e) {
			console.error('删除失败:', e);
		}
	}, [userId, refresh]);

	const forkSession = useCallback(
		async (sessionId: string, branchAfterMessageId?: number | string | null) => {
			try {
				const res = await sessionApi.fork(
					userId,
					sessionId,
					branchAfterMessageId != null ? Number(branchAfterMessageId) : null,
				);
				await refresh();
				return res.session_id;
			} catch (e) {
				console.error('Fork 失败:', e);
				return null;
			}
		},
		[userId, refresh],
	);

	return {
		sessions,
		loading,
		refresh,
		upsertSessionOptimistic,
		renameSession,
		deleteSession,
		forkSession,
	};
}
