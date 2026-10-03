/**
 * 通用设置页 — 外观、语言、连接信息、关于
 */

import { Globe, Monitor, Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { wsManager } from '@/api/ws';
import { SettingsGroup, SettingsRow, ReadOnlyRow } from '@/components/settings';
import { SettingsSectionTitle } from '@/components/settings/SettingsSectionTitle';
import { Button } from '@/components/ui/button';

export function GeneralPage() {
	const { t, i18n } = useTranslation();
	const currentUserId = wsManager.getUserId();
	const currentLang = i18n.language?.startsWith('zh') ? 'zh' : 'en';

	const handleThemeChange = (theme: 'light' | 'dark' | 'system') => {
		if (theme === 'system') {
			localStorage.removeItem('theme');
			document.documentElement.classList.toggle('dark', window.matchMedia('(prefers-color-scheme: dark)').matches);
		} else {
			localStorage.setItem('theme', theme);
			document.documentElement.classList.toggle('dark', theme === 'dark');
		}
	};

	const handleLanguageChange = (lang: 'en' | 'zh') => {
		i18n.changeLanguage(lang);
		localStorage.setItem('i18nextLng', lang);
	};

	return (
		<div className="settings-stack">
			{/* 外观 */}
			<section>
				<SettingsSectionTitle>{t('settings.general.appearance', { defaultValue: '外观' })}</SettingsSectionTitle>
				<SettingsGroup>
					<SettingsRow
						label={t('settings.general.theme', { defaultValue: '主题' })}
						description={t('settings.general.themeDesc', { defaultValue: '选择界面配色方案' })}
					>
						<div className="flex gap-1">
							{[
								{ key: 'light' as const, icon: Sun, label: '浅色' },
								{ key: 'dark' as const, icon: Moon, label: '深色' },
								{ key: 'system' as const, icon: Monitor, label: '系统' },
							].map((opt) => {
								const Icon = opt.icon;
								const isActive = localStorage.getItem('theme') === opt.key ||
									(opt.key === 'system' && !localStorage.getItem('theme'));
								return (
									<Button
										key={opt.key}
										variant={isActive ? 'secondary' : 'ghost'}
										size="sm"
										className="h-8 px-2.5 rounded-full text-[12px] gap-1.5"
										onClick={() => handleThemeChange(opt.key)}
									>
										<Icon className="size-3.5" />
										{opt.label}
									</Button>
								);
							})}
						</div>
					</SettingsRow>
					<SettingsRow
						label={t('settings.general.language', { defaultValue: '语言' })}
						description={t('settings.general.languageDesc', { defaultValue: '切换界面语言' })}
					>
						<div className="flex gap-1">
							{[
								{ key: 'zh' as const, label: '中文' },
								{ key: 'en' as const, label: 'English' },
							].map((opt) => (
								<Button
									key={opt.key}
									variant={currentLang === opt.key ? 'secondary' : 'ghost'}
									size="sm"
									className="h-8 px-2.5 rounded-full text-[12px]"
									onClick={() => handleLanguageChange(opt.key)}
								>
									{opt.label}
								</Button>
							))}
						</div>
					</SettingsRow>
				</SettingsGroup>
			</section>

			{/* 连接信息 */}
			<section>
				<SettingsSectionTitle>{t('settings.general.connection', { defaultValue: '连接' })}</SettingsSectionTitle>
				<SettingsGroup>
					<ReadOnlyRow
						label={t('settings.general.userId', { defaultValue: '用户 ID' })}
						value={currentUserId || '—'}
					/>
					<ReadOnlyRow
						label={t('settings.general.server', { defaultValue: '服务器' })}
						value={`ws://${window.location.hostname}:${window.location.port}/ws/chat`}
					/>
				</SettingsGroup>
			</section>

			{/* 关于 */}
			<section>
				<SettingsSectionTitle>{t('settings.general.about', { defaultValue: '关于' })}</SettingsSectionTitle>
				<SettingsGroup>
					<ReadOnlyRow
						label={t('settings.general.version', { defaultValue: '版本' })}
						value="0.2.0"
					/>
					<ReadOnlyRow
						label={t('settings.general.platform', { defaultValue: '平台' })}
						value="AgentScope Platform Server"
					/>
				</SettingsGroup>
			</section>
		</div>
	);
}