import { cn } from '@/lib/utils';

export type StatusTone = 'success' | 'warning' | 'neutral' | 'error';

interface StatusPillProps {
	tone: StatusTone;
	children: React.ReactNode;
	className?: string;
}

const toneClasses: Record<StatusTone, string> = {
	success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
	warning: 'bg-amber-500/10 text-amber-700 dark:text-amber-400',
	neutral: 'bg-muted text-muted-foreground',
	error: 'bg-red-500/10 text-red-700 dark:text-red-400',
};

export function StatusPill({ tone, children, className }: StatusPillProps) {
	return (
		<span className={cn(
			'rounded-full px-2.5 py-1 text-[12px] font-medium whitespace-nowrap',
			toneClasses[tone],
			className,
		)}>
			{children}
		</span>
	);
}