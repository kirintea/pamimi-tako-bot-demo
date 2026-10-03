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
import { useNavigate, useParams } from 'react-router-dom';
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

	const {
		messages,
		phase,
		connectionStatus,
		sendMessage,
		cancelGeneration,
		switchSession,
	} = useMessages(userId, urlSessionId ?? null);

	// Sync session switch
	useEffect(() => {
		if (urlSessionId) {
			switchSession(urlSessionId);
		}
	}, [urlSessionId, switchSession]);

	const handleFork = useCallback(async () => {
		if (!urlSessionId) return;
		const newSessionId = await forkSession(urlSessionId);
		if (newSessionId) {
			navigate(`/chat/${newSessionId}`);
			refreshSessions();
		}
	}, [urlSessionId, forkSession, navigate, refreshSessions]);

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
		// 切换会话：重置贴底跟随 + 隐藏滚动按钮
		stickToBottomRef.current = true;
		setShowScrollButton(false);
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
			sessionId={urlSessionId ?? null}
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
												urlSessionId && msg.role === 'assistant'
													? handleFork
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
