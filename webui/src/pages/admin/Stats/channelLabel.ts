/**
 * 渠道显示名解析 —— i18n 优先，后端 label 兜底，最后回落原值
 *
 * 需求备注：中文下 wecom 必须显示「企业微信」（zh.json stats.channelNames.wecom）。
 */

import { useTranslation } from '@/i18n/useI18n';

/** ''（未知桶）→ unknown；未登记渠道回落 API label / 原值 */
export function useChannelLabel(): (channel: string, fallback?: string) => string {
	const { t } = useTranslation();
	return (channel: string, fallback?: string) => {
		const key = channel === '' ? 'unknown' : channel;
		const translated = t(`stats.channelNames.${key}`, { defaultValue: '' });
		if (translated) return translated;
		return fallback ?? (channel === '' ? '未知' : channel);
	};
}
