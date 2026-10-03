/**
 * Setup 初始化向导（Penpot「🚀 初始化向导 Setup v2」）
 *
 * 居中 Card 720×660 r22，顶部 Logo「智」+ 标题/副标，
 * 4 步指示器（28 圆 + 94×2 连接线），4 个步骤：
 *   0 环境检查（6 检查行，数据取自 GET /health components）
 *   1 连接配置（用户 ID / WebSocket 地址 / API Key）
 *   2 模型配置（模型名称 / API Base / API Key → localStorage）
 *   3 完成 → onComplete
 * 底部右对齐：跳过/上一步 + 下一步/开始使用。
 */

import { Check, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { chatApi } from '@/api/chat';
import type { HealthResponse } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Props {
	onComplete: () => void;
	className?: string;
}

type CheckState = 'ready' | 'unconfigured' | 'pending' | 'checking';

interface CheckItem {
	key: string;
	label: string;
	state: CheckState;
}

/** 从 /health components 推断检查项状态 */
function buildChecks(health: HealthResponse | null, checking: boolean): CheckItem[] {
	const components = health?.components ?? {};
	const find = (...names: string[]) => {
		for (const [k, v] of Object.entries(components)) {
			const lk = k.toLowerCase();
			if (names.some((n) => lk.includes(n))) return String(v ?? '').toLowerCase();
		}
		return null;
	};
	const stateOf = (value: string | null): CheckState => {
		if (checking) return 'checking';
		if (value === null) return 'unconfigured';
		if (/(ok|up|ready|healthy|available)/.test(value)) return 'ready';
		return 'unconfigured';
	};

	const llm = stateOf(find('llm', 'model', 'openai'));
	const pg = stateOf(find('postgres', 'pg', 'database', 'db'));
	const redis = stateOf(find('redis'));
	const mcp = stateOf(find('mcp'));
	const otelVal = find('otel', 'tracing', 'observability');
	const otel = checking ? 'checking' : otelVal !== null && /(ok|up|ready|healthy|available)/.test(otelVal) ? 'ready' : 'unconfigured';
	const skillVal = find('skill');
	const skill = checking
		? 'checking'
		: skillVal !== null && /(ok|up|ready|healthy|available)/.test(skillVal)
			? 'ready'
			: 'pending';

	return [
		{ key: 'llm', label: 'LLM 服务', state: llm },
		{ key: 'postgres', label: 'PostgreSQL', state: pg },
		{ key: 'redis', label: 'Redis', state: redis },
		{ key: 'mcp', label: 'MCP 服务', state: mcp },
		{ key: 'otel', label: 'OTel 可观测', state: otel },
		{ key: 'skill', label: 'Skill 技能包', state: skill },
	];
}

const STATE_TEXT: Record<CheckState, { text: string; color: string; dot: string }> = {
	ready: { text: '就绪', color: 'text-[#047857]', dot: 'bg-[#10B981]' },
	unconfigured: { text: '未配置', color: 'text-[#B45309]', dot: 'bg-[#F59E0B]' },
	pending: { text: '待安装', color: 'text-[#6B7280]', dot: 'bg-[#9CA3AF]' },
	checking: { text: '检测中', color: 'text-[#6B7280]', dot: 'bg-[#9CA3AF]' },
};

const inputClassName =
	'h-10 w-full rounded-[8px] border border-border bg-white dark:bg-[#303030] px-3.5 text-[13px] text-[#111928] dark:text-[#f5f5f9] placeholder:text-[#9CA3AF] dark:placeholder:text-[#6b7280] focus:border-[#D1D5DB]';

function FieldBlock({
	label,
	children,
	hint,
}: {
	label: string;
	children: React.ReactNode;
	hint?: string;
}) {
	return (
		<label className="block">
			<span className="mb-1.5 block text-[13px] font-semibold text-[#374151] dark:text-gray-300">{label}</span>
			{children}
			{hint && <span className="mt-1.5 block text-xs text-[#9CA3AF]">{hint}</span>}
		</label>
	);
}

export const SetupPage = ({ onComplete, className }: Props) => {
	const { t } = useTranslation();
	const [step, setStep] = useState(0);

	// step0：健康检查
	const [health, setHealth] = useState<HealthResponse | null>(null);
	const [healthChecking, setHealthChecking] = useState(true);

	// step1：连接配置
	const [userId, setUserId] = useState(() => localStorage.getItem('user_id') ?? '');
	const [wsAddress, setWsAddress] = useState(() => {
		const saved = localStorage.getItem('ws_address');
		if (saved) return saved;
		const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
		return `${protocol}//${window.location.host}/ws/chat`;
	});
	const [apiKey, setApiKey] = useState(() => localStorage.getItem('api_key') ?? '');

	// step2：模型配置
	const [modelName, setModelName] = useState(() => localStorage.getItem('llm_model_name') || 'glm-5');
	const [modelBase, setModelBase] = useState(() => localStorage.getItem('llm_base_url') || 'http://127.0.0.1:8000/v1');
	const [modelKey, setModelKey] = useState(() => localStorage.getItem('llm_api_key') ?? '');

	const [errorMsg, setErrorMsg] = useState('');
	const [busy, setBusy] = useState(false);

	const runHealth = useCallback(async () => {
		setHealthChecking(true);
		try {
			const res = await chatApi.health();
			setHealth(res);
		} catch {
			setHealth(null);
		} finally {
			setHealthChecking(false);
		}
	}, []);

	useEffect(() => {
		void runHealth();
	}, [runHealth]);

	const checks = buildChecks(health, healthChecking);

	const saveConnection = () => {
		const trimmedUser = userId.trim();
		if (!trimmedUser) {
			setErrorMsg(t('setup.needUser', { defaultValue: '请输入用户 ID' }));
			return false;
		}
		localStorage.setItem('user_id', trimmedUser);
		localStorage.setItem('ws_address', wsAddress.trim().replace(/\/+$/, ''));
		const trimmedKey = apiKey.trim();
		if (trimmedKey) localStorage.setItem('api_key', trimmedKey);
		else localStorage.removeItem('api_key');
		return true;
	};

	const saveModel = () => {
		localStorage.setItem('llm_model_name', modelName.trim() || 'glm-5');
		localStorage.setItem('llm_base_url', modelBase.trim());
		if (modelKey.trim()) localStorage.setItem('llm_api_key', modelKey.trim());
		else localStorage.removeItem('llm_api_key');
		// 同步设置页模型表单
		try {
			const raw = localStorage.getItem('settings.models');
			const prev = raw ? (JSON.parse(raw) as Record<string, string>) : {};
			localStorage.setItem(
				'settings.models',
				JSON.stringify({
					...prev,
					model: modelName.trim() || 'glm-5',
					baseUrl: modelBase.trim(),
					apiKey: modelKey.trim(),
				}),
			);
		} catch {
			// ignore
		}
		return true;
	};

	const goNext = async () => {
		setErrorMsg('');
		if (step === 0) {
			setStep(1);
			return;
		}
		if (step === 1) {
			setBusy(true);
			try {
				await chatApi.health(); // 后端可达性验证
			} catch (err) {
				setErrorMsg(
					t('setup.backendUnreachable', {
						defaultValue: `无法连接到后端: ${err instanceof Error ? err.message : '连接失败'}`,
					}),
				);
				setBusy(false);
				return;
			} finally {
				setBusy(false);
			}
			if (saveConnection()) setStep(2);
			return;
		}
		if (step === 2) {
			if (saveModel()) setStep(3);
			return;
		}
		onComplete();
	};

	const goBack = () => {
		setErrorMsg('');
		if (step === 0) return;
		if (step === 3) {
			setStep(2);
			return;
		}
		setStep((s) => Math.max(0, s - 1));
	};

	const skip = () => {
		setErrorMsg('');
		if (step === 0) setStep(1);
		else if (step === 1 && saveConnection()) setStep(2);
		else if (step === 2 && saveModel()) setStep(3);
	};

	return (
		<div className="flex h-full w-full items-center justify-center overflow-y-auto bg-canvas p-6">
			<div
				className={cn(
					'flex w-full max-w-[720px] flex-col rounded-[22px] border border-border bg-background px-12 pb-10 pt-[50px] shadow-panel',
					className,
				)}
			>
				{/* Logo + 标题 */}
				<div className="flex flex-col items-center text-center">
					<div className="flex size-12 items-center justify-center rounded-[12px] bg-primary">
						<span className="text-[22px] font-bold leading-none text-primary-foreground">智</span>
					</div>
					<h1 className="mt-[22px] text-2xl font-semibold leading-[1.2] text-[#111928] dark:text-[#f5f5f9]">
						{t('setup.title', { defaultValue: '初始化平台' })}
					</h1>
					<p className="mt-3 text-[13px] text-[#6B7280]">
						{t('setup.subtitle', { defaultValue: '完成以下环境检查以开始使用' })}
					</p>
				</div>

				{/* 4 步指示器 */}
				<div className="mt-[38px] flex items-center justify-center">
					{[0, 1, 2, 3].map((i) => (
						<div key={i} className="flex items-center">
							{i > 0 && <span aria-hidden className="h-0.5 w-[94px] rounded-full bg-border" />}
							<span
								className={cn(
									'flex size-7 items-center justify-center rounded-full text-[14px]',
									i < step && 'bg-primary font-bold text-white',
									i === step && 'border border-primary bg-white font-bold text-primary',
									i > step && 'border border-border bg-white font-semibold text-[#9CA3AF]',
								)}
							>
								{i < step ? <Check className="size-3.5" /> : i + 1}
							</span>
						</div>
					))}
				</div>

				{/* 步骤内容 */}
				<div className="mt-[42px] min-h-[300px]">
					{step === 0 && (
						<div className="mx-auto flex w-full max-w-[600px] flex-col gap-1">
							{checks.map((item) => {
								const st = STATE_TEXT[item.state];
								return (
									<div
										key={item.key}
										className="flex h-[38px] items-center rounded-[10px] bg-[#F9FAFB] px-4"
									>
										{item.state === 'checking' ? (
											<Loader2 className="size-4 shrink-0 animate-spin text-[#9CA3AF]" />
										) : (
											<span
												aria-hidden
												className={cn('size-1.5 shrink-0 rounded-full', st.dot)}
											/>
										)}
										<span className="ml-3 text-[13px] text-[#111928] dark:text-[#f5f5f9]">{item.label}</span>
										<span className={cn('ml-auto text-xs font-semibold', st.color)}>
											{st.text}
										</span>
									</div>
								);
							})}
							{!healthChecking && health === null && (
								<p className="mt-2 text-center text-xs text-[#B45309]">
									{t('setup.healthUnreachable', { defaultValue: '后端不可达，请确认服务已启动（可跳过稍后重试）' })}
								</p>
							)}
						</div>
					)}

					{step === 1 && (
						<div className="mx-auto flex w-full max-w-[440px] flex-col gap-4">
							<FieldBlock label={t('setup.userId', { defaultValue: '用户 ID' })} hint={t('setup.userIdHint', { defaultValue: '用于区分不同用户的会话数据' })}>
								<Input
									value={userId}
									onChange={(e) => setUserId(e.target.value)}
									placeholder={t('setup.userIdPlaceholder', { defaultValue: '输入用户标识（如 admin、test）' })}
									autoFocus
									className={inputClassName}
								/>
							</FieldBlock>
							<FieldBlock label={t('setup.wsAddress', { defaultValue: 'WebSocket 地址' })} hint={t('setup.wsHint', { defaultValue: '留空使用默认地址' })}>
								<Input
									value={wsAddress}
									onChange={(e) => setWsAddress(e.target.value)}
									placeholder="ws://localhost:8090/ws/chat"
									className={inputClassName}
								/>
							</FieldBlock>
							<FieldBlock label={t('setup.apiKey', { defaultValue: 'API Key（可选）' })} hint={t('setup.apiKeyHint', { defaultValue: '仅在服务端开启 AUTH_REQUIRED 时填写' })}>
								<Input
									type="password"
									value={apiKey}
									onChange={(e) => setApiKey(e.target.value)}
									autoComplete="off"
									placeholder="sk-..."
									className={inputClassName}
								/>
							</FieldBlock>
						</div>
					)}

					{step === 2 && (
						<div className="mx-auto flex w-full max-w-[440px] flex-col gap-4">
							<FieldBlock label={t('setup.modelName', { defaultValue: '模型名称' })}>
								<Input
									value={modelName}
									onChange={(e) => setModelName(e.target.value)}
									placeholder="glm-5"
									autoFocus
									className={inputClassName}
								/>
							</FieldBlock>
							<FieldBlock label={t('setup.baseUrl', { defaultValue: 'API Base' })}>
								<Input
									value={modelBase}
									onChange={(e) => setModelBase(e.target.value)}
									placeholder="http://127.0.0.1:8000/v1"
									className={inputClassName}
								/>
							</FieldBlock>
							<FieldBlock label={t('setup.modelApiKey', { defaultValue: 'LLM API Key' })} hint={t('setup.modelApiKeyHint', { defaultValue: '对应 LLM_API_KEY，留空使用服务端配置' })}>
								<Input
									type="password"
									value={modelKey}
									onChange={(e) => setModelKey(e.target.value)}
									autoComplete="off"
									placeholder="••••••••••••"
									className={inputClassName}
								/>
							</FieldBlock>
						</div>
					)}

					{step === 3 && (
						<div className="mx-auto flex w-full max-w-[440px] flex-col items-center text-center">
							<div className="flex size-12 items-center justify-center rounded-full bg-[#ECFDF5]">
								<Check className="size-6 text-[#10B981]" />
							</div>
							<p className="mt-4 text-[15px] font-semibold text-[#111928] dark:text-[#f5f5f9]">
								{t('setup.doneTitle', { defaultValue: '配置完成' })}
							</p>
							<p className="mt-2 text-[13px] text-[#6B7280]">
								{t('setup.doneDesc', { defaultValue: '点击「开始使用」进入对话界面，可随时在设置中修改' })}
							</p>
							<p className="mt-4 text-xs text-[#9CA3AF]">
								{t('setup.doneUser', { defaultValue: '当前用户' })}: {userId || '—'}
							</p>
						</div>
					)}

					{errorMsg && (
						<div className="mx-auto mt-4 w-full max-w-[440px] rounded-[8px] bg-[#FEF2F2] px-4 py-2.5 text-[13px] text-[#EF4444]">
							{errorMsg}
						</div>
					)}
				</div>

				{/* 底部按钮 */}
				<div className="mt-auto flex items-center justify-end gap-3 pt-6">
					{step > 0 && (
						<Button
							variant="outline"
							onClick={goBack}
							className="h-10 w-24 rounded-[10px] border-border bg-white text-[13px] font-semibold text-foreground hover:bg-[#F3F4F6] dark:bg-transparent"
						>
							{t('setup.back', { defaultValue: '上一步' })}
						</Button>
					)}
					{step < 3 && (
						<Button
							variant="outline"
							onClick={skip}
							className="h-10 w-24 rounded-[10px] border-border bg-white text-[13px] font-semibold text-foreground hover:bg-[#F3F4F6] dark:bg-transparent"
						>
							{t('setup.skip', { defaultValue: '跳过' })}
						</Button>
					)}
					<Button
						onClick={goNext}
						disabled={busy}
						className={cn(
							'h-10 rounded-[10px] bg-primary text-[13px] font-semibold text-primary-foreground hover:bg-primary-hover',
							step === 3 ? 'w-32' : 'w-[120px]',
						)}
					>
						{busy && <Loader2 className="size-3.5 animate-spin" />}
						{step === 3
							? t('setup.start', { defaultValue: '开始使用' })
							: step === 0
								? t('setup.next', { defaultValue: '下一步' })
								: step === 1
									? t('setup.nextToModel', { defaultValue: '下一步' })
									: t('setup.nextToDone', { defaultValue: '下一步' })}
					</Button>
				</div>
			</div>
		</div>
	);
};
