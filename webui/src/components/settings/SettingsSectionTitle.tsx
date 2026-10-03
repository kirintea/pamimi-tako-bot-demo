import { cn } from '@/lib/utils';

interface SettingsSectionTitleProps {
	children: React.ReactNode;
	className?: string;
}

export function SettingsSectionTitle({ children, className }: SettingsSectionTitleProps) {
	return (
		<h3 className={cn('settings-section-title', className)}>
			{children}
		</h3>
	);
}