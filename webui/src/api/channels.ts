/**
 * Channel API
 *
 * 后端 GET /channels 返回 { channels, total }（ChannelListResponse），
 * 此处解包为数组；user_id 默认取本地登录身份。
 * 另含 start/stop/status/manifests 等生命周期与元数据端点。
 */

import { client } from './client';
import type {
	ChannelInfo,
	ChannelListResponse,
	ChannelManifest,
	ChannelStatusResponse,
	CreateChannelRequest,
	UpdateChannelRequest,
} from './types';

function defaultUserId(): string {
	try {
		return localStorage.getItem('user_id') || 'anonymous';
	} catch {
		return 'anonymous';
	}
}

const withUser = (userId?: string) =>
	`user_id=${encodeURIComponent(userId ?? defaultUserId())}`;

export const channelApi = {
	/** 列出当前用户的渠道（解包 {channels,total} → ChannelInfo[]） */
	list: (userId?: string) =>
		client
			.get<ChannelListResponse>('/channels', { user_id: userId ?? defaultUserId() })
			.then((res) => res.channels),

	/** 新增渠道 */
	create: (data: CreateChannelRequest, userId?: string) =>
		client.post<ChannelInfo>(`/channels?${withUser(userId)}`, data),

	/** 更新渠道（改名 / 启用禁用 / 配置覆盖） */
	update: (id: string, data: UpdateChannelRequest, userId?: string) =>
		client.patch<ChannelInfo>(
			`/channels/${encodeURIComponent(id)}?${withUser(userId)}`,
			data,
		),

	/** 删除渠道 */
	remove: (id: string, userId?: string) =>
		client.delete(`/channels/${encodeURIComponent(id)}?${withUser(userId)}`),

	/** 热启动单个渠道 */
	start: (id: string, userId?: string) =>
		client.post<ChannelInfo>(
			`/channels/${encodeURIComponent(id)}/start?${withUser(userId)}`,
		),

	/** 热停止单个渠道 */
	stop: (id: string, userId?: string) =>
		client.post<ChannelInfo>(
			`/channels/${encodeURIComponent(id)}/stop?${withUser(userId)}`,
		),

	/** 运行态快照 */
	status: (userId?: string) =>
		client.get<ChannelStatusResponse>('/channels/status', {
			user_id: userId ?? defaultUserId(),
		}),

	/** 渠道元数据（前端渲染添加表单 + 依赖可用性指示灯） */
	manifests: () =>
		client
			.get<{ manifests: ChannelManifest[] }>('/channels/manifests')
			.then((res) => res.manifests),
};
