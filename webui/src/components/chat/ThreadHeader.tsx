/**
 * ThreadHeader — 聊天视口右上角工具栏（Penpot「💬 Chat 主对话页 v2」）
 *
 * 左侧：会话ID 展示（点击复制）；右侧 3 × 28×28 图标按钮（gap 10）：
 * 连接状态点 + PromptNavigator + 主题切换。
 * 根 pointer-events-none 悬浮层；控件组 pointer-events-auto。
 * （[&_button] 规则会把按钮缩掉 —— 连接状态必须用 span）
 */

import { Check, Copy, Moon, Sun } from 'lucide-react';
import { type ReactNode, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface ThreadHeaderProps {
	connectionStatus: 'connected' | 'connecting' | 'disconnected';
	promptNavigatorAction?: ReactNode;
	/** 当前会话 ID — 左上角展示，点击复制（新会话尚未分配时不展示） */
	sessionId?: string;
}

type Theme = 'light' | 'dark';

function readTheme(): Theme {
	const stored = localStorage.getItem('theme');
	if (stored === 'light' || stored === 'dark') return stored;
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function ThreadHeader({ connectionStatus, promptNavigatorAction, sessionId }: ThreadHeaderProps) {
	const { t } = useTranslation();
	const [theme, setTheme] = useState<Theme>(() => readTheme());
	const [copied, setCopied] = useState(false);

	const handleCopySessionId = useCallback(() => {
		if (!sessionId) return;
		navigator.clipboard.writeText(sessionId).then(() => {
			setCopied(true);
			setTimeout(() => setCopied(false), 1500);
		});
	}, [sessionId]);

	// 外部（设置页）改主题时同步
	useEffect(() => {
		const sync = () => setTheme(readTheme());
		window.addEventListener('storage', sync);
		const media = window.matchMedia('(prefers-color-scheme: dark)');
		media.addEventListener('change', sync);
		return () => {
			window.removeEventListener('storage', sync);
			media.removeEventListener('change', sync);
		};
	}, []);

	const handleToggleTheme = useCallback(() => {
		setTheme((prev) => {
			const next: Theme = prev === 'dark' ? 'light' : 'dark';
			localStorage.setItem('theme', next);
			document.documentElement.classList.toggle('dark', next === 'dark');
			return next;
		});
	}, []);

	const statusLabel =
		connectionStatus === 'connected'
			? t('chat.connected')
			: connectionStatus === 'connecting'
				? t('chat.connecting')
				: t('chat.disconnected');

	return (
		<div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex shrink-0 items-center justify-between px-9 py-6">
			{/* 左侧：会话ID（点击复制） */}
			{sessionId && (
				<div className="pointer-events-auto flex min-w-0 items-center gap-1.5">
					<span className="shrink-0 text-xs text-muted-foreground">{t('chat.header.sessionId')}:</span>
					<button
						type="button"
						onClick={handleCopySessionId}
						title={copied ? t('chat.header.sessionIdCopied') : t('chat.header.copySessionId')}
						className="flex min-w-0 items-center gap-1 rounded-compact px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-row-hover hover:text-foreground"
					>
						<span className="truncate font-mono">{sessionId}</span>
						{copied ? (
							<Check className="size-3 shrink-0 text-green-500" aria-hidden />
						) : (
							<Copy className="size-3 shrink-0" aria-hidden />
						)}
					</button>
				</div>
			)}
			<div className="pointer-events-auto flex items-center gap-2.5">
				{/* 连接状态（span！） */}
				<span
					className="flex size-7 shrink-0 cursor-default items-center justify-center"
					title={statusLabel}
				>
					<span
						aria-hidden
						className={cn(
							'block size-2 shrink-0 rounded-full',
							connectionStatus === 'connected' && 'bg-success',
							connectionStatus === 'connecting' && 'bg-warning',
							connectionStatus === 'disconnected' && 'bg-error',
						)}
					/>
				</span>
				{promptNavigatorAction}
				<Button
					type="button"
					variant="ghost"
					size="icon"
					aria-label={t('chat.header.toggleTheme')}
					onClick={handleToggleTheme}
					className="size-7 rounded-compact text-muted-foreground hover:bg-row-hover hover:text-foreground"
				>
					{theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
				</Button>
			</div>
		</div>
	);
}
