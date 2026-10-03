import { cn } from '@/lib/utils';

interface SettingsRowProps {
	label: string;
	description?: string;
	children?: React.ReactNode;
	className?: string;
	hoverable?: boolean;
}

/** 字段行：label 13/400 #4B5563 左，控件右；min-h 64（64px 行距） */
export function SettingsRow({ label, description, children, className, hoverable = false }: SettingsRowProps) {
	return (
		<div
			className={cn(
				'flex min-h-[64px] items-center justify-between gap-6 px-0 py-3',
				hoverable && 'rounded-[8px] transition-colors hover:bg-[#F9FAFB]',
				className,
			)}
		>
			<div className="min-w-0">
				<div className="text-[13px] font-normal leading-5 text-nav-label">{label}</div>
				{description && (
					<div className="mt-0.5 text-[12px] leading-4 text-[#6B7280]">{description}</div>
				)}
			</div>
			{children && <div className="flex shrink-0 items-center">{children}</div>}
		</div>
	);
}

interface ReadOnlyRowProps {
	label: string;
	value: string;
	className?: string;
}

export function ReadOnlyRow({ label, value, className }: ReadOnlyRowProps) {
	return (
		<div className={cn('flex min-h-[64px] items-center justify-between gap-6 px-0 py-3', className)}>
			<div className="min-w-0 text-[13px] font-normal leading-5 text-nav-label">{label}</div>
			<div className="max-w-[360px]">
				<span className="truncate text-[13px] text-[#6B7280]">{value}</span>
			</div>
		</div>
	);
}
