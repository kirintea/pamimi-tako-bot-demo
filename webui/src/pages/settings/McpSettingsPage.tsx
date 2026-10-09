/**
 * MCP 设置页 — 从 pages/mcp 迁移到设置体系
 *
 * 当前复用现有 MCP API，后续增强为 SettingsGroup 行样式。
 */

import { Cable, Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { mcpApi } from '@/api/mcp';
import type { McpInfo } from '@/api/types';
import { SettingsGroup, SettingsRow } from '@/components/settings';
import { SettingsSectionTitle } from '@/components/settings/SettingsSectionTitle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';

export function McpSettingsPage() {
	const { t } = useTranslation();
	const [mcps, setMcps] = useState<McpInfo[]>([]);
	const [loading, setLoading] = useState(true);
	const [showAdd, setShowAdd] = useState(false);
	const [form, setForm] = useState({ name: '', transport: 'stdio', command: '', args: '', url: '', display_name: '', description: '' });

	const loadMcps = useCallback(async () => {
		try {
			setLoading(true);
			const data = await mcpApi.list();
			setMcps(data);
		} catch (e) {
			console.error('Failed to load MCPs:', e);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => { loadMcps(); }, [loadMcps]);

	const handleAdd = async () => {
		// 连接字段完整性校验（与后端 422 规则一致），避免保存空壳记录
		if (form.transport === 'stdio' ? !form.command.trim() : !form.url.trim()) {
			toast.error(
				form.transport === 'stdio'
					? t('mcp.commandRequired', { defaultValue: 'stdio 类型需要填写启动命令' })
					: t('mcp.urlRequired', { defaultValue: 'HTTP 类型需要填写 URL' }),
			);
			return;
		}
		try {
			await mcpApi.create({
				name: form.name,
				transport: form.transport,
				command: form.command || undefined,
				args: form.args ? form.args.split('\n').filter(Boolean) : [],
				url: form.url || undefined,
				display_name: form.display_name || undefined,
				description: form.description,
			});
			setShowAdd(false);
			setForm({ name: '', transport: 'stdio', command: '', args: '', url: '', display_name: '', description: '' });
			await loadMcps();
		} catch (e) {
			console.error('Failed to add MCP:', e);
		}
	};

	const handleDelete = async (id: string) => {
		if (!confirm(t('settings.mcp.confirmDelete', { defaultValue: '确认删除此 MCP？' }))) return;
		try {
			await mcpApi.delete(id);
			await loadMcps();
		} catch (e) {
			console.error('Failed to delete MCP:', e);
		}
	};

	return (
		<div className="settings-stack">
			<section>
				<div className="flex items-center justify-between mb-5">
					<SettingsSectionTitle className="mb-0">
						{t('settings.mcp.title', { defaultValue: 'MCP 服务' })}
					</SettingsSectionTitle>
					<Button
						variant="outline"
						size="sm"
						className="h-8 rounded-full gap-1.5 text-[12px]"
						onClick={() => setShowAdd(!showAdd)}
					>
						<Plus className="size-3.5" />
						{t('settings.mcp.add', { defaultValue: '添加' })}
					</Button>
				</div>

				{/* 添加表单 */}
				{showAdd && (
					<SettingsGroup className="mb-4 border border-border bg-[#F9FAFB] p-4">
						<div className="grid gap-3">
							<div className="grid grid-cols-2 gap-3">
								<Input
									placeholder={t('settings.mcp.namePlaceholder', { defaultValue: '名称' })}
									value={form.name}
									onChange={(e) => setForm({ ...form, name: e.target.value })}
									className="h-9 rounded-full text-[13px]"
								/>
								<select
									value={form.transport}
									onChange={(e) => setForm({ ...form, transport: e.target.value })}
									className="h-9 rounded-full border border-input bg-background px-3 text-[13px]"
								>
									<option value="stdio">stdio</option>
									<option value="http">HTTP</option>
									<option value="streamable_http">Streamable HTTP</option>
								</select>
							</div>
							{form.transport === 'stdio' ? (
								<div className="grid grid-cols-2 gap-3">
									<Input
										placeholder={t('settings.mcp.commandPlaceholder', { defaultValue: '命令 (如 npx)' })}
										value={form.command}
										onChange={(e) => setForm({ ...form, command: e.target.value })}
										className="h-9 rounded-full text-[13px]"
									/>
									<Input
										placeholder={t('settings.mcp.argsPlaceholder', { defaultValue: '参数 (每行一个)' })}
										value={form.args}
										onChange={(e) => setForm({ ...form, args: e.target.value })}
										className="h-9 rounded-full text-[13px]"
									/>
								</div>
							) : (
								<Input
									placeholder={t('settings.mcp.urlPlaceholder', { defaultValue: 'URL' })}
									value={form.url}
									onChange={(e) => setForm({ ...form, url: e.target.value })}
									className="h-9 rounded-full text-[13px]"
								/>
							)}
							<div className="flex justify-end gap-2">
								<Button variant="ghost" size="sm" className="h-8 rounded-full" onClick={() => setShowAdd(false)}>
									{t('common.cancel', { defaultValue: '取消' })}
								</Button>
								<Button size="sm" className="h-8 rounded-full" onClick={handleAdd} disabled={!form.name}>
									{t('common.add', { defaultValue: '添加' })}
								</Button>
							</div>
						</div>
					</SettingsGroup>
				)}

				{/* MCP 列表 */}
				{loading ? (
					<div className="flex items-center justify-center py-12">
						<Spinner className="size-5 text-muted-foreground" />
					</div>
				) : mcps.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
						<Cable className="size-8 mb-3 opacity-40" />
						<p className="text-[13px]">{t('settings.mcp.empty', { defaultValue: '暂无 MCP 服务' })}</p>
					</div>
				) : (
					<SettingsGroup>
						{mcps.map((mcp) => (
							<SettingsRow
								key={mcp.id}
								label={mcp.display_name || mcp.name}
								description={`${mcp.transport}${mcp.description ? ` · ${mcp.description}` : ''}`}
								hoverable
							>
								<div className="flex items-center gap-2">
									<Button
										variant="ghost"
										size="icon"
										className="size-8 text-muted-foreground hover:text-destructive"
										onClick={() => handleDelete(mcp.id)}
									>
										<Trash2 className="size-3.5" />
									</Button>
								</div>
							</SettingsRow>
						))}
					</SettingsGroup>
				)}
			</section>
		</div>
	);
}