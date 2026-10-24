/**
 * 渠道品牌 logo —— 渠道设置页 / 运营看板共用
 *
 * 有品牌资源时内联 SVG：单色图标随 brand 色着色，多色图标保留自身颜色；
 * 无资源时渲染两字母缩写徽标。资源表见 lib/channelBrands。
 */

import { getChannelBrand } from '@/lib/channelBrands';
import { cn } from '@/lib/utils';

export function ChannelBrandLogo({
	type,
	size = 22,
	className,
}: {
	type: string;
	size?: number;
	className?: string;
}) {
	const b = getChannelBrand(type);
	if (!b) {
		return (
			<div
				style={{ width: size, height: size }}
				className="flex shrink-0 items-center justify-center rounded-md bg-muted text-[9px] font-semibold text-muted-foreground"
			>
				{(type || '?').slice(0, 2).toUpperCase()}
			</div>
		);
	}
	return (
		<div
			style={{ width: size, height: size, color: b.brand }}
			className={cn('flex shrink-0 items-center justify-center [&>svg]:h-full [&>svg]:w-full', className)}
			dangerouslySetInnerHTML={{ __html: b.svg }}
		/>
	);
}
