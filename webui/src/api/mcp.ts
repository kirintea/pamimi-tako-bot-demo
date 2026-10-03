/**
 * MCP API
 *
 * 后端 GET /mcp 返回 { mcps, total }（ListMCPsResponse），
 * 此处解包为数组；user_id 默认取本地登录身份。
 */

import { client } from './client';
import type { McpInfo, McpListResponse, CreateMcpRequest } from './types';

function defaultUserId(): string {
	try {
		return localStorage.getItem('user_id') || 'anonymous';
	} catch {
		return 'anonymous';
	}
}

export const mcpApi = {
	/** 列出已安装 MCP（解包 {mcps,total} → McpInfo[]） */
	list: (userId?: string) =>
		client
			.get<McpListResponse>('/mcp', { user_id: userId ?? defaultUserId() })
			.then((res) => res.mcps),

	/** 添加 MCP */
	create: (data: CreateMcpRequest, userId?: string) =>
		client.post<McpInfo>(
			`/mcp?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
			data,
		),

	/** 更新 MCP（启用/禁用、改名、描述） */
	update: (id: string, data: Partial<McpInfo>, userId?: string) =>
		client.patch<McpInfo>(
			`/mcp/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
			data,
		),

	/** 删除 MCP */
	delete: (id: string, userId?: string) =>
		client.delete(
			`/mcp/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
		),
};
