/**
 * 对话观测台 — root 专属三栏视图
 *
 * 左：会话列表（GET /admin/sessions，前端过滤搜索）
 * 中：对话详情（GET /sessions/…/messages + rebuildChatMessages，复用 MessageBubble）
 * 右：树型节点（GET /admin/observe/…/turns）
 *
 * 中/右之间是可拖拽分隔条（PanelSplitter），右栏宽度按实际可用宽度 clamp
 * 并记忆，避免不同分辨率下「节点追踪」被挤出画面。
 *
 * 非 root 重定向 /chat。只读模式，不提供回复入口。
 */

import { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';

import { observeApi } from '@/api/observe';
import { sessionApi } from '@/api/session';
import type { ChatMessage, ObserveTurn, ObserveTurnsResponse, SessionInfo } from '@/api/types';
import { wsManager } from '@/api/ws';
import { useRole } from '@/hooks/useRole';
import { rebuildChatMessages } from '@/lib/rebuildMessages';

import { DialogViewer } from './DialogViewer';
import { PanelSplitter } from './PanelSplitter';
import { SessionList } from './SessionList';
import { TurnTree } from './TurnTree';

/** 右栏「节点追踪」宽度约束（px） */
const MIN_TREE = 240;
const DEFAULT_TREE = 360;
/** 中栏「对话详情」保住的最小宽度 —— 再窄消息气泡就没法读了 */
const MIN_DIALOG = 300;
const TREE_WIDTH_KEY = 'observe:treeWidth';

function readSavedTreeWidth(): number {
	try {
		const raw = localStorage.getItem(TREE_WIDTH_KEY);
		const n = raw ? Number(raw) : NaN;
		return Number.isFinite(n) && n > 0 ? n : DEFAULT_TREE;
	} catch {
		// 隐私模式 / 禁用存储：退回默认，不阻塞渲染
		return DEFAULT_TREE;
	}
}

function saveTreeWidth(width: number): void {
	try {
		localStorage.setItem(TREE_WIDTH_KEY, String(Math.round(width)));
	} catch {
		/* 存不了就算了，下次仍用默认 */
	}
}

export function ObservePage() {
	const { isRoot, loading: roleLoading } = useRole();
	const viewer = wsManager.getUserId();

	const [sessions, setSessions] = useState<SessionInfo[]>([]);
	const [listLoading, setListLoading] = useState(true);
	const [listError, setListError] = useState<string | null>(null);

	const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
	const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

	const [messages, setMessages] = useState<ChatMessage[]>([]);
	const [turnsData, setTurnsData] = useState<ObserveTurnsResponse | null>(null);
	const [detailLoading, setDetailLoading] = useState(false);
	const [detailError, setDetailError] = useState<string | null>(null);

	// 中+右这一行的实际宽度 —— 拖拽上限按它算，不能按 window.innerWidth
	// （外层还有 272px 侧边栏 + 左栏会话列表）。这一行只在选中会话后才挂载，
	// 所以用 callback ref 挂 ResizeObserver，不能用 [] 只跑一次的 effect。
	const [rowEl, setRowEl] = useState<HTMLDivElement | null>(null);
	const [rowWidth, setRowWidth] = useState(0);
	const [treeWidth, setTreeWidth] = useState(readSavedTreeWidth);

	useEffect(() => {
		if (!rowEl) return;
		const ro = new ResizeObserver((entries) => {
			const w = entries[0]?.contentRect.width ?? 0;
			setRowWidth(w);
		});
		ro.observe(rowEl);
		setRowWidth(rowEl.getBoundingClientRect().width);
		return () => ro.disconnect();
	}, [rowEl]);

	// 拖拽上限：右栏再宽也不能把中栏压到 MIN_DIALOG 以下；行比两个下限还窄时
	// （极小窗口 / 分屏），退化成右栏至少留 MIN_TREE，中栏靠自身滚动兜住。
	// rowWidth 未测到前不 clamp，免得默认宽度闪成 MIN_TREE。
	const maxTree = Math.max(MIN_TREE, rowWidth - MIN_DIALOG);
	const clampedTreeWidth =
		rowWidth > 0 ? Math.min(Math.max(treeWidth, MIN_TREE), maxTree) : treeWidth;

	const handleTreeWidth = useCallback((w: number) => {
		setTreeWidth(w);
		saveTreeWidth(w);
	}, []);

	// ① 会话列表（root 才请求）
	useEffect(() => {
		if (!viewer || !isRoot) {
			setListLoading(false);
			return;
		}
		let cancelled = false;
		setListLoading(true);
		(async () => {
			try {
				const res = await sessionApi.listAllSessions(viewer);
				if (!cancelled) setSessions(res.sessions);
			} catch (e) {
				if (!cancelled) setListError(e instanceof Error ? e.message : String(e));
			} finally {
				if (!cancelled) setListLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [viewer, isRoot]);

	// ② 选中会话 → 拉消息 + 轮次树
	useEffect(() => {
		if (!viewer || !selectedUserId || !selectedSessionId) return;
		let cancelled = false;
		setMessages([]);
		setTurnsData(null);
		setDetailLoading(true);
		setDetailError(null);
		(async () => {
			try {
				const [msgRes, turnRes] = await Promise.all([
					sessionApi.messages(selectedUserId, selectedSessionId, { viewer }),
					observeApi.turns(selectedUserId, selectedSessionId, viewer),
				]);
				if (cancelled) return;
				setMessages(rebuildChatMessages(msgRes));
				setTurnsData(turnRes);
			} catch (e) {
				if (!cancelled) setDetailError(e instanceof Error ? e.message : String(e));
			} finally {
				if (!cancelled) setDetailLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [viewer, selectedUserId, selectedSessionId]);

	if (roleLoading) {
		return <div className="p-6 text-[13px] text-text-tertiary">加载中…</div>;
	}
	if (!isRoot) {
		return <Navigate to="/chat" replace />;
	}

	const turns: ObserveTurn[] = turnsData?.turns ?? [];

	return (
		<div className="flex h-full">
			<SessionList
				sessions={sessions}
				loading={listLoading}
				error={listError}
				selectedSessionId={selectedSessionId}
				onSelect={(userId, sessionId) => {
					setSelectedUserId(userId);
					setSelectedSessionId(sessionId);
				}}
			/>
			<div className="flex min-w-0 flex-1 flex-col">
				{!selectedSessionId ? (
					<div className="flex h-full items-center justify-center text-[13px] text-text-tertiary">
						选择左侧会话开始观测
					</div>
				) : detailError ? (
					<div className="m-4 rounded-[8px] border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
						加载失败：{detailError}
					</div>
				) : (
					<div ref={setRowEl} className="flex min-h-0 min-w-0 flex-1">
						<DialogViewer
							loading={detailLoading}
							messages={messages}
							userId={selectedUserId!}
							sessionId={selectedSessionId}
							channel={turnsData?.channel || ''}
						/>
						<PanelSplitter
							value={clampedTreeWidth}
							min={MIN_TREE}
							max={maxTree}
							onChange={handleTreeWidth}
							defaultValue={DEFAULT_TREE}
						/>
						<TurnTree loading={detailLoading} turns={turns} width={clampedTreeWidth} />
					</div>
				)}
			</div>
		</div>
	);
}
