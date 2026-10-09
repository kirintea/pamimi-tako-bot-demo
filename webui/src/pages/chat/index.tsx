/**
 * 对话主页面 — 新的 结构
 *
 * 布局：绝对定位 ThreadHeader 悬浮层 + 三行 CSS Grid 视口
 *   row1 = 消息滚动区（thread）/ HeroGreeting 空状态（hero）
 *   row2 = composer dock（max-w-[58rem] 居中）
 *   row3 = spacer
 * 左侧 PromptRail 标记栏 + 右侧 PromptNavigator Sheet。
 * 自动滚动用 stickToBottomRef 门控（距底 <80px 才跟随）。
 */

import { ArrowDown } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { wsManager } from '@/api/ws';
import { ChatInput, type ChatInputHandle } from '@/components/chat/ChatInput';
import { ContextIndicator } from '@/components/chat/ContextIndicator';
import { HeroGreeting } from '@/components/chat/HeroGreeting';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { PromptNavigator } from '@/components/chat/PromptNavigator';
import { PromptRail } from '@/components/chat/PromptRail';
import { findPromptElement, promptTop } from '@/components/chat/promptNavigation';
import { ThreadHeader } from '@/components/chat/ThreadHeader';
import { Spinner } from '@/components/ui/spinner';
import { useMessages } from '@/hooks/useMessages';
import { useSessions } from '@/hooks/useSessions';
import { cn } from '@/lib/utils';

/** 距底小于该值视为"贴底"，跟随流式输出 */
const STICK_THRESHOLD_PX = 80;

export function ChatPage() {
	const navigate = useNavigate();
	const { sessionId: urlSessionId } = useParams<{ sessionId?: string }>();
	const userId = wsManager.getUserId();
	const { t } = useTranslation();

	const {
		sessions,
		loading: sessionsLoading,
		refresh: refreshSessions,
		forkSession,
	} = useSessions(userId);

	// 从 AppLayout Outlet context 获取侧栏刷新函数、乐观插入函数与「新建会话」计数器
	// （AppLayout 的 useSessions 实例与 ChatPage 的独立，必须通过 context 同步）
	const {
		refreshSessions: refreshSidebar,
		newChatNonce,
		upsertSessionOptimistic,
	} = useOutletContext<{
		refreshSessions: () => Promise<void>;
		newChatNonce: number;
		upsertSessionOptimistic: (session: {
			session_id: string;
			user_id: string;
			title: string;
			created_at: number;
			last_active: number;
			message_count: number;
		}) => void;
	}>();

	const {
		messages,
		phase,
		connectionStatus,
		resolvedSessionId,
		sendMessage,
		cancelGeneration,
	} = useMessages(userId, urlSessionId ?? null, newChatNonce);

	// 始终持有最新 messages 的 ref，供 effect 读取而不必加入依赖（避免每个 token 都重跑 effect）
	const messagesRef = useRef(messages);
	messagesRef.current = messages;

	// 注意：switchSession effect 已移除 — useMessages 主 effect（dep 含 sessionId）
	// 已覆盖会话切换的全部逻辑（清空消息 / connect WS / loadHistory），
	// 保留此 effect 会导致双 loadHistory 竞态 + 双 WS 重连，fork 导航后闪白屏。

	// 新会话首次发消息后：立即将新会话乐观插入「最近」列表（不等后端落库），
	// 并把 URL 从 /chat 提升到 /chat/{resolvedSessionId}（刷新后仍可回到本会话、fork 依赖的 id 有值）。
	// 仅在有消息后才执行，避免无消息时产生幽灵 URL。
	const navigatedRef = useRef<string | null>(null);
	const optimisticRef = useRef<string | null>(null);
	const [pendingUpgrade, setPendingUpgrade] = useState(false);

	// 在 /chat（无 sessionId）下发送首条消息时，标记需将 URL 升级为 /chat/{resolvedSessionId}。
	// 用 state 而非 ref，确保赋值后触发本 effect 重新执行。
	// 修复「点一次新建会话仍停留在历史会话」：旧逻辑在切换瞬间用陈旧的 resolvedSessionId +
	// 尚未清空的历史消息误导航回 /chat/{旧会话}，第二下点击才生效。
	const handleSend = useCallback(
		(content: Parameters<typeof sendMessage>[0]) => {
			if (!urlSessionId) setPendingUpgrade(true);
			sendMessage(content);
		},
		[urlSessionId, sendMessage],
	);

	useEffect(() => {
		if (
			pendingUpgrade &&
			!urlSessionId &&
			resolvedSessionId &&
			navigatedRef.current !== resolvedSessionId
		) {
			navigatedRef.current = resolvedSessionId;
			setPendingUpgrade(false);

			// 乐观插入「最近」：首条消息后即出现，无需等待 reply_end 落库
			if (optimisticRef.current !== resolvedSessionId) {
				optimisticRef.current = resolvedSessionId;
				const firstUser = messagesRef.current.find((m) => m.role === 'user');
				const rawTitle = firstUser
					? (typeof firstUser.content === 'string' ? firstUser.content : '')
					: '';
				const title = rawTitle.trim().slice(0, 30) || '新对话';
				upsertSessionOptimistic({
					session_id: resolvedSessionId,
					user_id: userId,
					title,
					created_at: Math.floor((firstUser?.createdAt ?? Date.now()) / 1000),
					last_active: Math.floor((firstUser?.createdAt ?? Date.now()) / 1000),
					message_count: 0,
				});
			}

			navigate(`/chat/${resolvedSessionId}`, { replace: true });
		}
	}, [pendingUpgrade, urlSessionId, resolvedSessionId, navigate, upsertSessionOptimistic, userId]);

	/**
	 * 有效 sessionId（URL 优先，无 URL 时用 WS 分配的真实 id）；
	 * 确保在 /chat（无 sessionId）路径下 fork / 上下文进度条仍能拿到真实会话 id。
	 */
	const activeSessionId = urlSessionId ?? resolvedSessionId;

	const handleFork = useCallback(
		async (branchAfterMessageId?: number | string | null) => {
			if (!activeSessionId) return;
			const newSessionId = await forkSession(activeSessionId, branchAfterMessageId);
			if (newSessionId) {
				// 刷新 AppLayout 侧栏（useSessions 实例独立，必须通过 context 同步）
				await refreshSidebar();
				navigate(`/chat/${newSessionId}`);
			}
		},
		[activeSessionId, forkSession, navigate, refreshSidebar],
	);

	const hasMessages = messages.length > 0;

	// ---- 滚动：贴底门控 ----
	const scrollRef = useRef<HTMLDivElement>(null);
	const stickToBottomRef = useRef(true);
	const [showScrollButton, setShowScrollButton] = useState(false);

	const handleScroll = useCallback(() => {
		const el = scrollRef.current;
		if (!el) return;
		const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
		const stick = distance < STICK_THRESHOLD_PX;
		stickToBottomRef.current = stick;
		setShowScrollButton(!stick);
	}, []);

	// 新消息到达且贴底时滚到底（上滚后自动停止跟随）
	useEffect(() => {
		if (!hasMessages) return;
		if (!stickToBottomRef.current) return;
		const el = scrollRef.current;
		if (el) el.scrollTop = el.scrollHeight;
	}, [messages, hasMessages]);

	// 上下文用量刷新信号：一轮回复结束后 / 切换会话时触发 ContextIndicator 重新拉取，
	// 解决「进度条一直 0%、需手动刷新才正常」的问题（后端在 reply_end 落库后才更新用量）。
	const [contextRefreshSignal, setContextRefreshSignal] = useState(0);

	// 每当一轮对话完成（streaming -> idle），刷新侧栏 + 触发上下文用量刷新。
	// 侧栏刷新使新会话已落库并出现在「最近」；上下文刷新使进度条显示真实用量（无需手动刷新）。
	// 仅对每个会话刷新一次，避免每次回复都打扰列表。
	const prevPhaseRef = useRef(phase);
	const refreshedSessionRef = useRef<string | null>(null);
	useEffect(() => {
		if (prevPhaseRef.current === 'streaming' && phase === 'idle') {
			if (refreshedSessionRef.current !== activeSessionId) {
				refreshedSessionRef.current = activeSessionId;
				refreshSidebar().catch(() => {});
				// 落库为 fire-and-forget，稍延迟再拉取用量，确保数据已写入
				setTimeout(() => setContextRefreshSignal((n) => n + 1), 1200);
			}
		}
		prevPhaseRef.current = phase;
	}, [phase, activeSessionId, refreshSidebar]);

	// 切换会话时重置「已刷新」标记并立即刷新一次上下文用量
	useEffect(() => {
		refreshedSessionRef.current = null;
		setContextRefreshSignal((n) => n + 1);
	}, [urlSessionId]);

	const scrollToBottom = useCallback(() => {
		const el = scrollRef.current;
		if (!el) return;
		stickToBottomRef.current = true;
		setShowScrollButton(false);
		el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
	}, []);

	// ---- 提示词跳转 ----
	const jumpToPrompt = useCallback((promptId: string) => {
		const el = scrollRef.current;
		if (!el) return;
		const target = findPromptElement(el, promptId);
		if (!target) return;
		stickToBottomRef.current = false;
		const top = promptTop(el, target) - 8;
		el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
	}, []);

	useEffect(() => {
		// 切换会话：重置贴底跟随 + 隐藏滚动按钮 + 重置 URL 升级/乐观插入标记
		stickToBottomRef.current = true;
		setShowScrollButton(false);
		navigatedRef.current = null;
		optimisticRef.current = null;
		setPendingUpgrade(false);
	}, [urlSessionId]);

	const chatInputRef = useRef<ChatInputHandle>(null);

	const composer = (
		<ChatInput
			ref={chatInputRef}
			phase={phase}
			hero={!hasMessages}
			userId={userId}
			onSend={sendMessage}
			onInterrupt={cancelGeneration}
		/>
	);

		const contextIndicator = (
		<ContextIndicator
			userId={userId}
			sessionId={activeSessionId}
			messages={messages}
			refreshSignal={contextRefreshSignal}
			className="mx-auto w-full max-w-[49.5rem]"
		/>
	);

	return (
		<div className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
			{/* 悬浮头部（右上 3 图标工具栏） */}
			<ThreadHeader
				connectionStatus={connectionStatus}
				promptNavigatorAction={
					<PromptNavigator messages={messages} onJumpToPrompt={jumpToPrompt} />
				}
			/>

			{/* 视口 */}
			<div className="relative flex min-h-0 flex-1 overflow-hidden">
				<div
					className={cn(
						'absolute inset-0',
						hasMessages ? 'overflow-hidden' : 'overflow-y-auto [overflow-anchor:none]',
					)}
				>
					<div
						data-layout={hasMessages ? 'thread' : 'hero'}
						className={cn(
							'thread-layout mx-auto grid min-h-full w-full',
							hasMessages
								? 'h-full max-w-[64rem]'
								: 'max-w-[72rem] px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-6 sm:px-4 sm:py-12',
						)}
					>
						{/* row 1：消息 / 空状态 */}
						{hasMessages ? (
							<div
								ref={scrollRef}
								onScroll={handleScroll}
								className="row-start-1 flex min-h-0 min-w-0 flex-col overflow-y-auto overflow-x-hidden [overflow-anchor:none] px-3 pt-12 sm:px-4"
							>
								<div className="mx-auto flex w-full max-w-[var(--content-column-width)] flex-col">
									{messages.map((msg, i) => (
										<MessageBubble
											key={msg.id}
											message={msg}
											className={i > 0 ? 'mt-5' : ''}
										onFork={
											activeSessionId && msg.role === 'assistant'
												? () => handleFork(msg.dbId ?? null)
												: undefined
										}
										/>
									))}
									{phase === 'streaming' && (
										<div className="mt-5 flex gap-1 py-2">
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce [animation-delay:-0.32s]" />
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce [animation-delay:-0.16s]" />
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce" />
										</div>
									)}
								</div>
								<div aria-hidden className="thread-message-end-gap shrink-0" />
							</div>
						) : (
							<div className="row-start-1 flex min-h-0 w-full items-center justify-center sm:items-end sm:pb-11">
								{sessionsLoading ? (
									<div className="flex items-center justify-center">
										<Spinner className="size-5 text-muted-foreground" />
									</div>
								) : (
									<div className="flex w-full animate-in fade-in-0 slide-in-from-bottom-2 flex-col items-center [animation-duration:220ms] motion-reduce:animate-none">
										<HeroGreeting text={t('chat.empty.title', { defaultValue: '今天想完成什么？' })} />
									</div>
								)}
							</div>
						)}

						{/* row 2：composer dock */}
						<div
							className={cn(
								'row-start-2 w-full',
								hasMessages ? 'relative z-10' : 'relative self-center',
							)}
						>
							{hasMessages && (
								<div className="px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-4">
									{/* 滚动到底按钮 */}
									{showScrollButton && (
										<button
											type="button"
											aria-label={t('chat.scrollToBottom')}
											title={t('chat.scrollToBottom')}
											onClick={scrollToBottom}
											className="absolute -top-11 left-1/2 z-20 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-border/70 bg-card text-foreground/85 shadow-[0_3px_10px_rgba(15,23,42,0.14)] transition-transform hover:scale-105"
										>
											<ArrowDown className="size-4" />
										</button>
									)}
									<div className="mx-auto w-full max-w-[58rem]">
										{contextIndicator}
										{composer}
									</div>
								</div>
							)}
							{!hasMessages && (
								<div className="mx-auto w-full max-w-[720px]">
									{composer}
								</div>
							)}
						</div>

						{/* row 3：spacer */}
						<div
							aria-hidden
							className="thread-layout-spacer row-start-3 min-h-0 overflow-hidden"
						/>
					</div>
				</div>

				{/* 左侧提示词标记栏 */}
				{hasMessages && (
					<PromptRail
						messages={messages}
						scrollRef={scrollRef}
						bottomOffset={96}
						onJumpToPrompt={jumpToPrompt}
					/>
				)}
			</div>
		</div>
	);
}
