/**
 * 渠道类归类 —— 运营看板（D3）
 *
 * web-like（类 Web）为默认模板类：`web` + 未登记渠道（经 web API 自报的
 * 第三方渠道，如 partner_a）。类仅是展示层分组：显示名仍按具体 channel
 * 值区分（useChannelLabel 链路不动），统计口径 / 后端 channel_options 零改动。
 */

import { useTranslation } from '@/i18n/useI18n';
import { getChannelBrand } from '@/lib/channelBrands';

export type ChannelCategoryKey = 'web-like' | 'im' | 'unknown';

/** 类名兜底（i18n 缺键时用；镜像 channelLabel.ts 的回落风格） */
export const CHANNEL_CATEGORY_FALLBACK: Record<ChannelCategoryKey, string> = {
	'web-like': '类 Web',
	im: 'IM 渠道',
	unknown: '未知',
};

/** 已登记的非 web-like 渠道；未登记且非空 → web-like（默认类） */
const CATEGORY_BY_CHANNEL: Record<string, ChannelCategoryKey> = {
	wecom: 'im',
	wechat: 'im',
	feishu: 'im',
	dingtalk: 'im',
	qq: 'im',
};

/** 分组小节头顺序：模板类在前 */
export const CHANNEL_CATEGORY_ORDER: ChannelCategoryKey[] = [
	'web-like',
	'im',
	'unknown',
];

export function channelCategory(channel: string): ChannelCategoryKey {
	if (!channel) return 'unknown';
	return CATEGORY_BY_CHANNEL[channel] ?? 'web-like';
}

/**
 * 类模板 logo：有品牌资源用自身；web-like 类无资源 → 类模板 `web` 图标
 * （具体渠道靠显示名区分，见 useChannelLabel）。
 */
export function channelLogoType(channel: string): string {
	if (!channel) return channel;
	if (getChannelBrand(channel)) return channel;
	return channelCategory(channel) === 'web-like' ? 'web' : channel;
}

/** 类名 hook —— i18n 优先，CHANNEL_CATEGORY_FALLBACK 兜底 */
export function useChannelCategoryLabel(): (category: ChannelCategoryKey) => string {
	const { t } = useTranslation();
	return (category: ChannelCategoryKey) =>
		t(`stats.channelCategories.${category}`, {
			defaultValue: CHANNEL_CATEGORY_FALLBACK[category],
		});
}
