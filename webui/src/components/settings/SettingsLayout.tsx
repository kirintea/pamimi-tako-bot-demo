/**
 * 设置页布局（Penpot「⚙️ 设置 v2」）
 *
 * 结构：页标题「设置」+ 副标题 → 左 subnav(180) + 右 Card(792 r16 p-6)。
 * 主区白底（shell 延伸），卡片白底 + #E5E7EB 描边。
 */

import { ChevronDown, Settings as SettingsIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { SettingsSidebar, type SettingsSection } from './SettingsSidebar';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

interface SettingsLayoutProps {
	children: React.ReactNode;
	activeSection: SettingsSection;
	onSectionChange: (section: SettingsSection) => void;
	onBack?: () => void;
}

const MOBILE_NAV_ITEMS: { key: SettingsSection; labelKey: string; fallback: string }[] = [
	{ key: 'general', labelKey: 'settings.nav.general', fallback: '通用' },
	{ key: 'models', labelKey: 'settings.nav.models', fallback: '模型' },
	{ key: 'security', labelKey: 'settings.nav.security', fallback: '安全' },
	{ key: 'skills', labelKey: 'settings.nav.skills', fallback: '技能' },
	{ key: 'mcp', labelKey: 'settings.nav.mcp', fallback: 'MCP' },
];

const CARD_TITLES: Record<SettingsSection, { key: string; fallback: string }> = {
	general: { key: 'settings.card.general', fallback: '通用配置' },
	models: { key: 'settings.card.models', fallback: '模型配置' },
	security: { key: 'settings.card.security', fallback: '安全配置' },
	skills: { key: 'settings.card.skills', fallback: '技能配置' },
	mcp: { key: 'settings.card.mcp', fallback: 'MCP 配置' },
};

export function SettingsLayout({ children, activeSection, onSectionChange, onBack }: SettingsLayoutProps) {
	const { t } = useTranslation();
	const currentLabel = MOBILE_NAV_ITEMS.find((n) => n.key === activeSection);
	const cardTitle = CARD_TITLES[activeSection];

	return (
		<div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background lg:flex-row">
			{/* Mobile top bar */}
			<div className="flex lg:hidden items-center gap-2 px-4 py-3 border-b border-border bg-background">
				{onBack && (
					<Button variant="ghost" size="icon" className="size-8 shrink-0" onClick={onBack}>
						<ChevronDown className="size-4 rotate-90" />
					</Button>
				)}
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button variant="ghost" className="flex-1 justify-between gap-2 text-[14px] font-medium">
							<span className="flex items-center gap-2">
								<SettingsIcon className="size-4" />
								{currentLabel ? t(currentLabel.labelKey, { defaultValue: currentLabel.fallback }) : ''}
							</span>
							<ChevronDown className="size-4 text-muted-foreground" />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="start" className="w-56">
						{MOBILE_NAV_ITEMS.map((item) => (
							<DropdownMenuItem
								key={item.key}
								onClick={() => onSectionChange(item.key)}
								className={item.key === activeSection ? 'bg-accent' : ''}
							>
								{t(item.labelKey, { defaultValue: item.fallback })}
							</DropdownMenuItem>
						))}
					</DropdownMenuContent>
				</DropdownMenu>
			</div>

			{/* Content */}
			<div className="min-h-0 flex-1 overflow-y-auto">
				<div className="px-6 pb-12 pt-[52px] sm:px-12">
					{/* 页标题 */}
					<h2 className="text-2xl font-semibold leading-[1.2] text-[#111928] dark:text-[#f5f5f9]">
						{t('settings.title', { defaultValue: '设置' })}
					</h2>
					<p className="mt-2 text-[13px] text-[#6B7280] dark:text-[#9ca3af]">
						{t('settings.subtitle', { defaultValue: '平台运行参数与集成配置' })}
					</p>

					{/* subnav + card */}
					<div className="mt-16 flex gap-9">
						<SettingsSidebar active={activeSection} onChange={onSectionChange} />
						<div className="mt-4 min-w-0 w-full max-w-[792px] flex-1">
							<div className="rounded-[16px] border border-border bg-background p-6">
								<h3 className="text-lg font-semibold leading-[1.2] text-[#111928] dark:text-[#f5f5f9]">
									{t(cardTitle.key, { defaultValue: cardTitle.fallback })}
								</h3>
								<div className="mt-5">{children}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
