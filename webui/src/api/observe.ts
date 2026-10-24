/**
 * 对话观测台 API —— 会话轮次 + 节点树（root 专属）
 */

import { client } from './client';
import type { ObserveTurnsResponse } from './types';

export const observeApi = {
	/** 轮次 + 节点树（最近 limit 轮） */
	turns: (userId: string, sessionId: string, viewer: string, limit = 50) =>
		client.get<ObserveTurnsResponse>(
			`/admin/observe/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/turns`,
			{ viewer, limit: String(limit) },
		),
};
