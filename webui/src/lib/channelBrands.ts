// 渠道品牌资源：type -> { brand 主色, svg 内联矢量 }。
// svg 从 assets/svg/{type}.svg 读取（?raw 打包进产物，文件名即 type 键），
// 尺寸 100% 充满容器；单色图标注入 currentColor 以用 brand 着色，多色图标保留自身颜色。
// 仅前端展示用，与后端 GET /channels/manifests 的 type 键对应。

import dingtalkSvg from '@/assets/svg/dingtalk.svg?raw';
import discordSvg from '@/assets/svg/discord.svg?raw';
import emailSvg from '@/assets/svg/email.svg?raw';
import feishuSvg from '@/assets/svg/feishu.svg?raw';
import matrixSvg from '@/assets/svg/matrix.svg?raw';
import mattermostSvg from '@/assets/svg/mattermost.svg?raw';
import msteamsSvg from '@/assets/svg/msteams.svg?raw';
import qqSvg from '@/assets/svg/qq.svg?raw';
import signalSvg from '@/assets/svg/signal.svg?raw';
import slackSvg from '@/assets/svg/slack.svg?raw';
import telegramSvg from '@/assets/svg/telegram.svg?raw';
import webSvg from '@/assets/svg/web.svg?raw';
import websocketSvg from '@/assets/svg/websocket.svg?raw';
import wechatSvg from '@/assets/svg/wechat.svg?raw';
import wecomSvg from '@/assets/svg/wecom.svg?raw';
import whatsappSvg from '@/assets/svg/whatsapp.svg?raw';

export interface ChannelBrand {
	brand: string;
	svg: string;
}

export const CHANNEL_BRANDS: Record<string, ChannelBrand> = {
	wecom: { brand: '#2F7DE1', svg: wecomSvg },
	feishu: { brand: '#3370FF', svg: feishuSvg },
	dingtalk: { brand: '#1890FF', svg: dingtalkSvg },
	discord: { brand: '#5865F2', svg: discordSvg },
	telegram: { brand: '#229ED9', svg: telegramSvg },
	slack: { brand: '#4A154B', svg: slackSvg },
	msteams: { brand: '#5059C9', svg: msteamsSvg },
	mattermost: { brand: '#0058CC', svg: mattermostSvg },
	matrix: { brand: '#0DBD8B', svg: matrixSvg },
	qq: { brand: '#12B7F5', svg: qqSvg },
	signal: { brand: '#3a76f0', svg: signalSvg },
	email: { brand: '#6b7280', svg: emailSvg },
	whatsapp: { brand: '#25D366', svg: whatsappSvg },
	wechat: { brand: '#07C160', svg: wechatSvg },
	web: { brand: '#1296db', svg: webSvg },
	websocket: { brand: '#00C188', svg: websocketSvg },
};

export function getChannelBrand(type: string): ChannelBrand | undefined {
	return CHANNEL_BRANDS[type];
}
