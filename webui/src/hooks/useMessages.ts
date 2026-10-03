/**
 * 消息管理 hook — 通过 WebSocket 流式接收消息
 *
 * 核心职责：
 * 1. 管理当前会话的消息列表
 * 2. 通过 WebSocket 发送消息、接收流式回复
 * 3. 处理 text_delta / thinking_delta / tool_call / tool_result / reply_end
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { sessionApi } from '@/api/session';
import type { ChatMessage, ContentPart, ToolCallInfo, ToolCallRecord, WsMessage } from '@/api/types';
import { wsManager } from '@/api/ws';

export type ReplyPhase = 'idle' | 'streaming' | 'interrupting';

export function useMessages(userId: string, sessionId: string | null) {
	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [phase, setPhase] = useState<ReplyPhase>('idle');
	const [connectionStatus, setConnectionStatus] = useState<'connected' | 'connecting' | 'disconnected'>('disconnected');
	const phaseRef = useRef<ReplyPhase>('idle');
	phaseRef.current = phase;

	/** 处理 WebSocket 消息（用 ref 保证闭包不陈旧） */
	const handleWsMessageRef = useRef<(msg: WsMessage) => void>(() => {});
	handleWsMessageRef.current = (msg: WsMessage) => {
		// 调试：重连后消息流
		if (msg.type !== 'pong') {
			console.debug('[WS recv]', msg.type, msg.payload);
		}
		switch (msg.type) {
			case 'connected':
				break;

			case 'text_delta': {
				const delta = (msg.payload as { delta: string }).delta;
				setMessages(prev => {
					const next = [...prev];
					const last = next[next.length - 1];

					// 追加到最近一条「纯文本」assistant 消息
					if (last && last.role === 'assistant' && !last.thinking && !last.toolCalls) {
						next[next.length - 1] = { ...last, content: last.content + delta };
					} else {
						// 新起一条文本消息（thinking 之后 / tool 之后 / 首条）
						next.push({
							id: `msg-${Date.now()}`,
							role: 'assistant',
							content: delta,
						});
					}
					return next;
				});
				break;
			}

			case 'thinking_delta': {
				const delta = (msg.payload as { delta: string }).delta;
				setMessages(prev => {
					const next = [...prev];
					const last = next[next.length - 1];

					// 追加到最近一条「纯思考」assistant 消息
					if (last && last.role === 'assistant' && last.thinking !== undefined && !last.toolCalls) {
						next[next.length - 1] = { ...last, thinking: (last.thinking || '') + delta };
					} else {
						// 新起一条思考消息
						next.push({
							id: `msg-${Date.now()}-think`,
							role: 'assistant',
							content: '',
							thinking: delta,
						});
					}
					return next;
				});
				break;
			}

			case 'tool_call': {
				const payload = msg.payload as {
					tool_name: string;
					tool_call_id: string;
					tool_args?: unknown;
				};
				const toolInfo: ToolCallInfo = {
					tool_name: payload.tool_name,
					tool_call_id: payload.tool_call_id,
					tool_args: payload.tool_args,
				};
				setMessages(prev => {
					const next = [...prev];
					const last = next[next.length - 1];
					// 追加到最近一条「工具调用」消息（多个工具连续调用）
					if (last && last.role === 'assistant' && last.toolCalls && !last.thinking) {
						next[next.length - 1] = {
							...last,
							toolCalls: [...last.toolCalls, toolInfo],
						};
					} else {
						// 新起一条工具消息
						next.push({
							id: `msg-${Date.now()}-tool`,
							role: 'assistant',
							content: '',
							toolCalls: [toolInfo],
						});
					}
					return next;
				});
				break;
			}

			case 'tool_result': {
				const payload = msg.payload as {
					tool_call_id: string;
					state: string;
					result: string;
				};
				setMessages(prev => {
					const next = [...prev];
					for (let i = next.length - 1; i >= 0; i--) {
						if (next[i].toolCalls) {
							const tc = next[i].toolCalls!.find(
								t => t.tool_call_id === payload.tool_call_id,
							);
							if (tc) {
								tc.result = payload.result;
								tc.state = payload.state;
								next[i] = { ...next[i], toolCalls: [...next[i].toolCalls!] };
							}
							break;
						}
					}
					return next;
				});
				break;
			}

			case 'reply_end': {
				setPhase('idle');
				const payload = msg.payload as { text?: string };
				if (payload.text) {
					// reply_end 携带完整文本时，替换最后一条 assistant 消息内容
					setMessages(prev => {
						const next = [...prev];
						const last = next[next.length - 1];
						if (last && last.role === 'assistant') {
							next[next.length - 1] = { ...last, content: payload.text! };
						}
						return next;
					});
				}
				break;
			}

			case 'error': {
				setPhase('idle');
				const errorMsg = (msg.payload as { message: string }).message;
				setMessages(prev => [
					...prev,
					{ id: `msg-${Date.now()}-err`, role: 'assistant', content: `⚠️ 错误: ${errorMsg}` },
				]);
				break;
			}

			case 'generation_in_progress': {
				// 后台任务仍在运行，初始化部分内容并进入 streaming 状态
				console.debug('[WS] generation_in_progress:', msg.payload);
				const bgPayload = msg.payload as { partial_text?: string };
				setPhase('streaming');
				if (bgPayload.partial_text) {
					setMessages(prev => {
						const next = [...prev];
						const last = next[next.length - 1];
						if (last && last.role === 'assistant' && !last.thinking && !last.toolCalls) {
							next[next.length - 1] = { ...last, content: bgPayload.partial_text! };
						} else {
							next.push({
								id: `msg-${Date.now()}-bg`,
								role: 'assistant',
								content: bgPayload.partial_text!,
							});
						}
						return next;
					});
				}
				break;
			}

			case 'pending_reply': {
				// 后台任务已完成（断连期间生成完毕），展示最终回复
				setPhase('idle');
				const pendingPayload = msg.payload as { text?: string };
				if (pendingPayload.text) {
					setMessages(prev => {
						const next = [...prev];
						const last = next[next.length - 1];
						if (last && last.role === 'assistant') {
							next[next.length - 1] = { ...last, content: pendingPayload.text! };
						} else {
							next.push({
								id: `msg-${Date.now()}-pending`,
								role: 'assistant',
								content: pendingPayload.text!,
							});
						}
						return next;
					});
				}
				break;
			}

			case 'pong':
				break;
		}
	};

	/** 加载历史消息（含 thinking + toolCalls 重建，v3 有序持久化） */
	const loadHistory = useCallback(async (sid: string) => {
		if (!userId) return;
		try {
			const res = await sessionApi.messages(userId, sid);
			const rebuilt: ChatMessage[] = [];

			// v3: 按 turn_id 分组（新数据）vs 旧数据（无 turn_id）
			const turns = new Map<string, typeof res.messages>();
			const legacyMessages: typeof res.messages = [];

			for (const m of res.messages) {
				if (m.turn_id) {
					if (!turns.has(m.turn_id)) turns.set(m.turn_id, []);
					turns.get(m.turn_id)!.push(m);
				} else {
					legacyMessages.push(m);
				}
			}

			// === v3 新路径：按 turn 处理 ===
			for (const [, turnMsgs] of turns) {
				// 按 turn_seq 排序（DB 已排序，此处防御性排序）
				turnMsgs.sort((a, b) => (a.turn_seq ?? 0) - (b.turn_seq ?? 0));

				for (const m of turnMsgs) {
					if (m.role === 'user') {
						// 用户消息处理
						let userContent = m.content;
						let userImages: string[] | undefined;
						try {
							const parsed = JSON.parse(m.content);
							if (Array.isArray(parsed)) {
								const textParts = parsed.filter((p: { type: string }) => p.type === 'text');
								const imageParts = parsed.filter((p: { type: string }) => p.type === 'image');
								userContent = textParts.map((p: { text: string }) => p.text).join('') || '';
								if (imageParts.length) userImages = imageParts.map((p: { key: string }) => p.key);
							}
						} catch { /* 纯文本 */ }
						rebuilt.push({
							id: `hist-${m.id}`,
							role: 'user',
							content: userContent,
							images: userImages,
							createdAt: m.created_at ? Date.parse(m.created_at) || undefined : undefined,
						});
					} else {
						const meta = m.metadata;
						const blockType = meta?.type;

						if (blockType === 'thinking' && meta) {
							rebuilt.push({
								id: `hist-${m.id}-think`,
								role: 'assistant',
								content: '',
								thinking: meta.text || '',
							});
						} else if (blockType === 'tool_call' && meta) {
							rebuilt.push({
								id: `hist-${m.id}-tool-${meta.tool_call_id}`,
								role: 'assistant',
								content: '',
								toolCalls: [{
									tool_name: meta.tool_name || '',
									tool_call_id: meta.tool_call_id || '',
									tool_args: meta.tool_args,
									result: meta.result,
									state: meta.state,
								}],
							});
						} else if (blockType === 'text' || (!blockType && m.content.trim())) {
							if (m.content.trim()) {
								rebuilt.push({
									id: `hist-${m.id}-text`,
									role: 'assistant',
									content: m.content,
								});
							}
						}
					}
				}
			}

			// === 降级路径：旧数据（无 turn_id）===
			for (const m of legacyMessages) {
				if (m.role === 'user') {
					let userContent = m.content;
					let userImages: string[] | undefined;
					try {
						const parsed = JSON.parse(m.content);
						if (Array.isArray(parsed)) {
							const textParts = parsed.filter((p: { type: string }) => p.type === 'text');
							const imageParts = parsed.filter((p: { type: string }) => p.type === 'image');
							userContent = textParts.map((p: { text: string }) => p.text).join('') || '';
							if (imageParts.length) userImages = imageParts.map((p: { key: string }) => p.key);
						}
					} catch { /* 纯文本 */ }
					rebuilt.push({
						id: `hist-${m.id}`,
						role: 'user',
						content: userContent,
						images: userImages,
						createdAt: m.created_at ? Date.parse(m.created_at) || undefined : undefined,
					});
				} else {
					const meta = m.metadata;
					if (meta?.thinking) {
						rebuilt.push({
							id: `hist-${m.id}-think`,
							role: 'assistant',
							content: '',
							thinking: meta.thinking,
						});
					}
					if (meta?.tool_calls?.length) {
						rebuilt.push({
							id: `hist-${m.id}-tool`,
							role: 'assistant',
							content: '',
							toolCalls: meta.tool_calls.map((tc: ToolCallRecord) => ({
								tool_name: tc.tool_name,
								tool_call_id: tc.tool_call_id,
								tool_args: tc.tool_args,
								result: tc.result,
								state: tc.state,
							})),
						});
					}
					if (m.content.trim()) {
						rebuilt.push({
							id: `hist-${m.id}`,
							role: 'assistant',
							content: m.content,
						});
					}
				}
			}

			setMessages(rebuilt);
		} catch (e) {
			console.error('加载消息历史失败:', e);
		}
	}, [userId]);

	/** 连接 WebSocket */
	const connect = useCallback((sid?: string) => {
		setConnectionStatus('connecting');
		wsManager.connect({
			userId,
			sessionId: sid,
			onOpen: () => setConnectionStatus('connected'),
			onClose: () => setConnectionStatus('disconnected'),
			onError: () => setConnectionStatus('disconnected'),
			onMessage: (msg) => handleWsMessageRef.current(msg),
		});
	}, [userId]);

	/** 发送消息（纯文本或多模态） */
	const sendMessage = useCallback((content: string | ContentPart[]) => {
		// 纯文本
		if (typeof content === 'string') {
			if (!content.trim()) return;
			setMessages(prev => [
				...prev,
				{ id: `msg-${Date.now()}`, role: 'user', content: content.trim(), createdAt: Date.now() },
			]);
			setPhase('streaming');
			wsManager.send({
				type: 'chat',
				payload: { message: content.trim() },
			});
			return;
		}

		// 多模态（ContentPart[]）
		if (!content.length) return;

		// 构建用户消息显示文本
		const textParts = content.filter((p): p is { type: 'text'; text: string } => p.type === 'text');
		const imageParts = content.filter((p): p is { type: 'image'; key: string } => p.type === 'image');
		const displayText = textParts.map(p => p.text).join('') || (imageParts.length ? `[${imageParts.length} 张图片]` : '');

		setMessages(prev => [
			...prev,
			{
				id: `msg-${Date.now()}`,
				role: 'user',
				content: displayText,
				images: imageParts.map(p => p.key),
				createdAt: Date.now(),
			},
		]);
		setPhase('streaming');
		wsManager.send({
			type: 'chat',
			payload: { message: content },
		});
	}, []);

	/** 取消生成 */
	const cancelGeneration = useCallback(() => {
		setPhase('interrupting');
		wsManager.send({ type: 'cancel', payload: {} });
	}, []);

	/** 清空消息 */
	const clearMessages = useCallback(() => {
		setMessages([]);
		setPhase('idle');
	}, []);

	/** 切换会话 */
	const switchSession = useCallback(async (newSessionId: string) => {
		clearMessages();
		await loadHistory(newSessionId);
		wsManager.switchSession(newSessionId);
	}, [clearMessages, loadHistory]);

	// 自动连接 + 清空 + 加载历史
	useEffect(() => {
		if (!userId) return;

		// sessionId 变化时清空旧消息
		setMessages([]);
		setPhase('idle');

		connect(sessionId ?? undefined);

		if (sessionId) {
			loadHistory(sessionId);
		}
	}, [userId, sessionId, connect, loadHistory]);

	// 监听连接状态变化
	useEffect(() => {
		const check = () => {
			setConnectionStatus(
				wsManager.isConnected() ? 'connected' : 'disconnected',
			);
		};
		const timer = setInterval(check, 2000);
		return () => clearInterval(timer);
	}, []);

	return {
		messages,
		phase,
		connectionStatus,
		sendMessage,
		cancelGeneration,
		clearMessages,
		loadHistory,
		connect,
		switchSession,
	};
}
