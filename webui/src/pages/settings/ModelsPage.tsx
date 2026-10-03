/**
 * 模型设置页（Penpot「⚙️ 设置 v2」模型配置卡）
 *
 * 字段行 64px 行距：label 左 13/400 #4B5563，输入 416×32 r8 #F9FAFB 右。
 * 底部「恢复默认」+「保存」；值持久化 localStorage（模型名供 ChatInput 展示）。
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ModelForm {
	provider: string;
	model: string;
	baseUrl: string;
	apiKey: string;
	temperature: string;
	maxTokens: string;
}

const DEFAULTS: ModelForm = {
	provider: 'openai-compatible',
	model: 'glm-5',
	baseUrl: 'http://127.0.0.1:8000/v1',
	apiKey: '',
	temperature: '0.7',
	maxTokens: '8192',
};

const STORAGE_KEY = 'settings.models';

function loadForm(): ModelForm {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<ModelForm>) };
	} catch {
		// ignore
	}
	return { ...DEFAULTS };
}

const fieldClassName =
	'h-8 w-full rounded-[8px] border border-border bg-[#F9FAFB] px-4 text-[13px] text-[#111928] placeholder:text-[#9CA3AF] focus:border-[#D1D5DB]';

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex min-h-[64px] items-center justify-between gap-6 py-3">
			<span className="text-[13px] font-normal text-nav-label">{label}</span>
			<div className="w-[416px] shrink-0">{children}</div>
		</div>
	);
}

export function ModelsPage() {
	const { t } = useTranslation();
	const [form, setForm] = useState<ModelForm>(() => loadForm());

	const set = <K extends keyof ModelForm>(key: K, value: ModelForm[K]) =>
		setForm((prev) => ({ ...prev, [key]: value }));

	const handleSave = () => {
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(form));
			// 同步 ChatInput 模型标签
			localStorage.setItem('llm_model_name', form.model);
			toast.success(t('settings.models.saved', { defaultValue: '模型配置已保存' }));
		} catch {
			toast.error(t('settings.models.saveFailed', { defaultValue: '保存失败（存储不可用）' }));
		}
	};

	const handleReset = () => {
		setForm({ ...DEFAULTS });
		toast.info(t('settings.models.reset', { defaultValue: '已恢复默认值（需点击保存生效）' }));
	};

	return (
		<div>
			<div className="divide-y divide-[#F3F4F6]">
				<FieldRow label={t('settings.models.provider', { defaultValue: '模型提供商' })}>
					<select
						value={form.provider}
						onChange={(e) => set('provider', e.target.value)}
						className={fieldClassName}
					>
						<option value="openai-compatible">{t('settings.models.providerOpenai', { defaultValue: 'OpenAI 兼容' })}</option>
						<option value="siliconflow">SiliconFlow</option>
						<option value="anthropic">Anthropic</option>
						<option value="dashscope">DashScope</option>
					</select>
				</FieldRow>
				<FieldRow label={t('settings.models.modelName', { defaultValue: '模型名称' })}>
					<Input
						value={form.model}
						onChange={(e) => set('model', e.target.value)}
						placeholder="glm-5"
						className={fieldClassName}
					/>
				</FieldRow>
				<FieldRow label={t('settings.models.baseUrl', { defaultValue: 'API Base' })}>
					<Input
						value={form.baseUrl}
						onChange={(e) => set('baseUrl', e.target.value)}
						placeholder="http://127.0.0.1:8000/v1"
						className={fieldClassName}
					/>
				</FieldRow>
				<FieldRow label={t('settings.models.apiKey', { defaultValue: 'API Key' })}>
					<Input
						type="password"
						value={form.apiKey}
						onChange={(e) => set('apiKey', e.target.value)}
						placeholder="••••••••••••"
						className={fieldClassName}
					/>
				</FieldRow>
				<FieldRow label={t('settings.models.temperature', { defaultValue: '温度' })}>
					<Input
						value={form.temperature}
						onChange={(e) => set('temperature', e.target.value)}
						placeholder="0.7"
						className={fieldClassName}
					/>
				</FieldRow>
				<FieldRow label={t('settings.models.maxTokens', { defaultValue: '最大 Token' })}>
					<Input
						value={form.maxTokens}
						onChange={(e) => set('maxTokens', e.target.value)}
						placeholder="8192"
						className={fieldClassName}
					/>
				</FieldRow>
			</div>

			{/* 底部按钮 */}
			<div className="mt-6 flex justify-end gap-3">
				<Button
					variant="outline"
					onClick={handleReset}
					className="h-9 w-24 rounded-[10px] border-border bg-white text-[13px] font-semibold text-foreground hover:bg-[#F3F4F6] dark:bg-transparent"
				>
					{t('settings.models.restoreDefaults', { defaultValue: '恢复默认' })}
				</Button>
				<Button
					onClick={handleSave}
					className="h-9 w-[92px] rounded-[10px] bg-primary text-[13px] font-semibold text-primary-foreground hover:bg-primary-hover"
				>
					{t('common.save', { defaultValue: '保存' })}
				</Button>
			</div>

			<p className="mt-4 text-[12px] text-[#6B7280]">
				{t('settings.models.hint', { defaultValue: '当前为前端持久化（localStorage），服务端配置 API 就绪后对接。' })}
			</p>
		</div>
	);
}
