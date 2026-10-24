/**
 * 观测台中栏 — 对话详情（只读）
 *
 * 消息气泡复用 chat/MessageBubble + lib/rebuildMessages 的产物；
 * 含告警错误码的正文块额外渲染红色异常提醒横幅；
 * 底部为只读输入位（禁用 + 只读徽标）。
 *
 * 工具链路不在这里展示 —— 右栏「节点追踪」已经给出完整 user→thinking→tool→text
 * 链及每个工具的输入/输出/状态，中栏重复挂一叠只会让人看不出归属哪一轮。
 */

import { AlertTriangle, Paperclip } from 'lucide-react';

import type { ChatMessage } from '@/api/types';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { channelLabel } from '@/lib/channel';
import { formatDateTime } from '@/lib/utils';

/** A_B_C 形态的告警错误码（≥3 段全大写），如 OPEN_DOOR_WITHOUT_COMMAND */
const ANOMALY_RE = /([A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,})/;

/**
 * 扫描窗口上限。
 * 正则的 `[A-Z][A-Z0-9]*` 在超长全大写串（如大段常量/代码块）上会指数回溯，
 * 实测 20k 字符约 355ms，会卡住 render。告警码都很短（~25 字符），截断到
 * 前若干字符几乎不会漏；真出现在极后位置的告警码是已知取舍。
 */
const ANOMALY_SCAN_LIMIT = 4000;

/** 从文本里提取告警错误码（无则 null） */
function detectAnomaly(text: string): string | null {
	return ANOMALY_RE.exec(text.slice(0, ANOMALY_SCAN_LIMIT))?.[1] ?? null;
}

interface Props {
	loading: boolean;
	messages: ChatMessage[];
	userId: string;
	sessionId: string;
	channel: string;
}

export function DialogViewer({ loading, messages, userId, sessionId, channel }: Props) {
	return (
		<div className="flex min-h-0 min-w-0 flex-1 flex-col">
			{/* 中/右分隔线画在分隔条（PanelSplitter）上，根元素不带 border */}
			{/* min-w-0 不能省：flex 项默认 min-width:auto，Markdown 宽表格会把整行
			    撑破，把右栏「节点追踪」挤出画面外。 */}
			<div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4">
				<span className="truncate text-[13px] font-semibold text-nav-label">
					对话详情 · {sessionId}
				</span>
				<span className="shrink-0 rounded-full bg-secondary px-1.5 py-0.5 text-[10px] text-text-tertiary">
					{loading ? '—' : channelLabel(channel) || '未知渠道'}
				</span>
				<span className="ml-auto shrink-0 text-[11px] text-text-tertiary">{userId}</span>
			</div>

			{/* 只读横幅 */}
			<div className="flex h-9 shrink-0 items-center border-b border-border bg-warning/10 px-4">
				<span className="text-[12px] font-semibold text-warning">
					只读查看 · root 观测视图，不可回复
				</span>
			</div>

			{/* 消息流（照抄 AllSessions：max-w 限宽 + gap 垂直间距；MessageBubble 根元素自带零垂直 margin）
			    overflow-x-auto：Markdown 表格/长代码串偶尔宽过列宽，让它在本列内
			    横向滚动，而不是顶开整行布局。 */}
			<div className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-auto px-6 py-5">
				{loading ? (
					<div className="text-[12px] text-text-tertiary">加载中…</div>
				) : messages.length === 0 ? (
					<div className="text-[12px] text-text-tertiary">暂无对话内容</div>
				) : (
					<div className="mx-auto flex max-w-[760px] flex-col gap-5">
						{messages.map((m) => {
							const anomaly = m.role === 'assistant' ? detectAnomaly(m.content) : null;
							return (
								<div key={m.id}>
									{anomaly && (
										<div className="mb-2 flex items-center gap-2 rounded-[8px] border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
											<AlertTriangle className="size-3.5 shrink-0" />
											<span>异常提醒 · {anomaly}</span>
										</div>
									)}
									<MessageBubble
										message={m}
										timeText={m.created_at ? formatDateTime(m.created_at) : undefined}
									/>
								</div>
							);
						})}
					</div>
				)}
			</div>

			{/* 只读输入位 */}
			<div className="shrink-0 border-t border-border p-3">
				<div className="rounded-[10px] border border-border">
					<input
						disabled
						placeholder="只读模式 · 无法回复"
						className="w-full bg-transparent px-3 py-2 text-[13px] outline-none placeholder:text-text-tertiary"
					/>
					<div className="flex items-center gap-2 border-t border-border px-3 py-1.5">
						<Paperclip className="size-3.5 text-text-tertiary" />
						<span className="text-[11px] text-warning">只读模式 · 无法回复</span>
					</div>
				</div>
			</div>
		</div>
	);
}
