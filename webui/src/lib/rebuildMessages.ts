/**
 * 把后端 MessagesResponse 重建为前端 ChatMessage[]（v3 有序持久化：
 * 按 turn_id 分组；think/tool/text 三种 block 还原为独立气泡）。
 *
 * 该函数从 useMessages.loadHistory 中抽取，供普通会话与管理视图（root
 * 只读查看他人会话）复用，确保历史渲染逻辑只维护一份。
 */

import type {
	ChatMessage,
	MessagesResponse,
	ToolCallRecord,
} from '@/api/types';

export function rebuildChatMessages(res: MessagesResponse): ChatMessage[] {
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
				dbId: m.id,
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
					dbId: m.id,
					role: 'assistant',
					content: '',
					thinking: meta.text || '',
				});
			} else if (blockType === 'tool_call' && meta) {
				rebuilt.push({
					id: `hist-${m.id}-tool-${meta.tool_call_id}`,
					dbId: m.id,
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
						dbId: m.id,
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
				dbId: m.id,
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
					dbId: m.id,
					role: 'assistant',
					content: '',
					thinking: meta.thinking,
				});
			}
			if (meta?.tool_calls?.length) {
				rebuilt.push({
					id: `hist-${m.id}-tool`,
					dbId: m.id,
					role: 'assistant',
					content: '',
					toolCalls: (meta.tool_calls as ToolCallRecord[]).map((tc: ToolCallRecord) => ({
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
					dbId: m.id,
					role: 'assistant',
					content: m.content,
				});
			}
		}
	}

	return rebuilt;
}
