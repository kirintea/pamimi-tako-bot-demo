/**
 * MCP 服务管理页面 — Penpot v2 设计规格
 */

import {
	Loader2,
	Plus,
	Trash2,
	Cable,
	Pencil,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { mcpApi } from '@/api/mcp';
import type { McpInfo, CreateMcpRequest } from '@/api/types';
import { wsManager } from '@/api/ws';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Spinner } from '@/components/ui/spinner';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

/** Normalize transport string for display */
function transportLabel(transport: string): string {
	switch (transport) {
		case 'stdio':
			return 'stdio';
		case 'http':
			return 'HTTP';
		case 'streamable_http':
			return 'Streamable HTTP';
		case 'sse':
			return 'SSE';
		default:
			return transport;
	}
}

/** Derive a human-readable endpoint from an MCP record */
function endpointText(mcp: McpInfo): string {
	if (mcp.url) return mcp.url;
	if (mcp.command) {
		const parts = [mcp.command, ...(mcp.args ?? [])];
		return parts.join(' ');
	}
	return '—';
}

/* ------------------------------------------------------------------ */
/*  Transport segmented control options                               */
/* ------------------------------------------------------------------ */

type TransportOption = { label: string; value: string };
const TRANSPORT_OPTIONS: TransportOption[] = [
	{ label: 'STDIO', value: 'stdio' },
	{ label: 'SSE', value: 'sse' },
	{ label: 'Streamable HTTP', value: 'streamable_http' },
];

/* ------------------------------------------------------------------ */
/*  Default JSON template for the JSON tab                            */
/* ------------------------------------------------------------------ */

const DEFAULT_JSON = JSON.stringify(
	{
		mcpServers: {
			'my-mcp-server': {
				command: 'npx',
				args: ['-y', '@playwright/mcp@latest'],
				env: { API_KEY: 'xxx' },
			},
		},
	},
	null,
	2,
);

/* ------------------------------------------------------------------ */
/*  Form state shape                                                  */
/* ------------------------------------------------------------------ */

interface McpFormState {
	name: string;
	display_name: string;
	description: string;
	transport: string;
	command: string;
	argsText: string; // one arg per line
	envText: string; // KEY=VALUE per line
	url: string;
	headersText: string; // KEY=VALUE per line (for HTTP headers)
	enabled: boolean;
}

const EMPTY_FORM: McpFormState = {
	name: '',
	display_name: '',
	description: '',
	transport: 'stdio',
	command: '',
	argsText: '',
	envText: '',
	url: '',
	headersText: '',
	enabled: true,
};

/* ================================================================== */
/*  MCPPage                                                           */
/* ================================================================== */

export function MCPPage() {
	const { t } = useTranslation();
	const userId = wsManager.getUserId();

	/* ---- state ---- */
	const [mcps, setMcps] = useState<McpInfo[]>([]);
	const [loading, setLoading] = useState(true);

	// dialog
	const [dialogOpen, setDialogOpen] = useState(false);
	const [dialogMode, setDialogMode] = useState<'add' | 'edit'>('add');
	const [editingId, setEditingId] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	// form & tabs
	const [activeTab, setActiveTab] = useState<'form' | 'json'>('form');
	const [form, setForm] = useState<McpFormState>({ ...EMPTY_FORM });
	const [jsonText, setJsonText] = useState(DEFAULT_JSON);

	/* ---- data ---- */
	const loadMcps = async () => {
		setLoading(true);
		try {
			setMcps(await mcpApi.list(userId));
		} catch (e) {
			console.error('加载 MCP 列表失败:', e);
			toast.error(t('mcp.loadError', { defaultValue: '加载 MCP 列表失败' }));
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		loadMcps();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	/* ---- grouping ---- */
	const enabledMcps = useMemo(() => mcps.filter((m) => m.enabled !== false), [mcps]);
	const disabledMcps = useMemo(() => mcps.filter((m) => m.enabled === false), [mcps]);

	/* ---- helpers ---- */
	const updateForm = (patch: Partial<McpFormState>) =>
		setForm((f) => ({ ...f, ...patch }));

	const openAddDialog = () => {
		setDialogMode('add');
		setEditingId(null);
		setForm({ ...EMPTY_FORM });
		setJsonText(DEFAULT_JSON);
		setActiveTab('form');
		setDialogOpen(true);
	};

	const openEditDialog = (mcp: McpInfo) => {
		setDialogMode('edit');
		setEditingId(mcp.id);
		setForm({
			name: mcp.name,
			display_name: mcp.display_name ?? '',
			description: mcp.description ?? '',
			transport: mcp.transport,
			command: mcp.command ?? '',
			argsText: (mcp.args ?? []).join('\n'),
			envText: mcp.headers
				? Object.entries(mcp.headers)
						.map(([k, v]) => `${k}=${v}`)
						.join('\n')
				: '',
			url: mcp.url ?? '',
			headersText: '',
			enabled: mcp.enabled ?? true,
		});
		setActiveTab('form');
		setDialogOpen(true);
	};

	/* ---- parse headers/env helper ---- */
	const parseKVLines = (text: string): Record<string, string> | undefined => {
		const entries = text
			.split('\n')
			.map((l) => l.trim())
			.filter(Boolean)
			.map((l) => {
				const idx = l.indexOf('=');
				if (idx <= 0) return null;
				return [l.slice(0, idx), l.slice(idx + 1)] as [string, string];
			})
			.filter(Boolean) as [string, string][];
		return entries.length > 0 ? Object.fromEntries(entries) : undefined;
	};

	/* ---- build CreateMcpRequest from form ---- */
	const buildRequest = (): CreateMcpRequest => {
		const isStdio = form.transport === 'stdio';
		const args = form.argsText
			.split('\n')
			.map((s) => s.trim())
			.filter(Boolean);
		const headers = parseKVLines(
			isStdio ? form.envText : form.headersText,
		);
		return {
			name: form.name,
			display_name: form.display_name || undefined,
			transport: form.transport,
			command: isStdio ? form.command || undefined : undefined,
			args: isStdio && args.length > 0 ? args : undefined,
			url: !isStdio ? form.url || undefined : undefined,
			headers,
			description: form.description || undefined,
		};
	};

	/* ---- save (form tab) ---- */
	const handleSaveForm = async () => {
		if (!form.name.trim()) {
			toast.error(t('mcp.nameRequired', { defaultValue: '请输入服务名称' }));
			return;
		}
		setSubmitting(true);
		try {
			if (dialogMode === 'edit' && editingId) {
				await mcpApi.update(editingId, {
					name: form.name,
					display_name: form.display_name || undefined,
					description: form.description || undefined,
					enabled: form.enabled,
				}, userId);
				toast.success(t('mcp.updateSuccess', { defaultValue: 'MCP 服务已更新' }));
			} else {
				await mcpApi.create(buildRequest(), userId);
				toast.success(t('mcp.createSuccess', { defaultValue: 'MCP 服务已添加' }));
			}
			setDialogOpen(false);
			await loadMcps();
		} catch (e) {
			console.error('保存 MCP 失败:', e);
			toast.error(t('mcp.saveError', { defaultValue: '保存失败，请重试' }));
		} finally {
			setSubmitting(false);
		}
	};

	/* ---- save (JSON tab) ---- */
	const handleSaveJson = async () => {
		let parsed: Record<string, unknown>;
		try {
			parsed = JSON.parse(jsonText);
		} catch {
			toast.error(t('mcp.jsonParseError', { defaultValue: 'JSON 解析失败' }));
			return;
		}

		let req: CreateMcpRequest;

		// Try mcpServers format first
		if (parsed.mcpServers && typeof parsed.mcpServers === 'object') {
			const servers = parsed.mcpServers as Record<string, Record<string, unknown>>;
			const entries = Object.entries(servers);
			if (entries.length === 0) {
				toast.error(t('mcp.jsonParseError', { defaultValue: 'JSON 解析失败' }));
				return;
			}
			const [name, cfg] = entries[0];
			const env = cfg.env && typeof cfg.env === 'object' ? cfg.env as Record<string, string> : undefined;
			req = {
				name,
				transport: 'stdio',
				command: typeof cfg.command === 'string' ? cfg.command : undefined,
				args: Array.isArray(cfg.args) ? (cfg.args as string[]) : undefined,
				headers: env,
			};
		} else if (typeof parsed.name === 'string' && typeof parsed.transport === 'string') {
			// Flat CreateMcpRequest shape
			req = parsed as unknown as CreateMcpRequest;
		} else {
			toast.error(t('mcp.jsonParseError', { defaultValue: 'JSON 解析失败' }));
			return;
		}

		setSubmitting(true);
		try {
			await mcpApi.create(req, userId);
			toast.success(t('mcp.createSuccess', { defaultValue: 'MCP 服务已添加' }));
			setDialogOpen(false);
			await loadMcps();
		} catch (e) {
			console.error('添加 MCP 失败:', e);
			toast.error(t('mcp.saveError', { defaultValue: '保存失败，请重试' }));
		} finally {
			setSubmitting(false);
		}
	};

	/* ---- delete ---- */
	const handleDelete = async (id: string) => {
		if (!window.confirm(t('mcp.confirmDelete', { defaultValue: '确定删除此 MCP 服务？' }))) return;
		try {
			await mcpApi.delete(id, userId);
			toast.success(t('mcp.deleteSuccess', { defaultValue: 'MCP 服务已删除' }));
			await loadMcps();
		} catch (e) {
			console.error('删除 MCP 失败:', e);
			toast.error(t('mcp.deleteError', { defaultValue: '删除失败，请重试' }));
		}
	};

	/* ================================================================ */
	/*  Render                                                          */
	/* ================================================================ */

	return (
		<div className="h-full overflow-y-auto">
			<div className="px-12 pt-[52px] pb-12">
				{/* ---- Header row ---- */}
				<div className="flex items-start justify-between">
					<div>
						<h2 className="text-2xl font-semibold text-[#111928] dark:text-[#f5f5f9]">
							{t('mcp.title', { defaultValue: 'MCP 服务' })}
						</h2>
						<p className="mt-[14px] text-[13px] text-[#6B7280] dark:text-[#9ca3af]">
							{t('mcp.subtitle', {
								defaultValue: '管理 Model Context Protocol 服务器连接与工具授权',
							})}
						</p>
					</div>
					<Button
						onClick={openAddDialog}
						className="h-9 w-36 rounded-[10px] bg-primary hover:bg-primary-hover text-white text-[13px] font-semibold shrink-0"
					>
						<Plus className="size-4" />
						{t('mcp.add', { defaultValue: '添加服务' })}
					</Button>
				</div>

				{/* ---- Content ---- */}
				{loading ? (
					<div className="flex items-center justify-center py-32">
						<Spinner className="size-5 text-muted-foreground" />
					</div>
				) : mcps.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-32 text-muted-foreground">
						<Cable className="size-8 mb-3 opacity-40" />
						<span className="text-sm">
							{t('mcp.empty', { defaultValue: '暂无 MCP 服务' })}
						</span>
						<span className="text-xs mt-1">
							{t('mcp.emptyHint', { defaultValue: '点击上方按钮添加' })}
						</span>
					</div>
				) : (
					<>
						{/* ---- 已连接 ---- */}
						{enabledMcps.length > 0 && (
							<>
								<div className="mt-10 text-xs font-semibold text-[#6B7280] dark:text-[#9ca3af]">
									{t('mcp.connected', { defaultValue: '已连接' })}
								</div>
								<div className="mt-3 flex flex-col gap-4">
									{enabledMcps.map((mcp) => (
										<McpRow
											key={mcp.id}
											mcp={mcp}
											t={t}
											onEdit={openEditDialog}
											onDelete={handleDelete}
										/>
									))}
								</div>
							</>
						)}

						{/* ---- 已停用 ---- */}
						{disabledMcps.length > 0 && (
							<>
								<div className={cn(enabledMcps.length > 0 ? 'mt-10' : 'mt-10')}>
									<span className="text-xs font-semibold text-[#6B7280] dark:text-[#9ca3af]">
										{t('mcp.disabled', { defaultValue: '已停用' })}
									</span>
								</div>
								<div className="mt-3 flex flex-col gap-4">
									{disabledMcps.map((mcp) => (
										<McpRow
											key={mcp.id}
											mcp={mcp}
											t={t}
											onEdit={openEditDialog}
											onDelete={handleDelete}
										/>
									))}
								</div>
							</>
						)}
					</>
				)}
			</div>

			{/* ============================================================ */}
			{/*  Add / Edit Dialog                                           */}
			{/* ============================================================ */}
			{dialogOpen && (
				<div
					className="fixed inset-0 bg-[#111928]/50 z-50 flex items-center justify-center p-4"
					onClick={() => setDialogOpen(false)}
				>
					<div
						className="w-[680px] max-h-[90vh] overflow-y-auto rounded-[22px] bg-white dark:bg-[#303030] p-8 shadow-panel"
						onClick={(e) => e.stopPropagation()}
					>
						{/* ---- Title ---- */}
						<h3 className="text-xl font-semibold text-[#111928] dark:text-[#f5f5f9]">
							{dialogMode === 'edit'
								? t('mcp.editTitle', { defaultValue: '编辑 MCP 服务' })
								: t('mcp.addTitle', { defaultValue: '添加 MCP 服务' })}
						</h3>
						<p className="mt-1 text-[13px] text-[#6B7280] dark:text-[#9ca3af]">
							{t('mcp.dialogSubtitle', {
								defaultValue: '配置传输方式与连接参数，保存后 Agent 可调用其工具',
							})}
						</p>
						<div className="mt-4 border-t border-border" />

						{/* ---- Tabs (add mode only has JSON tab) ---- */}
						{dialogMode === 'add' && (
							<div className="mt-5 h-9 rounded-[10px] bg-[#F3F4F6] dark:bg-[#383838] p-1 flex gap-1">
								<button
									className={cn(
										'flex-1 rounded-[8px] text-[13px] transition-colors',
										activeTab === 'form'
											? 'bg-white dark:bg-[#303030] text-primary font-semibold shadow-sm'
											: 'text-[#6B7280] dark:text-[#9ca3af] hover:text-foreground',
									)}
									onClick={() => setActiveTab('form')}
								>
									{t('mcp.tabForm', { defaultValue: '表单' })}
								</button>
								<button
									className={cn(
										'flex-1 rounded-[8px] text-[13px] transition-colors',
										activeTab === 'json'
											? 'bg-white dark:bg-[#303030] text-primary font-semibold shadow-sm'
											: 'text-[#6B7280] dark:text-[#9ca3af] hover:text-foreground',
									)}
									onClick={() => setActiveTab('json')}
								>
									JSON
								</button>
							</div>
						)}

						{/* ---- Form Tab ---- */}
						{(dialogMode === 'edit' || activeTab === 'form') && (
							<div className="mt-5 space-y-4">
								{/* Edit hint about transport fields */}
								{dialogMode === 'edit' && (
									<p className="text-xs text-[#9CA3AF]">
										{t('mcp.editTransportHint', {
											defaultValue:
												'传输参数需删除重建（后端 PATCH 暂不支持修改传输方式与连接参数）',
										})}
									</p>
								)}

								{/* Transport segmented control (add mode only) */}
								{dialogMode === 'add' && (
									<div>
										<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
											{t('mcp.transport', { defaultValue: '传输方式' })}
										</label>
										<div className="h-10 rounded-[10px] bg-[#F3F4F6] dark:bg-[#383838] p-1 flex">
											{TRANSPORT_OPTIONS.map((opt) => (
												<button
													key={opt.value}
													className={cn(
														'flex-1 rounded-[8px] text-[13px] transition-colors',
														form.transport === opt.value
															? 'bg-white dark:bg-[#303030] font-semibold text-primary shadow-sm'
															: 'text-[#6B7280] dark:text-[#9ca3af]',
													)}
													onClick={() => updateForm({ transport: opt.value })}
												>
													{opt.label}
												</button>
											))}
										</div>
									</div>
								)}

								{/* Edit: show transport as read-only badge */}
								{dialogMode === 'edit' && (
									<div>
										<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
											{t('mcp.transport', { defaultValue: '传输方式' })}
										</label>
										<span className="inline-block rounded-[8px] bg-[#F3F4F6] dark:bg-[#383838] h-6 px-3 text-xs font-semibold text-[#4B5563] dark:text-[#9ca3af]">
											{transportLabel(form.transport)}
										</span>
									</div>
								)}

								{/* 服务名称 */}
								<div>
									<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
										{t('mcp.name', { defaultValue: '服务名称' })}
										<span className="text-destructive ml-0.5">*</span>
									</label>
									<Input
										value={form.name}
										onChange={(e) => updateForm({ name: e.target.value })}
										placeholder="my-mcp-server"
										className="h-10 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF]"
									/>
								</div>

								{/* 描述 */}
								<div>
									<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
										{t('mcp.descriptionLabel', { defaultValue: '描述' })}
									</label>
									<Input
										value={form.description}
										onChange={(e) => updateForm({ description: e.target.value })}
										placeholder={t('mcp.descriptionPlaceholder', {
											defaultValue: '服务的简要描述',
										})}
										className="h-10 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF]"
									/>
								</div>

								{/* Edit: enabled toggle */}
								{dialogMode === 'edit' && editingId && (
									<div className="flex items-center gap-3">
										<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300">
											{t('mcp.enabledLabel', { defaultValue: '启用状态' })}
										</label>
										<Switch
											checked={form.enabled}
											onCheckedChange={(checked) => updateForm({ enabled: checked })}
										/>
										<span className="text-xs text-[#6B7280] dark:text-[#9ca3af]">
											{form.enabled
												? t('mcp.connected', { defaultValue: '已连接' })
												: t('mcp.disabledLabel', { defaultValue: '已停用' })}
										</span>
									</div>
								)}

								{/* STDIO fields */}
								{(dialogMode === 'add' && form.transport === 'stdio') ||
								(dialogMode === 'edit' && form.transport === 'stdio') ? (
									<>
										<div>
											<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
												{t('mcp.command', { defaultValue: '启动命令' })}
											</label>
											<Input
												value={form.command}
												onChange={(e) => updateForm({ command: e.target.value })}
												placeholder="npx"
												disabled={dialogMode === 'edit'}
												className={cn(
													'h-10 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF]',
													dialogMode === 'edit' && 'bg-[#F9FAFB] opacity-70',
												)}
											/>
										</div>
										<div>
											<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
												{t('mcp.args', { defaultValue: '启动参数' })}
											</label>
											<Textarea
												value={form.argsText}
												onChange={(e) => updateForm({ argsText: e.target.value })}
												placeholder="-y @playwright/mcp@latest"
												disabled={dialogMode === 'edit'}
												className={cn(
													'h-14 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 py-2 text-[13px] placeholder:text-[#9CA3AF] resize-none',
													dialogMode === 'edit' && 'bg-[#F9FAFB] opacity-70',
												)}
											/>
										</div>
										<div>
											<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
												{t('mcp.env', { defaultValue: '环境变量' })}
											</label>
											<Textarea
												value={form.envText}
												onChange={(e) => updateForm({ envText: e.target.value })}
												placeholder="API_KEY=xxx"
												disabled={dialogMode === 'edit'}
												className={cn(
													'h-14 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 py-2 text-[13px] placeholder:text-[#9CA3AF] resize-none',
													dialogMode === 'edit' && 'bg-[#F9FAFB] opacity-70',
												)}
											/>
											<p className="mt-1 text-xs text-[#9CA3AF]">
												{t('mcp.envHint', {
													defaultValue:
														'环境变量：后端暂存于 headers 字段（规划中独立 env）',
												})}
											</p>
										</div>
									</>
								) : null}

								{/* HTTP fields (SSE / Streamable HTTP) */}
								{(dialogMode === 'add' &&
									(form.transport === 'sse' || form.transport === 'streamable_http' || form.transport === 'http')) ||
								(dialogMode === 'edit' &&
									(form.transport === 'sse' || form.transport === 'streamable_http' || form.transport === 'http')) ? (
									<>
										<div>
											<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
												URL
											</label>
											<Input
												value={form.url}
												onChange={(e) => updateForm({ url: e.target.value })}
												placeholder="https://example.com/mcp"
												disabled={dialogMode === 'edit'}
												className={cn(
													'h-10 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF]',
													dialogMode === 'edit' && 'bg-[#F9FAFB] opacity-70',
												)}
											/>
										</div>
										<div>
											<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
												{t('mcp.headers', { defaultValue: '请求头' })}
											</label>
											<Textarea
												value={form.headersText}
												onChange={(e) => updateForm({ headersText: e.target.value })}
												placeholder="Authorization=Bearer xxx"
												disabled={dialogMode === 'edit'}
												className={cn(
													'h-14 rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3 py-2 text-[13px] placeholder:text-[#9CA3AF] resize-none',
													dialogMode === 'edit' && 'bg-[#F9FAFB] opacity-70',
												)}
											/>
										</div>
									</>
								) : null}

								{/* Hint */}
								{dialogMode === 'add' && (
									<p className="text-xs text-[#9CA3AF]">
										{form.transport === 'stdio'
											? t('mcp.stdioHint', {
													defaultValue: 'stdio 服务在服务端本机启动',
												})
											: t('mcp.httpHint', {
													defaultValue: 'HTTP 服务需保证服务端可达',
												})}
									</p>
								)}
							</div>
						)}

						{/* ---- JSON Tab ---- */}
						{dialogMode === 'add' && activeTab === 'json' && (
							<div className="mt-5">
								<label className="text-[13px] font-semibold text-[#374151] dark:text-gray-300 mb-1.5 block">
									{t('mcp.jsonConfig', { defaultValue: '配置 (JSON)' })}
								</label>
								<div className="h-[330px] rounded-[12px] bg-[#0D1117] p-4 overflow-auto">
									<textarea
										value={jsonText}
										onChange={(e) => setJsonText(e.target.value)}
										spellCheck={false}
										className="w-full h-full bg-transparent text-[#E6EDF3] font-mono text-xs leading-5 resize-none outline-none"
									/>
								</div>
							</div>
						)}

						{/* ---- Footer ---- */}
						<div className="flex justify-end gap-3 mt-6">
							<Button
								variant="outline"
								onClick={() => setDialogOpen(false)}
								className="h-10 w-[100px] rounded-[8px] border border-border bg-white dark:bg-[#303030] text-[14px] font-semibold hover:bg-[#F3F4F6] dark:bg-[#383838]"
							>
								{t('common.cancel', { defaultValue: '取消' })}
							</Button>
							<Button
								onClick={
									dialogMode === 'add' && activeTab === 'json'
										? handleSaveJson
										: handleSaveForm
								}
								disabled={submitting}
								className="h-10 w-[100px] rounded-[8px] bg-primary hover:bg-primary-hover text-white text-[14px] font-semibold"
							>
								{submitting && <Loader2 className="size-4 animate-spin" />}
								{t('common.save', { defaultValue: '保存' })}
							</Button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}

/* ================================================================== */
/*  McpRow — single MCP row component                                 */
/* ================================================================== */

interface McpRowProps {
	mcp: McpInfo;
	t: (key: string, opts?: { defaultValue?: string }) => string;
	onEdit: (mcp: McpInfo) => void;
	onDelete: (id: string) => void;
}

function McpRow({ mcp, t, onEdit, onDelete }: McpRowProps) {
	const isEnabled = mcp.enabled !== false;

	return (
		<div className="h-16 rounded-[12px] border border-border bg-white dark:bg-[#303030] flex items-center">
			{/* Left block */}
			<div className="flex-1 min-w-0 pl-6">
				<div className="text-[15px] font-semibold text-[#111928] dark:text-[#f5f5f9] truncate">
					{mcp.display_name || mcp.name}
				</div>
				<div className="text-xs text-[#9CA3AF] truncate mt-0.5">
					{mcp.description || endpointText(mcp)}
				</div>
			</div>

			{/* Type badge */}
			<div className="shrink-0 w-[120px] px-3">
				<span className="inline-block rounded-[8px] bg-[#F3F4F6] dark:bg-[#383838] h-6 px-3 text-xs font-semibold text-[#4B5563] dark:text-[#9ca3af] whitespace-nowrap">
					{transportLabel(mcp.transport)}
				</span>
			</div>

			{/* Endpoint */}
			<div className="shrink-0 w-[200px] px-3">
				<span className="text-xs text-[#6B7280] dark:text-[#9ca3af] truncate block">
					{endpointText(mcp)}
				</span>
			</div>

			{/* Right cluster */}
			<div className="shrink-0 flex items-center gap-3 pr-6 ml-auto">
				{/* Status dot */}
				<div
					className={cn(
						'size-2 rounded-full',
						isEnabled ? 'bg-[#10B981]' : 'bg-[#9CA3AF]',
					)}
				/>
				{/* State label */}
				<span
					className={cn(
						'text-xs font-semibold',
						isEnabled ? 'text-[#047857]' : 'text-[#6B7280] dark:text-[#9ca3af]',
					)}
				>
					{isEnabled
						? t('mcp.connected', { defaultValue: '已连接' })
						: t('mcp.disabledLabel', { defaultValue: '已停用' })}
				</span>
				{/* Edit button */}
				<button
					onClick={() => onEdit(mcp)}
					className="size-5 flex items-center justify-center text-[#9CA3AF] hover:text-foreground transition-colors"
					aria-label={t('mcp.edit', { defaultValue: '编辑' })}
				>
					<Pencil className="size-4" />
				</button>
				{/* Delete button */}
				<button
					onClick={() => onDelete(mcp.id)}
					className="size-5 flex items-center justify-center text-[#9CA3AF] hover:text-destructive transition-colors"
					aria-label={t('mcp.delete', { defaultValue: '删除' })}
				>
					<Trash2 className="size-4" />
				</button>
			</div>
		</div>
	);
}