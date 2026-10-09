/**
 * AppSidebar — v2 设计板侧边栏（Penpot 规格）
 *
 * 272px 展开 / 56px 折叠；bg #F9FAFB + 右侧 1px 分隔线。
 * 结构：Logo+折叠 → 蓝色「新建对话」→ 搜索 → 工作台导航(5 项)
 *      → 最近会话 → 底部 用户行(r0) + 设置行(r10 含绿点)。
 * 知识库 RAG / 自动化 为后端未实现的占位项（toast 提示）。
 */

import { format } from 'date-fns';
import {
	BookText,
	Cable,
	ChevronDown,
	ChevronRight,
	Crown,
	Database,
	Ellipsis,
	LogIn,
	MessageSquare,
	PanelLeftClose,
	PanelLeftOpen,
	Pencil,
	Plus,
	Search,
	Settings,
	Trash2,
	UserRound,
	Zap,
} from 'lucide-react';
import { type ReactNode, useMemo, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { useRole } from '@/hooks/useRole';
import { wsManager } from '@/api/ws';
import type { SessionInfo } from '@/api/types';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface AppSidebarProps {
	collapsed?: boolean;
	onCollapse?: () => void;
	onExpand?: () => void;
	onNewChat?: () => void;
	/** Session list from ChatPage */
	sessions?: SessionInfo[];
	activeSessionId?: string | null;
	onSelectSession?: (sessionId: string) => void;
	onRenameSession?: (sessionId: string, newTitle: string) => void;
	onDeleteSession?: (sessionId: string) => void;
}

interface NavItemProps {
	icon: ReactNode;
	label: string;
	active?: boolean;
	onClick?: () => void;
	collapsed?: boolean;
	tooltip?: string;
}

function NavItem({ icon, label, active, onClick, collapsed, tooltip }: NavItemProps) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<button
					type="button"
					onClick={onClick}
					className={cn(
						'flex h-[34px] w-full items-center gap-2.5 rounded-[10px] px-3 text-[13px] transition-colors',
						collapsed && 'justify-center px-0',
						active
							? 'bg-primary-light font-semibold text-primary'
							: 'font-normal text-nav-label hover:bg-row-hover',
					)}
				>
					{icon}
					{!collapsed && <span className="truncate">{label}</span>}
				</button>
			</TooltipTrigger>
			{collapsed && tooltip && <TooltipContent side="right">{tooltip}</TooltipContent>}
		</Tooltip>
	);
}

export function AppSidebar({
	collapsed,
	onCollapse,
	onExpand,
	onNewChat,
	sessions = [],
	activeSessionId,
	onSelectSession,
	onRenameSession,
	onDeleteSession,
}: AppSidebarProps) {
	const navigate = useNavigate();
	const location = useLocation();
	const { t } = useTranslation();
	const [showUserDialog, setShowUserDialog] = useState(false);
	const [newUserId, setNewUserId] = useState('');
	const [renameTarget, setRenameTarget] = useState<string | null>(null);
	const [renameValue, setRenameValue] = useState('');
	const [query, setQuery] = useState('');
	const [workbenchOpen, setWorkbenchOpen] = useState(true);
	const currentUserId = wsManager.getUserId();
	const { isRoot } = useRole();

	const handleSwitchUser = () => {
		const trimmed = newUserId.trim();
		if (!trimmed || trimmed === currentUserId) {
			setShowUserDialog(false);
			return;
		}
		localStorage.setItem('user_id', trimmed);
		wsManager.disconnect();
		wsManager.connect({ userId: trimmed, onMessage: () => {} });
		setShowUserDialog(false);
		setNewUserId('');
		window.location.reload();
	};

	const handleRename = () => {
		if (renameTarget && renameValue.trim() && onRenameSession) {
			onRenameSession(renameTarget, renameValue.trim());
			setRenameTarget(null);
			setRenameValue('');
		}
	};

	const isChatActive = location.pathname === '/chat' || location.pathname.startsWith('/chat/');
	const isSettingsActive = location.pathname.startsWith('/settings');

	const filteredSessions = useMemo(() => {
		const q = query.trim().toLowerCase();
		if (!q) return sessions;
		return sessions.filter(
			(s) =>
				(s.title || '').toLowerCase().includes(q) ||
				s.session_id.toLowerCase().includes(q),
		);
	}, [sessions, query]);

	const handlePlaceholderNav = (label: string) => {
		toast.info(t('sidebar.featurePlanned', { defaultValue: `${label} — 即将支持，规划中` }));
	};

	const navItems: Array<{
		key: string;
		icon: ReactNode;
		label: string;
		to?: string;
		placeholder?: boolean;
	}> = [
		{ key: 'chat', icon: <MessageSquare className="size-3.5" />, label: t('common.chat', { defaultValue: '对话' }), to: '/chat' },
		{ key: 'mcp', icon: <Cable className="size-3.5" />, label: t('sidebar.navMcp', { defaultValue: 'MCP 服务' }), to: '/mcp' },
		{ key: 'skill', icon: <BookText className="size-3.5" />, label: t('sidebar.navSkill', { defaultValue: '技能 Skill' }), to: '/skill' },
		{ key: 'rag', icon: <Database className="size-3.5" />, label: t('sidebar.navRag', { defaultValue: '知识库 RAG' }), placeholder: true },
		{ key: 'automation', icon: <Zap className="size-3.5" />, label: t('sidebar.navAutomation', { defaultValue: '自动化' }), placeholder: true },
	];

	const isNavActive = (item: (typeof navItems)[number]) => {
		if (item.placeholder || !item.to) return false;
		if (item.key === 'chat') return isChatActive;
		return location.pathname === item.to;
	};

	return (
		<TooltipProvider>
			{/* ── 展开 ── */}
			{!collapsed ? (
				<aside className="flex w-[272px] shrink-0 flex-col rounded-[22px] border-r border-border bg-canvas px-3 pt-6 pb-7 transition-[width] duration-200 ease-in-out">
					{/* Logo + 折叠 */}
					<div className="flex items-center justify-between px-1">
						<div className="flex size-8 items-center justify-center rounded-[9px] bg-primary">
							<span className="text-[16px] font-bold leading-none text-primary-foreground">智</span>
						</div>
						<Tooltip>
							<TooltipTrigger asChild>
								<Button
									variant="ghost"
									size="icon"
									aria-label={t('sidebar.collapse', { defaultValue: '收起' })}
									onClick={onCollapse}
									className="size-6 text-muted-foreground hover:bg-row-hover hover:text-foreground"
								>
									<PanelLeftClose className="size-4" />
								</Button>
							</TooltipTrigger>
							<TooltipContent side="right">{t('sidebar.collapse', { defaultValue: '收起' })}</TooltipContent>
						</Tooltip>
					</div>

					{/* 新建对话 */}
					<button
						type="button"
						onClick={onNewChat}
						className="mt-4 flex h-[34px] w-full items-center gap-2.5 rounded-[10px] bg-primary-light px-3 text-[13px] font-semibold text-primary transition-colors hover:bg-primary/15"
					>
						<Plus className="size-3.5" />
						<span>{t('sidebar.newChat', { defaultValue: '新建对话' })}</span>
					</button>

					{/* 搜索 */}
					<div className="mt-1.5 flex h-[34px] items-center gap-2.5 px-3">
						<Search className="size-4 shrink-0 text-text-tertiary" />
						<input
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							placeholder={t('sidebar.search', { defaultValue: '搜索' })}
							className="w-full bg-transparent text-[13px] font-normal text-nav-label outline-none placeholder:text-nav-label"
						/>
					</div>

				{/* 工作台（可折叠，root 用户可收起以腾出空间看最近对话） */}
				<button
					type="button"
					onClick={() => setWorkbenchOpen((v) => !v)}
					aria-expanded={workbenchOpen}
					className="mt-4 flex w-full items-center gap-1 px-3 text-[11px] font-semibold text-text-tertiary transition-colors hover:text-foreground"
				>
					{workbenchOpen ? (
						<ChevronDown className="size-3.5 shrink-0" />
					) : (
						<ChevronRight className="size-3.5 shrink-0" />
					)}
					<span>{t('sidebar.groupWorkbench', { defaultValue: '工作台' })}</span>
				</button>
				{workbenchOpen && (
					<nav className="mt-3 flex flex-col gap-0.5">
						{navItems.map((item) => (
							<NavItem
								key={item.key}
								icon={item.icon}
								label={item.label}
								active={isNavActive(item)}
								onClick={() =>
									item.placeholder
										? handlePlaceholderNav(item.label)
										: item.to && navigate(item.to)
								}
							/>
						))}
					</nav>
				)}

				{/* 管理视图（仅 root） */}
				{isRoot && (
					<>
						<div className="mt-6 px-3 text-[11px] font-semibold text-text-tertiary">
							{t('sidebar.groupAdmin', { defaultValue: '管理视图' })}
						</div>
						<nav className="mt-3 flex flex-col gap-0.5">
							<NavItem
								icon={<Crown className="size-3.5" />}
								label={t('sidebar.adminAllSessions', { defaultValue: '全部用户会话' })}
								active={location.pathname.startsWith('/admin/sessions')}
								onClick={() => navigate('/admin/sessions')}
							/>
						</nav>
					</>
				)}

				{/* 最近 */}
					<div className="mt-6 px-3 text-[11px] font-semibold text-text-tertiary">
						{t('sidebar.sessions', { defaultValue: '最近' })}
					</div>
					<div className="mt-3 min-h-0 flex-1 overflow-y-auto">
						{filteredSessions.length === 0 && (
							<div className="px-3 py-2 text-[12px] text-text-tertiary">
								{query
									? t('sidebar.noMatch', { defaultValue: '无匹配会话' })
									: t('sidebar.noSessions', { defaultValue: '暂无会话' })}
							</div>
						)}
						<div className="flex flex-col gap-0.5">
							{filteredSessions.map((session) => {
								const active = activeSessionId === session.session_id;
								return (
									<div key={session.session_id} className="group/session relative">
										<button
											type="button"
											onClick={() => onSelectSession?.(session.session_id)}
											className={cn(
												'flex h-[30px] w-full items-center rounded-[8px] px-3 pr-8 text-left text-[13px] transition-colors',
												active
													? 'bg-primary-light font-medium text-primary'
													: 'font-normal text-nav-label hover:bg-row-hover',
											)}
										>
											<span className="truncate">
												{session.title || session.session_id.slice(0, 8)}
											</span>
										</button>
										<DropdownMenu>
											<DropdownMenuTrigger asChild>
												<button
													type="button"
													aria-label={t('common.more', { defaultValue: '更多' })}
													className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity hover:bg-row-hover group-hover/session:opacity-100"
												>
													<Ellipsis className="size-3.5" />
												</button>
											</DropdownMenuTrigger>
											<DropdownMenuContent side="right" align="start">
												<DropdownMenuItem
													onClick={() => {
														setRenameTarget(session.session_id);
														setRenameValue(session.title || '');
													}}
												>
													<Pencil />
													{t('sidebar.rename', { defaultValue: '重命名' })}
												</DropdownMenuItem>
												<DropdownMenuItem
													variant="destructive"
													onClick={() => onDeleteSession?.(session.session_id)}
												>
													<Trash2 />
													{t('sidebar.delete', { defaultValue: '删除' })}
												</DropdownMenuItem>
											</DropdownMenuContent>
										</DropdownMenu>
									</div>
								);
							})}
						</div>
					</div>

					{/* 底部：用户行 r0 + 设置行 r10 */}
					<div className="mt-4 flex flex-col gap-0.5">
						<button
							type="button"
							onClick={() => {
								setNewUserId(currentUserId || '');
								setShowUserDialog(true);
							}}
							title={currentUserId || undefined}
							className="flex h-[34px] w-full items-center gap-2.5 rounded-[10px] bg-secondary px-3 text-[13px] font-normal text-nav-label transition-colors hover:bg-secondary/70"
						>
							<UserRound className="size-3.5" />
							<span className="truncate">{currentUserId || t('sidebar.user', { defaultValue: '用户' })}</span>
							{isRoot && (
								<span className="ml-auto rounded-[4px] bg-primary px-1.5 py-0.5 text-[10px] font-bold leading-none text-primary-foreground">
									ROOT
								</span>
							)}
						</button>
						<button
							type="button"
							onClick={() => navigate('/settings')}
							className={cn(
								'flex h-[34px] w-full items-center gap-2.5 rounded-[10px] bg-secondary px-3 text-[13px] transition-colors',
								isSettingsActive
									? 'bg-primary-light font-semibold text-primary'
									: 'font-normal text-nav-label hover:bg-secondary/70',
							)}
						>
							<Settings className="size-3.5" />
							<span>{t('common.settings', { defaultValue: '设置' })}</span>
							<span
								aria-hidden
								className="ml-auto mr-1 size-2 rounded-full bg-success"
								title={t('sidebar.serviceStatus', { defaultValue: '服务在线' })}
							/>
						</button>
					</div>
				</aside>
			) : (
				/* ── 折叠 icon-only ── */
				<aside className="flex w-14 shrink-0 flex-col items-center rounded-[22px] border-r border-border bg-canvas pt-6 pb-7 transition-[width] duration-200 ease-in-out">
					<div className="flex size-8 items-center justify-center rounded-[9px] bg-primary">
						<span className="text-[16px] font-bold leading-none text-primary-foreground">智</span>
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label={t('sidebar.expand', { defaultValue: '展开' })}
						onClick={onExpand}
						className="mt-3 size-6 text-muted-foreground hover:bg-row-hover hover:text-foreground"
					>
						<PanelLeftOpen className="size-4" />
					</Button>
					<Tooltip>
						<TooltipTrigger asChild>
							<button
								type="button"
								aria-label={t('sidebar.newChat', { defaultValue: '新建对话' })}
								onClick={onNewChat}
								className="mt-4 flex size-[34px] items-center justify-center rounded-[10px] bg-primary-light text-primary transition-colors hover:bg-primary/15"
							>
								<Plus className="size-4" />
							</button>
						</TooltipTrigger>
						<TooltipContent side="right">{t('sidebar.newChat', { defaultValue: '新建对话' })}</TooltipContent>
					</Tooltip>
					<nav className="mt-4 flex w-full flex-col items-center gap-0.5">
						{navItems.map((item) => (
							<NavItem
								key={item.key}
								icon={item.icon}
								label={item.label}
								active={isNavActive(item)}
								onClick={() =>
									item.placeholder
										? handlePlaceholderNav(item.label)
										: item.to && navigate(item.to)
								}
								collapsed
								tooltip={item.label}
							/>
						))}
				</nav>
				{isRoot && (
					<Tooltip>
						<TooltipTrigger asChild>
							<button
								type="button"
								aria-label={t('sidebar.adminAllSessions', { defaultValue: '全部用户会话' })}
								onClick={() => navigate('/admin/sessions')}
								className={cn(
									'flex size-[34px] items-center justify-center rounded-[10px]',
									location.pathname.startsWith('/admin/sessions')
										? 'bg-primary-light text-primary'
										: 'bg-secondary text-nav-label hover:bg-secondary/70',
								)}
							>
								<Crown className="size-4" />
							</button>
						</TooltipTrigger>
						<TooltipContent side="right">{t('sidebar.adminAllSessions', { defaultValue: '全部用户会话' })}</TooltipContent>
					</Tooltip>
				)}
				<div className="mt-auto flex flex-col items-center gap-0.5">
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => {
										setNewUserId(currentUserId || '');
										setShowUserDialog(true);
									}}
									className="flex size-[34px] items-center justify-center rounded-[10px] bg-secondary text-nav-label"
								>
									<UserRound className="size-3.5" />
								</button>
							</TooltipTrigger>
							<TooltipContent side="right">{currentUserId || '用户'}</TooltipContent>
						</Tooltip>
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => navigate('/settings')}
									className={cn(
										'flex size-[34px] items-center justify-center rounded-[10px] bg-secondary text-nav-label',
										isSettingsActive && 'bg-primary-light text-primary',
									)}
								>
									<Settings className="size-3.5" />
								</button>
							</TooltipTrigger>
							<TooltipContent side="right">{t('common.settings', { defaultValue: '设置' })}</TooltipContent>
						</Tooltip>
					</div>
				</aside>
			)}

			{/* User switch dialog */}
			{showUserDialog && (
				<div
					className="fixed inset-0 bg-[#111928]/50 flex items-center justify-center z-50"
					onClick={() => setShowUserDialog(false)}
				>
					<div
						className="bg-card rounded-[22px] p-6 w-80 shadow-panel"
						onClick={(e) => e.stopPropagation()}
					>
						<h3 className="text-sm font-medium mb-2">{t('sidebar.switchUser', { defaultValue: '切换用户' })}</h3>
						<p className="text-xs text-muted-foreground mb-4">
							{t('sidebar.currentUser', { defaultValue: '当前用户' })}: <span className="font-mono">{currentUserId}</span>
						</p>
						<Input
							value={newUserId}
							onChange={(e) => setNewUserId(e.target.value)}
							placeholder={t('sidebar.newUserPlaceholder', { defaultValue: '输入新用户 ID' })}
							onKeyDown={(e) => e.key === 'Enter' && handleSwitchUser()}
							autoFocus
							className="rounded-[8px]"
						/>
						<div className="flex justify-end gap-2 mt-4">
							<Button variant="outline" size="sm" className="rounded-[8px]" onClick={() => setShowUserDialog(false)}>
								{t('common.cancel', { defaultValue: '取消' })}
							</Button>
							<Button size="sm" className="rounded-[8px]" onClick={handleSwitchUser} disabled={!newUserId.trim()}>
								<LogIn className="size-3.5" />
								{t('sidebar.switch', { defaultValue: '切换' })}
							</Button>
						</div>
					</div>
				</div>
			)}

			{/* Rename dialog */}
			{renameTarget && (
				<div
					className="fixed inset-0 bg-[#111928]/50 flex items-center justify-center z-50"
					onClick={() => setRenameTarget(null)}
				>
					<div
						className="bg-card rounded-[22px] p-6 w-80 shadow-panel"
						onClick={(e) => e.stopPropagation()}
					>
						<h3 className="text-sm font-medium mb-4">{t('sidebar.renameSession', { defaultValue: '重命名会话' })}</h3>
						<Input
							value={renameValue}
							onChange={(e) => setRenameValue(e.target.value)}
							placeholder={t('sidebar.renamePlaceholder', { defaultValue: '输入新名称' })}
							onKeyDown={(e) => e.key === 'Enter' && handleRename()}
							autoFocus
							className="rounded-[8px]"
						/>
						<div className="flex justify-end gap-2 mt-4">
							<Button variant="outline" size="sm" className="rounded-[8px]" onClick={() => setRenameTarget(null)}>
								{t('common.cancel', { defaultValue: '取消' })}
							</Button>
							<Button size="sm" className="rounded-[8px]" onClick={handleRename} disabled={!renameValue.trim()}>
								{t('common.confirm', { defaultValue: '确认' })}
							</Button>
						</div>
					</div>
				</div>
			)}
		</TooltipProvider>
	);
}
