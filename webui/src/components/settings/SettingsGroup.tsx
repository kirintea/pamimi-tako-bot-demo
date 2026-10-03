import { cn } from '@/lib/utils';

interface SettingsGroupProps {
	children: React.ReactNode;
	className?: string;
}

/** 行分组：透明底 + 行间 1px #F3F4F6 分隔线（卡片内嵌） */
export function SettingsGroup({ children, className }: SettingsGroupProps) {
	return (
		<div className={cn('overflow-hidden rounded-[12px] divide-y divide-[#F3F4F6]', className)}>
			{children}
		</div>
	);
}
