/**
 * 管理员视图 — 凭证管理（root 专属）。
 *
 * ① 列表（GET /admin/credentials?viewer=，key_prefix 掩码）
 * ② 创建（POST，服务端生成或自备 key；成功弹窗一次性展示明文 + 复制，P2）
 * ③ 吊销（POST …/revoke，确认弹窗；吊销后行保留置灰，P3）
 * 门控：useRole 页内占位（非 root）+ 后端 403 双保险。
 */

import { Copy, KeyRound, Plus, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { credentialsApi, type Credential, type CreatedCredential } from '@/api/credentials';
import { wsManager } from '@/api/ws';
import { useRole } from '@/hooks/useRole';
import { cn, formatDateTime } from '@/lib/utils';
import { useTranslation } from 'react-i18next';

const errorMessage = (error: unknown, fallback: string): string =>
	error instanceof Error ? error.message : fallback;

export function CredentialsPage() {
	const { t } = useTranslation();
	const viewer = wsManager.getUserId();
	const { isRoot, loading: roleLoading } = useRole();

	const [items, setItems] = useState<Credential[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	// 创建表单
	const [showCreate, setShowCreate] = useState(false);
	const [form, setForm] = useState({ app_id: '', channel: '', description: '' });
	const [creating, setCreating] = useState(false);
	// 明文一次性展示（P2）
	const [created, setCreated] = useState<CreatedCredential | null>(null);
	const [copied, setCopied] = useState(false);
	const [copyError, setCopyError] = useState(false);
	const keyRef = useRef<HTMLElement>(null);
	// 吊销确认（P3）
	const [revokeTarget, setRevokeTarget] = useState<Credential | null>(null);
	const [revoking, setRevoking] = useState(false);

	const load = useCallback(() => {
		if (!viewer) {
			setLoading(false);
			return;
		}
		setLoading(true);
		credentialsApi
			.list(viewer)
			.then((res) => setItems(res.items || []))
			.catch((e) => setError(errorMessage(e, '加载凭证列表失败（需 root 权限）')))
			.finally(() => setLoading(false));
	}, [viewer]);

	useEffect(() => {
		if (isRoot) load();
		else if (!roleLoading) setLoading(false);
	}, [isRoot, roleLoading, load]);

	const handleCreate = async () => {
		if (!viewer || !form.app_id.trim() || !form.channel.trim()) return;
		setCreating(true);
		setError(null);
		try {
			const rec = await credentialsApi.create(viewer, {
				app_id: form.app_id.trim(),
				channel: form.channel.trim(),
				description: form.description.trim() || undefined,
			});
			setCreated(rec);            // 明文仅此一次
			setShowCreate(false);
			setForm({ app_id: '', channel: '', description: '' });
			load();
		} catch (e) {
			setError(errorMessage(e, '创建失败'));
		} finally {
			setCreating(false);
		}
	};

	const handleRevoke = async () => {
		if (!viewer || !revokeTarget) return;
		setRevoking(true);
		setError(null);
		try {
			await credentialsApi.revoke(viewer, revokeTarget.id);
			setRevokeTarget(null);
			load();
		} catch (e) {
			setError(errorMessage(e, '吊销失败'));
		} finally {
			setRevoking(false);
		}
	};

	const selectKeyText = () => {
		const node = keyRef.current;
		if (!node) return false;
		const range = document.createRange();
		range.selectNodeContents(node);
		const selection = window.getSelection();
		selection?.removeAllRanges();
		selection?.addRange(range);
		return true;
	};

	const legacyCopy = () => {
		if (!selectKeyText()) return false;
		try {
			return document.execCommand('copy');
		} catch {
			return false;
		}
	};

	const copyKey = async () => {
		if (!created) return;
		try {
			await navigator.clipboard.writeText(created.key);
			setCopied(true);
			setCopyError(false);
			setTimeout(() => setCopied(false), 2000);
			return;
		} catch {
			// 非 HTTPS / 内网 HTTP 下 Clipboard API 常不可用，降级为选中 + execCommand。
			if (legacyCopy()) {
				setCopied(true);
				setCopyError(false);
				setTimeout(() => setCopied(false), 2000);
				return;
			}
			selectKeyText();
			setCopyError(true);
		}
	};

	// —— 门控占位（同 Observe/Stats 模式）——
	if (roleLoading) {
		return <div className="p-6 text-sm text-text-tertiary">{t('common.loading', { defaultValue: '加载中…' })}</div>;
	}
	if (!isRoot) {
		return (
			<div className="flex h-full flex-col items-center justify-center gap-3 p-6">
				<ShieldAlert className="size-8 text-text-tertiary" />
				<div className="text-sm text-text-secondary">
					{t('credentials.needRoot', { defaultValue: '需要 root 角色才能访问凭证管理' })}
				</div>
			</div>
		);
	}

	return (
		<div className="flex h-full flex-col gap-4 p-6">
			{/* 标题 + 创建按钮 */}
			<div className="flex items-center justify-between">
				<h1 className="flex items-center gap-2 text-base font-semibold">
					<KeyRound className="size-4" />
					{t('credentials.title', { defaultValue: '凭证管理' })}
				</h1>
				<button
					className="flex items-center gap-1.5 rounded-md bg-[var(--color-sidebar-primary,#215bef)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
					onClick={() => setShowCreate(true)}
					disabled={loading}
				>
					<Plus className="size-3.5" />
					{t('credentials.create', { defaultValue: '创建凭证' })}
				</button>
			</div>

			{error && (
				<div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
					{error}
				</div>
			)}

			{/* 列表 */}
			<div className="overflow-x-auto rounded-lg border border-border">
				<table className="w-full text-sm">
					<thead className="bg-[var(--sidebar,#f9fafb)] text-left text-xs text-text-secondary">
						<tr>
							<th className="px-3 py-2 font-medium">App ID</th>
							<th className="px-3 py-2 font-medium">{t('credentials.channel', { defaultValue: '渠道' })}</th>
							<th className="px-3 py-2 font-medium">Key</th>
							<th className="px-3 py-2 font-medium">{t('credentials.status', { defaultValue: '状态' })}</th>
							<th className="px-3 py-2 font-medium">{t('credentials.createdAt', { defaultValue: '创建时间' })}</th>
							<th className="px-3 py-2 font-medium">{t('credentials.expiresAt', { defaultValue: '失效/吊销时间' })}</th>
							<th className="px-3 py-2 font-medium">{t('credentials.description', { defaultValue: '描述' })}</th>
							<th className="px-3 py-2 font-medium" />
						</tr>
					</thead>
					<tbody>
						{loading && (
							<tr><td colSpan={8} className="px-3 py-6 text-center text-text-tertiary">
								{t('common.loading', { defaultValue: '加载中…' })}
							</td></tr>
						)}
						{!loading && items.length === 0 && (
							<tr><td colSpan={8} className="px-3 py-6 text-center text-text-tertiary">
								{t('credentials.empty', { defaultValue: '暂无凭证' })}
							</td></tr>
						)}
						{items.map((c) => (
							<tr key={c.id} className={cn('border-t border-border', c.status === 'revoked' && 'opacity-50')}>
								<td className="px-3 py-2 font-mono text-xs">{c.app_id}</td>
								<td className="px-3 py-2">{c.channel}</td>
								<td className="px-3 py-2 font-mono text-xs">{c.key_prefix}…</td>
								<td className="px-3 py-2 text-xs">
									{c.status === 'active' ? (
										<span className="rounded bg-green-100 px-1.5 py-0.5 text-green-700 dark:bg-green-950 dark:text-green-300">
											{t('credentials.active', { defaultValue: '有效' })}
										</span>
									) : (
										<span className="rounded bg-gray-200 px-1.5 py-0.5 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
											{t('credentials.revoked', { defaultValue: '已吊销' })}
											{c.revoked_at ? ` ${formatDateTime(c.revoked_at)}` : ''}
										</span>
									)}
								</td>
								<td className="px-3 py-2 text-xs text-text-secondary">
									{c.created_at ? formatDateTime(c.created_at) : ''}
								</td>
								<td className="px-3 py-2 text-xs">
									{c.status === 'revoked' ? (
										<span className="font-medium text-red-600 dark:text-red-400">
											{c.revoked_at ? formatDateTime(c.revoked_at) : '—'}
										</span>
									) : (
										<span className="text-text-secondary">
											{t('credentials.neverExpires', { defaultValue: '永不过期' })}
										</span>
									)}
								</td>
								<td className="max-w-[200px] truncate px-3 py-2 text-xs text-text-secondary">
									{c.description || '—'}
								</td>
								<td className="px-3 py-2 text-right">
									{c.status === 'active' && (
										<button
											className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950"
											onClick={() => setRevokeTarget(c)}
										>
											{t('credentials.revoke', { defaultValue: '吊销' })}
										</button>
									)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>

			{/* 创建弹窗 */}
			{showCreate && (
				<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
					<div className="w-[400px] rounded-lg border border-border bg-background p-5 shadow-lg">
						<h2 className="mb-4 text-sm font-semibold">
							{t('credentials.createTitle', { defaultValue: '创建凭证' })}
						</h2>
						<div className="flex flex-col gap-3">
							<input
								className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
								placeholder="App ID（如 acme）"
								value={form.app_id}
								onChange={(e) => setForm({ ...form, app_id: e.target.value })}
							/>
							<input
								className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
								placeholder={t('credentials.channelPlaceholder', { defaultValue: '渠道（1-16 位字母数字_-，如 acme_im）' })}
								value={form.channel}
								onChange={(e) => setForm({ ...form, channel: e.target.value })}
							/>
							<input
								className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
								placeholder={t('credentials.descPlaceholder', { defaultValue: '描述（可选）' })}
								value={form.description}
								onChange={(e) => setForm({ ...form, description: e.target.value })}
							/>
						</div>
						<div className="mt-5 flex justify-end gap-2">
							<button
								className="rounded-md px-3 py-1.5 text-sm text-text-secondary hover:bg-[var(--sidebar-accent,#e8f0fe)]"
								onClick={() => setShowCreate(false)}
							>
								{t('common.cancel', { defaultValue: '取消' })}
							</button>
							<button
								className="rounded-md bg-[var(--color-sidebar-primary,#215bef)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
								onClick={handleCreate}
								disabled={creating || !form.app_id.trim() || !form.channel.trim()}
							>
								{creating ? t('common.creating', { defaultValue: '创建中…' }) : t('common.confirm', { defaultValue: '确认' })}
							</button>
						</div>
					</div>
				</div>
			)}

			{/* 明文一次性弹窗（P2） */}
			{created && (
				<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
					<div className="w-[440px] rounded-lg border border-border bg-background p-5 shadow-lg">
						<h2 className="mb-2 text-sm font-semibold">
							{t('credentials.createdTitle', { defaultValue: '凭证创建成功' })}
						</h2>
						<p className="mb-3 text-xs text-amber-600 dark:text-amber-400">
							{t('credentials.keyOnce', {
								defaultValue: '⚠ 明文 key 仅显示这一次，关闭后仅保留前缀——请立即复制保存！',
							})}
						</p>
						<div className="flex items-center gap-2 rounded-md border border-border bg-[var(--sidebar,#f9fafb)] px-3 py-2">							<code
								ref={keyRef}
								className="flex-1 cursor-text select-all break-all font-mono text-xs"
								onCopy={() => { setCopied(true); setCopyError(false); }}
							>
								{created.key}
							</code>
							<button
								className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-[var(--sidebar-accent,#e8f0fe)]"
								onClick={copyKey}
							>
								<Copy className="size-3.5" />
								{copied
									? t('credentials.copied', { defaultValue: '已复制' })
									: t('credentials.copy', { defaultValue: '复制' })}
							</button>
						</div>
						{copyError && (
							<p className="mt-2 text-xs text-red-600 dark:text-red-400">
								{t('credentials.copyUnavailable', {
									defaultValue: '复制不可用，请手动选中文本后右键复制',
								})}
							</p>
						)}
						<div className="mt-4 flex justify-end">
							<button
								className="rounded-md bg-[var(--color-sidebar-primary,#215bef)] px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
								disabled={copyError}
								onClick={() => setCreated(null)}
							>
								{t('credentials.savedClose', { defaultValue: '已保存，关闭' })}
							</button>
						</div>
					</div>
				</div>
			)}

			{/* 吊销确认弹窗（P3） */}
			{revokeTarget && (
				<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
					<div className="w-[380px] rounded-lg border border-border bg-background p-5 shadow-lg">
						<h2 className="mb-2 text-sm font-semibold">
							{t('credentials.revokeTitle', { defaultValue: '吊销凭证' })}
						</h2>
						<p className="text-xs text-text-secondary">
							{t('credentials.revokeConfirm', {
								defaultValue: '确定吊销',
							})}
							<span className="font-mono"> {revokeTarget.app_id} / {revokeTarget.key_prefix}…</span>
							{t('credentials.revokeConfirmEnd', { defaultValue: '？吊销后该应用所有请求将返回 401，且不可恢复。' })}
						</p>
						<div className="mt-5 flex justify-end gap-2">
							<button
								className="rounded-md px-3 py-1.5 text-sm text-text-secondary hover:bg-[var(--sidebar-accent,#e8f0fe)]"
								onClick={() => setRevokeTarget(null)}
							>
								{t('common.cancel', { defaultValue: '取消' })}
							</button>
							<button
								className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
								onClick={handleRevoke}
								disabled={revoking}
							>
								{revoking ? t('common.working', { defaultValue: '处理中…' }) : t('credentials.revoke', { defaultValue: '吊销' })}
							</button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}
