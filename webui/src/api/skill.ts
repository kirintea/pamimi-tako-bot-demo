/**
 * Skill API
 *
 * 后端 GET /skill 返回 { skills, total }（ListSkillsResponse），
 * 此处解包为数组；user_id 默认取本地登录身份。
 */

import { client } from './client';
import type {
	SkillInfo,
	SkillListResponse,
	CreateSkillRequest,
	UpdateSkillRequest,
} from './types';

function defaultUserId(): string {
	try {
		return localStorage.getItem('user_id') || 'anonymous';
	} catch {
		return 'anonymous';
	}
}

export const skillApi = {
	/** 列出已安装 Skill（解包 {skills,total} → SkillInfo[]） */
	list: (userId?: string) =>
		client
			.get<SkillListResponse>('/skill', { user_id: userId ?? defaultUserId() })
			.then((res) => res.skills),

	/** 获取单个 Skill */
	get: (id: string, userId?: string) =>
		client.get<SkillInfo>(
			`/skill/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
		),

	/** 添加 Skill */
	create: (data: CreateSkillRequest, userId?: string) =>
		client.post<SkillInfo>(
			`/skill?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
			data,
		),

	/** 更新 Skill（启用/禁用、改名、描述） */
	update: (id: string, data: UpdateSkillRequest, userId?: string) =>
		client.patch<SkillInfo>(
			`/skill/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
			data,
		),

	/** 删除 Skill */
	delete: (id: string, userId?: string) =>
		client.delete(
			`/skill/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId ?? defaultUserId())}`,
		),
};
