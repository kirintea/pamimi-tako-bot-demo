import { cn } from '@/lib/utils';

interface ToggleButtonProps {
	checked: boolean;
	onCheckedChange: (checked: boolean) => void;
	disabled?: boolean;
	className?: string;
}

/** 开关：36×20 r10，knob 16 白；开 #215BEF / 关 #E5E7EB（v2 设计） */
export function ToggleButton({ checked, onCheckedChange, disabled, className }: ToggleButtonProps) {
	return (
		<button
			role="switch"
			aria-checked={checked}
			disabled={disabled}
			onClick={() => onCheckedChange(!checked)}
			className={cn(
				'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full p-0.5',
				'transition-colors duration-200 ease-out',
				'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
				checked ? 'bg-primary' : 'bg-border',
				disabled && 'cursor-default opacity-60',
				className,
			)}
		>
			<span
				className={cn(
					'block h-4 w-4 rounded-full bg-white shadow-sm',
					'transition-transform duration-200 ease-out',
					checked ? 'translate-x-4' : 'translate-x-0',
				)}
			/>
		</button>
	);
}
