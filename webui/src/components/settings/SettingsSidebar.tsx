/**
 * 设置页 subnav（Penpot「⚙️ 设置 v2」）
 *
 * 纯文字导航（无图标），条目 180×32 r8，36px 行距。
 * 激活 #E8F0FE 13/600 #215BEF；非激活 13/400 #4B5563。
 * 顺序：通用 / 模型 / 安全 / 技能 / MCP
 */

import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export type SettingsSection = 'general' | 'models' | 'security' | 'skills' | 'mcp';

interface NavItem {
	key: SettingsSection;
	labelKey: string;
	fallback: string;
}

const NAV_ITEMS: NavItem[] = [
	{ key: 'general', labelKey: 'settings.nav.general', fallback: '通用' },
	{ key: 'models', labelKey: 'settings.nav.models', fallback: '模型' },
	{ key: 'security', labelKey: 'settings.nav.security', fallback: '安全' },
	{ key: 'skills', labelKey: 'settings.nav.skills', fallback: '技能' },
	{ key: 'mcp', labelKey: 'settings.nav.mcp', fallback: 'MCP' },
];

interface SettingsSidebarProps {
	active: SettingsSection;
	onChange: (section: SettingsSection) => void;
}

export function SettingsSidebar({ active, onChange }: SettingsSidebarProps) {
	const { t } = useTranslation();

	return (
		<aside className="hidden w-[180px] shrink-0 flex-col gap-1 lg:flex">
			{NAV_ITEMS.map((item) => {
				const isActive = active === item.key;
				return (
					<button
						key={item.key}
						onClick={() => onChange(item.key)}
						className={cn(
							'flex h-8 w-full items-center rounded-[8px] px-4 text-[13px] leading-5 transition-colors',
							isActive
								? 'bg-primary-light font-semibold text-primary'
								: 'font-normal text-nav-label hover:bg-row-hover',
						)}
					>
						<span>{t(item.labelKey, { defaultValue: item.fallback })}</span>
					</button>
				);
			})}
		</aside>
	);
}
