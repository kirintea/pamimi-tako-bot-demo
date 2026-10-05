/**
 * AppLayout — v2 内嵌圆角 shell
 *
 * 结构（Penpot「💬 Chat 主对话页 v2」等 4 个共用板）：
 *   外层 bgPage #F9FAFB 内嵌 32px
 *   └─ Shell 白底 radius 22 border #E5E7EB shadow-panel
 *      ├─ Sidebar 272px #F9FAFB（右侧 1px 分隔线）
 *      └─ Main flex-1（路由 Outlet）
 */

import { useCallback, useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';

import { wsManager } from '@/api/ws';
import { AppSidebar } from '@/components/layout/AppSidebar';
import { useSessions } from '@/hooks/useSessions';

export function AppLayout() {
	const navigate = useNavigate();
	const location = useLocation();
	const [collapsed, setCollapsed] = useState(false);
	const userId = wsManager.getUserId();

	const {
		sessions,
		refresh: refreshSessions,
		renameSession,
		deleteSession,
	} = useSessions(userId);

	// Extract active session ID from the current route
	const activeSessionId = location.pathname.startsWith('/chat/')
		? location.pathname.split('/chat/')[1] || null
		: null;

	const handleNewChat = useCallback(() => {
		navigate('/chat');
	}, [navigate]);

	const handleSelectSession = useCallback((sessionId: string) => {
		navigate(`/chat/${sessionId}`);
	}, [navigate]);

	const handleRenameSession = useCallback(async (sessionId: string, newTitle: string) => {
		await renameSession(sessionId, newTitle);
	}, [renameSession]);

	const handleDeleteSession = useCallback(async (sessionId: string) => {
		await deleteSession(sessionId);
		if (activeSessionId === sessionId) {
			navigate('/chat');
		}
	}, [deleteSession, navigate, activeSessionId]);

	return (
		<div className="h-screen w-screen overflow-hidden bg-canvas p-3 sm:p-6 lg:p-8">
			<div className="flex h-full min-h-0 w-full overflow-hidden rounded-[22px] border border-border bg-background shadow-panel">
				<AppSidebar
					collapsed={collapsed}
					onCollapse={() => setCollapsed(true)}
					onExpand={() => setCollapsed(false)}
					onNewChat={handleNewChat}
					sessions={sessions}
					activeSessionId={activeSessionId}
					onSelectSession={handleSelectSession}
					onRenameSession={handleRenameSession}
					onDeleteSession={handleDeleteSession}
				/>
				<main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
					<Outlet context={{ refreshSessions }} />
				</main>
			</div>
		</div>
	);
}
