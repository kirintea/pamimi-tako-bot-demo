/**
 * 渠道标签共享常量 —— AllSessions / Observe 共用
 */

export const CHANNEL_LABELS: Record<string, string> = {
	web: 'Web',
	wecom: '企业微信',
};

/** 渠道名 → 中文标签；未知渠道原样返回，空值返回空串 */
export function channelLabel(channel?: string | null): string {
	if (!channel) return '';
	return CHANNEL_LABELS[channel] || channel;
}
