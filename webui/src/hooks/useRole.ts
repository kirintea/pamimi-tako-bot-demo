/**
 * useRole — 查询当前用户是否为 root（管理视图入口的显隐依据）。
 *
 * 调用 GET /me?user_id=...（后端 UserService 解析角色）。
 * 任意失败（未初始化 / 网络错误）一律降级为 normal，确保界面不会误显管理入口。
 */

import { useEffect, useState } from 'react';

import { sessionApi } from '@/api/session';
import { wsManager } from '@/api/ws';

export function useRole() {
	const [isRoot, setIsRoot] = useState(false);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		let cancelled = false;
		const userId = wsManager.getUserId();
		if (!userId) {
			setLoading(false);
			return;
		}
		sessionApi
			.getMe(userId)
			.then((res) => {
				if (!cancelled) setIsRoot(res.is_root);
			})
			.catch(() => {
				if (!cancelled) setIsRoot(false);
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		return () => {
			cancelled = true;
		};
	}, []);

	return { isRoot, loading };
}
