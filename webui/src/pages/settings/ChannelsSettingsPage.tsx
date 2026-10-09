/**
 * 渠道设置页 — 接入企业微信等外部平台
 *
 * 复用现有渠道 API（api/channels）：列表 / 新增 / 启用停用(热切换) / 删除。
 * 启用即热启动，停用即热停止（后端 start/stop 端点）。
 *
 * 添加渠道：下拉选择 已知可接入的全部 17 种类型（无论后端是否实现），
 * 每行最左红/绿指示灯标记该渠道依赖是否齐全（缺失则禁用启动并提示缺哪些包），
 * 真实品牌 logo 由 channelBrands 按 type 渲染，鼠标悬停可看依赖详情。
 * 仅 backend_supported=true 的类型可创建（当前仅 wecom），其余展示「后端未接入」。
 */

import { ChevronDown, ExternalLink, Loader2, Play, Plus, Square, Trash2, Webhook } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { channelApi } from '@/api/channels';
import type { ChannelInfo, ChannelManifest, ChannelRuntime } from '@/api/types';
import { CHANNEL_BRANDS } from './channelBrands';
import { SettingsGroup, SettingsRow } from '@/components/settings';
import { SettingsSectionTitle } from '@/components/settings/SettingsSectionTitle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';

interface ChannelForm {
	name: string;
	enabled: boolean;
	/** 按 manifest.fields 的 key 存值（text/password 原样，list 存逗号/换行文本） */
	fields: Record<string, string>;
}

const EMPTY_FORM: ChannelForm = {
	name: '',
	enabled: false,
	fields: {},
};

function statusTone(status: string): string {
	switch (status) {
		case 'running':
			return 'text-emerald-600';
		case 'starting':
			return 'text-amber-600';
		case 'failed':
			return 'text-destructive';
		default:
			return 'text-muted-foreground';
	}
}

/** 真实品牌 logo：单色图标用品牌色着色，多色图标保留自身颜色 */
function ChannelLogo({ type, size = 22 }: { type: string; size?: number }) {
	const b = CHANNEL_BRANDS[type];
	if (!b) {
		return (
			<div
				style={{ width: size, height: size }}
				className="flex items-center justify-center rounded-md bg-muted text-[9px] font-semibold text-muted-foreground"
			>
				{(type || '?').slice(0, 2).toUpperCase()}
			</div>
		);
	}
	return (
		<div
			style={{ width: size, height: size, color: b.brand }}
			className="flex shrink-0 items-center justify-center [&>svg]:h-full [&>svg]:w-full"
			dangerouslySetInnerHTML={{ __html: b.svg }}
		/>
	);
}

export function ChannelsSettingsPage() {
	const { t } = useTranslation();
	const [channels, setChannels] = useState<ChannelInfo[]>([]);
	const [runtime, setRuntime] = useState<Record<string, ChannelRuntime>>({});
	const [manifests, setManifests] = useState<ChannelManifest[]>([]);
	const [loading, setLoading] = useState(true);
	const [showAdd, setShowAdd] = useState(false);
	const [showTypeList, setShowTypeList] = useState(false);
	const [selectedType, setSelectedType] = useState('wecom');
	const [busy, setBusy] = useState<string | null>(null);
	const [form, setForm] = useState<ChannelForm>(EMPTY_FORM);

	const manifestByType = useMemo(() => {
		const map: Record<string, ChannelManifest> = {};
		for (const m of manifests) map[m.type] = m;
		return map;
	}, [manifests]);

	const loadChannels = useCallback(async () => {
		try {
			setLoading(true);
			const [list, statusRes, manifestsRes] = await Promise.all([
				channelApi.list(),
				channelApi.status().catch(() => ({ runtime: {} })),
				channelApi.manifests().catch(() => []),
			]);
			setChannels(list);
			setRuntime(statusRes.runtime);
			setManifests(manifestsRes);
		} catch (e) {
			console.error('Failed to load channels:', e);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		loadChannels();
	}, [loadChannels]);

	const handleAdd = async () => {
		const m = manifestByType[selectedType];
		if (!m?.backend_supported) return;
		try {
			setBusy('__new__');
			const cfg: Record<string, unknown> = {};
			for (const f of m.fields ?? []) {
				const key = f.key as string;
				const v = form.fields[key] ?? '';
				if (f.type === 'list') {
					cfg[key] = v ? v.split(/[\n,]/).map((s) => s.trim()).filter(Boolean) : [];
				} else {
					cfg[key] = v;
				}
			}
			await channelApi.create({
				name: form.name,
				type: selectedType,
				enabled: form.enabled,
				config: cfg as never,
			});
			setShowAdd(false);
			setForm(EMPTY_FORM);
			await loadChannels();
		} catch (e) {
			console.error('Failed to add channel:', e);
		} finally {
			setBusy(null);
		}
	};

	const handleToggle = async (ch: ChannelInfo) => {
		const running = runtime[ch.id]?.running;
		try {
			setBusy(ch.id);
			if (running) {
				await channelApi.stop(ch.id);
			} else {
				await channelApi.start(ch.id);
			}
			await loadChannels();
		} catch (e) {
			console.error('Failed to toggle channel:', e);
		} finally {
			setBusy(null);
		}
	};

	const handleDelete = async (id: string) => {
		if (!confirm(t('settings.channels.confirmDelete', { defaultValue: '确认删除此渠道？' }))) return;
		try {
			setBusy(id);
			await channelApi.remove(id);
			await loadChannels();
		} catch (e) {
			console.error('Failed to delete channel:', e);
		} finally {
			setBusy(null);
		}
	};

	// 依赖指示灯：返回 { known, satisfied, missing, title, dotClass }
	const depInfo = (type: string) => {
		const m = manifestByType[type];
		if (!m) {
			return {
				known: false,
				satisfied: false,
				missing: [] as string[],
				title: t('settings.channels.depsUnknown', { defaultValue: '依赖状态未知' }),
				dotClass: 'bg-muted-foreground/40',
			};
		}
		const missing = m.dependencies.filter((d) => !d.available).map((d) => d.name);
		if (m.dependencies.length === 0 || m.dependencies_satisfied) {
			return {
				known: true,
				satisfied: true,
				missing,
				title: t('settings.channels.depsReady', { defaultValue: '依赖已就绪' }),
				dotClass: 'bg-emerald-500',
			};
		}
		return {
			known: true,
			satisfied: false,
			missing,
			title: t('settings.channels.depsMissing', {
				pkgs: missing.join(', '),
				defaultValue: `缺少依赖：${missing.join(', ')}`,
			}),
			dotClass: 'bg-red-500',
		};
	};

	const selectedManifest = manifestByType[selectedType];
	const selectedDep = depInfo(selectedType);
	const selectedSupported = !!selectedManifest?.backend_supported;
	const canSubmit =
		!!form.name.trim() &&
		selectedSupported &&
		(selectedDep.known ? selectedDep.satisfied : true) &&
		busy !== '__new__';

	return (
		<div className="settings-stack">
			<section>
				<div className="mb-5 flex items-center justify-between">
					<SettingsSectionTitle className="mb-0">
						{t('settings.channels.title', { defaultValue: '渠道' })}
					</SettingsSectionTitle>
					<Button
						variant="outline"
						size="sm"
						className="h-8 gap-1.5 rounded-full text-[12px]"
						onClick={() => {
							setShowAdd(!showAdd);
							setShowTypeList(false);
						}}
					>
						<Plus className="size-3.5" />
						{t('settings.channels.add', { defaultValue: '添加' })}
					</Button>
				</div>

				{/* 添加表单 */}
				{showAdd && (
					<SettingsGroup className="relative mb-4 border border-border bg-[#F9FAFB] p-4">
						{/* 渠道类型下拉：列出 已知可接入的全部类型 */}
						<div className="relative">
							<button
								type="button"
								className="flex w-full items-center gap-2 rounded-2xl border border-input bg-background px-3 py-2 text-[13px]"
								onClick={() => setShowTypeList((v) => !v)}
							>
								<ChannelLogo type={selectedType} size={22} />
								<span className="flex-1 text-left font-medium">
									{selectedManifest?.display_name ?? selectedType}
								</span>
								<span
									title={selectedDep.title}
									className={`size-2.5 rounded-full ${selectedDep.dotClass}`}
								/>
								<ChevronDown className="size-4 text-muted-foreground" />
							</button>

							{showTypeList && (
								<>
									{/* 点击空白处关闭 */}
									<div
										className="fixed inset-0 z-10"
										onClick={() => setShowTypeList(false)}
									/>
									<div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-border bg-popover p-1 shadow-lg">
										{manifests.map((m) => {
											const d = depInfo(m.type);
											return (
												<button
													key={m.type}
													type="button"
													title={d.title}
													className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent"
													onClick={() => {
														setSelectedType(m.type);
														setForm((f) => ({ ...f, fields: {} }));
														setShowTypeList(false);
													}}
												>
													<span
														title={d.title}
														className={`size-2.5 shrink-0 rounded-full ${d.dotClass}`}
													/>
													<ChannelLogo type={m.type} size={22} />
													<span className="flex-1 truncate">{m.display_name}</span>
													{!m.backend_supported && (
														<span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
															{t('settings.channels.backendUnsupported', { defaultValue: '未实现' })}
														</span>
													)}
												</button>
											);
										})}
									</div>
								</>
							)}
						</div>

						{/* 选中渠道的依赖状态行（始终可见的红绿灯 + 缺失包提示） */}
						<div className="mb-3 mt-3 flex items-center gap-2">
							<span
								title={selectedDep.title}
								className={`size-2.5 rounded-full ${selectedDep.dotClass}`}
							/>
							<span className="text-[12px] text-muted-foreground">{selectedDep.title}</span>
						</div>

						{selectedSupported ? (
							<div className="grid gap-3">
								<Input
									placeholder={t('settings.channels.namePlaceholder', { defaultValue: '名称' })}
									value={form.name}
									onChange={(e) => setForm({ ...form, name: e.target.value })}
									className="h-9 rounded-full text-[13px]"
								/>
								{(selectedManifest?.fields ?? []).map((f) => {
									const key = f.key as string;
									const val = form.fields[key] ?? '';
									const placeholder = (f.placeholder as string) ?? '';
									const setVal = (v: string) =>
										setForm({ ...form, fields: { ...form.fields, [key]: v } });
									if (f.type === 'password') {
										return (
											<Input
												key={key}
												type="password"
												placeholder={placeholder}
												value={val}
												onChange={(e) => setVal(e.target.value)}
												className="h-9 rounded-full text-[13px]"
											/>
										);
									}
									if (f.type === 'textarea') {
										return (
											<textarea
												key={key}
												placeholder={placeholder}
												value={val}
												onChange={(e) => setVal(e.target.value)}
												className="h-20 w-full rounded-2xl border border-input bg-background px-3 py-2 text-[13px]"
											/>
										);
									}
									return (
										<Input
											key={key}
											placeholder={placeholder}
											value={val}
											onChange={(e) => setVal(e.target.value)}
											className="h-9 rounded-full text-[13px]"
										/>
									);
								})}
								<label className="flex items-center gap-2 text-[13px] text-muted-foreground">
									<input
										type="checkbox"
										checked={form.enabled}
										onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
									/>
									{t('settings.channels.enabledOnCreate', { defaultValue: '创建后立即启用并启动' })}
								</label>
								{selectedManifest?.doc_url && (
									<a
										href={selectedManifest.doc_url}
										target="_blank"
										rel="noreferrer"
										className="flex items-center gap-1 text-[12px] text-primary hover:underline"
									>
										<ExternalLink className="size-3" />
										{t('settings.channels.docLink', { defaultValue: '查看接入文档' })}
									</a>
								)}
								<div className="flex justify-end gap-2">
									<Button
										variant="ghost"
										size="sm"
										className="h-8 rounded-full"
										onClick={() => setShowAdd(false)}
									>
										{t('common.cancel', { defaultValue: '取消' })}
									</Button>
									<Button
										size="sm"
										className="h-8 rounded-full"
										onClick={handleAdd}
										disabled={!canSubmit}
									>
										{busy === '__new__' && <Loader2 className="size-3.5 animate-spin" />}
										{t('common.add', { defaultValue: '添加' })}
									</Button>
								</div>
							</div>
						) : (
							<div className="mt-1 rounded-xl border border-dashed border-border bg-muted/40 p-4 text-[13px] text-muted-foreground">
								<p className="font-medium text-foreground">
									{t('settings.channels.backendUnsupported', { defaultValue: '未实现' })}
								</p>
								<p className="mt-1">
									{t('settings.channels.backendUnsupportedDesc', {
										defaultValue: '该渠道类型后端尚未实现适配器，敬请期待。',
									})}
								</p>
							</div>
						)}
					</SettingsGroup>
				)}

				{/* 渠道列表 */}
				{loading ? (
					<div className="flex items-center justify-center py-12">
						<Spinner className="size-5 text-muted-foreground" />
					</div>
				) : channels.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
						<Webhook className="mb-3 size-8 opacity-40" />
						<p className="text-[13px]">
							{t('settings.channels.empty', { defaultValue: '暂无渠道' })}
						</p>
					</div>
				) : (
					<SettingsGroup>
						{channels.map((ch) => {
							const rt = runtime[ch.id];
							const running = rt?.running ?? false;
							const state = rt?.state ?? ch.status;
							const isBusy = busy === ch.id;
							const dep = depInfo(ch.type);
							const startDisabled = isBusy || (dep.known && !dep.satisfied);
							return (
								<SettingsRow
									key={ch.id}
									label={ch.name}
									description={`${ch.type} · ${t(`channel.state.${state}`, { defaultValue: state })}`}
									hoverable
								>
									<div className="flex items-center gap-2">
										{/* 依赖指示灯：绿=齐全 / 红=缺失 / 灰=未知 */}
										<span
											title={dep.title}
											className={`size-2.5 rounded-full ${dep.dotClass}`}
										/>
										<ChannelLogo type={ch.type} size={20} />
										<span
											className={`size-2 rounded-full ${state === 'running' ? 'bg-emerald-500' : state === 'failed' ? 'bg-destructive' : 'bg-muted-foreground/40'}`}
										/>
										<Button
											variant="ghost"
											size="sm"
											className="h-8 gap-1 rounded-full text-[12px]"
											onClick={() => handleToggle(ch)}
											disabled={startDisabled}
										>
											{isBusy ? (
												<Loader2 className="size-3.5 animate-spin" />
											) : running ? (
												<Square className="size-3" />
											) : (
												<Play className="size-3" />
											)}
											{t(running ? 'settings.channels.stop' : 'settings.channels.start', {
												defaultValue: running ? '停止' : '启动',
											})}
										</Button>
										<Button
											variant="ghost"
											size="icon"
											className="size-8 text-muted-foreground hover:text-destructive"
											onClick={() => handleDelete(ch.id)}
											disabled={isBusy}
										>
											<Trash2 className="size-3.5" />
										</Button>
									</div>
								</SettingsRow>
							);
						})}
					</SettingsGroup>
				)}
			</section>
		</div>
	);
}
