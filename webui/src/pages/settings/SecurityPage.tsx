/**
 * 安全设置页 — 访问模式、权限配置
 */

import { Eye, EyeOff, Shield } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { SettingsGroup, SettingsRow } from '@/components/settings';
import { SettingsSectionTitle } from '@/components/settings/SettingsSectionTitle';
import { ToggleButton } from '@/components/settings/ToggleButton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export function SecurityPage() {
	const { t } = useTranslation();
	const [authEnabled, setAuthEnabled] = useState(false);
	const [apiKey, setApiKey] = useState('');
	const [showApiKey, setShowApiKey] = useState(false);
	const [permissionMode, setPermissionMode] = useState('bypass');

	return (
		<div className="settings-stack">
			{/* API 认证 */}
			<section>
				<SettingsSectionTitle>{t('settings.security.apiAuth', { defaultValue: 'API 认证' })}</SettingsSectionTitle>
				<SettingsGroup>
					<SettingsRow
						label={t('settings.security.authRequired', { defaultValue: '启用认证' })}
						description={t('settings.security.authRequiredDesc', { defaultValue: '要求 API 请求携带密钥' })}
					>
						<ToggleButton checked={authEnabled} onCheckedChange={setAuthEnabled} />
					</SettingsRow>
					{authEnabled && (
						<SettingsRow
							label={t('settings.security.apiKey', { defaultValue: 'API 密钥' })}
							description={t('settings.security.apiKeyDesc', { defaultValue: '通过 Authorization: Bearer <key> 传递' })}
						>
							<div className="relative w-48">
								<Input
									type={showApiKey ? 'text' : 'password'}
									value={apiKey}
									onChange={(e) => setApiKey(e.target.value)}
									placeholder="sk-..."
									className="h-9 rounded-full text-[13px] pr-9"
								/>
								<button
									type="button"
									onClick={() => setShowApiKey(!showApiKey)}
									className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
								>
									{showApiKey ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
								</button>
							</div>
						</SettingsRow>
					)}
				</SettingsGroup>
			</section>

			{/* 权限模式 */}
			<section>
				<SettingsSectionTitle>{t('settings.security.permission', { defaultValue: '权限模式' })}</SettingsSectionTitle>
				<SettingsGroup>
					<SettingsRow
						label={t('settings.security.permissionMode', { defaultValue: '默认权限模式' })}
						description={t('settings.security.permissionModeDesc', { defaultValue: '新会话的默认权限级别' })}
					>
						<select
							value={permissionMode}
							onChange={(e) => setPermissionMode(e.target.value)}
							className="h-9 w-40 rounded-full border border-input bg-background px-3 text-[13px]"
						>
							<option value="bypass">{t('settings.security.modeBypass', { defaultValue: '绕过 (全部允许)' })}</option>
							<option value="default">{t('settings.security.modeDefault', { defaultValue: '默认' })}</option>
							<option value="explore">{t('settings.security.modeExplore', { defaultValue: '探索' })}</option>
							<option value="accept_edits">{t('settings.security.modeAcceptEdits', { defaultValue: '接受编辑' })}</option>
						</select>
					</SettingsRow>
				</SettingsGroup>
			</section>

			{/* 工具守卫 */}
			<section>
				<SettingsSectionTitle>{t('settings.security.toolGuard', { defaultValue: '工具守卫' })}</SettingsSectionTitle>
				<SettingsGroup>
					<SettingsRow
						label={t('settings.security.toolGuardEnabled', { defaultValue: '启用工具守卫' })}
						description={t('settings.security.toolGuardDesc', { defaultValue: '按黑白名单过滤可用工具' })}
					>
						<ToggleButton checked={false} onCheckedChange={() => {}} />
					</SettingsRow>
				</SettingsGroup>
			</section>
		</div>
	);
}