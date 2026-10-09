/**
 * 会话 API
 */

import { client } from './client';
import type { SessionListResponse, MessagesResponse, AdminSessionsResponse, MeResponse } from './types';

export const sessionApi = {
	/** 列出用户历史会话 */
	list: (userId: string) =>
		client.get<SessionListResponse>(`/sessions/${encodeURIComponent(userId)}`),

	/** 获取会话消息历史
	 *  @param params.viewer 发起查看的用户（查询参数）；缺省视为本人。
	 *        非本人访问需为 root（只读查看他人会话，见方案 §8.6）。
	 *        前端对本人会话始终传 viewer=当前 userId，以硬化越权闸。
	 */
	messages: (
		userId: string,
		sessionId: string,
		params?: { before_id?: number; limit?: number; viewer?: string },
	) =>
		client.get<MessagesResponse>(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/messages`,
			Object.fromEntries(
				Object.entries(params ?? {})
					.filter(([, v]) => v != null)
					.map(([k, v]) => [k, String(v)]),
			),
		),

	/** root 管理视图：列出所有用户的全部会话（跨用户扫描） */
	listAllSessions: (viewer: string) =>
		client.get<AdminSessionsResponse>(
			`/admin/sessions?viewer=${encodeURIComponent(viewer)}`,
		),

	/** 查询当前用户身份与角色（前端据此判断是否展示 root 管理视图） */
	getMe: (userId: string) =>
		client.get<MeResponse>(`/me?user_id=${encodeURIComponent(userId)}`),

	/** 重命名会话 */
	rename: (userId: string, sessionId: string, title: string) =>
		client.post(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/rename`,
			{ title },
		),

	/** 删除会话 */
	delete: (userId: string, sessionId: string) =>
		client.post(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/delete`,
		),

	/** Fork 会话
	 * @param branchAfterMessageId 中途 fork 的截断点消息 ID（包含该消息）；缺省复制全部历史
	 */
	fork: (userId: string, sessionId: string, branchAfterMessageId?: number | null) =>
		client.post<{ session_id: string; parent_session_id: string; title: string }>(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/fork`,
			{ branch_after_message_id: branchAfterMessageId ?? null },
		),
};
