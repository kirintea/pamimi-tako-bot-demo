/**
 * 渠道设置页 — 接入企业微信等外部平台
 *
 * 复用现有渠道 API（api/channels）：列表 / 新增 / 启用停用(热切换) / 删除。
 * 启用即热启动，停用即热停止（后端 start/stop 端点）。
 *
 * 添加渠道：下拉选择 已知可接入的全部 15 种类型（无论后端是否实现），
 * 每行最左红/绿指示灯标记该渠道依赖是否齐全（缺失则禁用启动并提示缺哪些包），
 * 真实品牌 logo 由 ChannelBrandLogo（lib/channelBrands）按 type 渲染，鼠标悬停可看依赖详情。
 * 仅 backend_supported=true 的类型可创建（当前仅 wecom），其余展示「后端未接入」。
 */

import { ChevronDown, ExternalLink, Loader2, Play, Plus, Square, Trash2, Webhook } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { channelApi } from '@/api/channels';
import type { ChannelInfo, ChannelManifest, ChannelRuntime, CreateChannelRequest } from '@/api/types';
import { ChannelBrandLogo } from '@/components/ChannelBrandLogo';
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
	/** 会话空闲自动新开阈值（小时）空串 = 用默认 24；数字串含 0/负值 = 禁用 */
	idleHours: string;
}

const EMPTY_FORM: ChannelForm = {
	name: '',
	enabled: false,
	fields: {},
	idleHours: '',
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
	/** 行内编辑草稿：ch.id → 输入中的小时数字符串（未提交前不污染 ch.session_idle_hours） */
	const [idleDrafts, setIdleDrafts] = useState<Record<string, string>>({});

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
			const payload: CreateChannelRequest = {
				name: form.name,
				type: selectedType,
				enabled: form.enabled,
				config: cfg as never,
			};
			// 空串不发字段（= 用默认 24）；Number("") 是 0，必须先分支掉不能直接 Number
			const idleRaw = form.idleHours.trim();
			if (idleRaw !== '') {
				const n = Number(idleRaw);
				if (!Number.isFinite(n)) {
					// text 输入可能残留 "-"、"1e" 等中间态，静默中止会让用户困惑
					toast.error(t('settings.channels.idleHoursInvalid', { defaultValue: '会话空闲阈值须为数字' }));
					return;
				}
				payload.session_idle_hours = n;
			}
			await channelApi.create(payload);
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

	/** 行内提交 N 阈值：空值=null（后端回退默认 24）；未改动/非法则丢弃草稿不发 PATCH */
	const commitIdle = async (ch: ChannelInfo) => {
		const raw = idleDrafts[ch.id];
		if (raw === undefined) return;
		const trimmed = raw.trim();
		const current = ch.session_idle_hours == null ? '' : String(ch.session_idle_hours);
		const clearDraft = () =>
			setIdleDrafts((d) => {
				const n = { ...d };
				delete n[ch.id];
				return n;
			});
		if (trimmed === current) return clearDraft(); // 未改动，不空转 PATCH
		let value: number | null = null;
		if (trimmed !== '') {
			const n = Number(trimmed);
			if (!Number.isFinite(n)) return clearDraft(); // 非法输入丢弃
			value = n;
		}
		try {
			setBusy(ch.id);
			await channelApi.update(ch.id, { session_idle_hours: value });
			await loadChannels();
			clearDraft(); // 仅成功后清草稿；失败保留输入，用户可再次 blur 重试
		} catch (e) {
			console.error('Failed to update session_idle_hours:', e);
			toast.error(t('settings.channels.idleHoursSaveFailed', { defaultValue: '保存会话空闲阈值失败' }));
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
								<ChannelBrandLogo type={selectedType} size={22} />
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
													<ChannelBrandLogo type={m.type} size={22} />
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
								{/* N：会话空闲自动新开阈值。type=text 避免 number 输入把 "-"、"1e" 等中间态抹掉（负数=禁用须能输）；<=0 后端按禁用处理 */}
								<Input
									type="text"
									inputMode="decimal"
									step="0.5"
									placeholder={t('settings.channels.idleHoursPlaceholder', { defaultValue: '留空默认 24 小时；0 = 不自动新开' })}
									value={form.idleHours}
									onChange={(e) => setForm({ ...form, idleHours: e.target.value })}
									className="h-9 rounded-full text-[13px]"
									title={t('settings.channels.idleHoursHint', {
										defaultValue: '会话空闲超过该小时数自动新开会话（等同 /new，有提示和会话ID）；留空=默认 24，0=不自动新开',
									})}
								/>
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
										<ChannelBrandLogo type={ch.type} size={20} />
										<span
											className={`size-2 rounded-full ${state === 'running' ? 'bg-emerald-500' : state === 'failed' ? 'bg-destructive' : 'bg-muted-foreground/40'}`}
										/>
										{/* 行内 N 阈值：type=text 防 number 中间态误重置（输 "-" 被抹成空 → null）；blur/Enter 提交，空值 → null → 回退默认 24 */}
										<Input
											type="text"
											inputMode="decimal"
											step="0.5"
											className="h-8 w-20 rounded-full text-[12px]"
											placeholder={t('settings.channels.idleHoursShort', { defaultValue: '24h' })}
											title={t('settings.channels.idleHoursHint', {
												defaultValue: '会话空闲超过该小时数自动新开会话（等同 /new，有提示和会话ID）；留空=默认 24，0=不自动新开',
											})}
											value={idleDrafts[ch.id] ?? (ch.session_idle_hours == null ? '' : String(ch.session_idle_hours))}
											onChange={(e) => setIdleDrafts((d) => ({ ...d, [ch.id]: e.target.value }))}
											onBlur={() => commitIdle(ch)}
											onKeyDown={(e) => {
												if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
											}}
											disabled={isBusy}
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
