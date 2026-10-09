/**
 * 设置主页 — 侧边栏导航 + 子页面切换
 */

import { useNavigate, useParams } from 'react-router-dom';

import { SettingsLayout } from '@/components/settings/SettingsLayout';
import type { SettingsSection } from '@/components/settings/SettingsSidebar';
import { GeneralPage } from './GeneralPage';
import { ModelsPage } from './ModelsPage';
import { McpSettingsPage } from './McpSettingsPage';
import { SkillsSettingsPage } from './SkillsSettingsPage';
import { SecurityPage } from './SecurityPage';
import { ChannelsSettingsPage } from './ChannelsSettingsPage';

const VALID_SECTIONS: SettingsSection[] = ['general', 'models', 'mcp', 'skills', 'security', 'channels'];

export function SettingsPage() {
	const navigate = useNavigate();
	const { section: rawSection } = useParams<{ section?: string }>();
	const section: SettingsSection = VALID_SECTIONS.includes(rawSection as SettingsSection)
		? (rawSection as SettingsSection)
		: 'general';

	const handleSectionChange = (s: SettingsSection) => {
		navigate(`/settings/${s}`, { replace: true });
	};

	const renderSection = () => {
		switch (section) {
			case 'general':
				return <GeneralPage />;
			case 'models':
				return <ModelsPage />;
			case 'mcp':
				return <McpSettingsPage />;
			case 'skills':
				return <SkillsSettingsPage />;
			case 'channels':
				return <ChannelsSettingsPage />;
			case 'security':
				return <SecurityPage />;
			default:
				return <GeneralPage />;
		}
	};

	return (
		<SettingsLayout
			activeSection={section}
			onSectionChange={handleSectionChange}
			onBack={() => navigate('/chat')}
		>
			{renderSection()}
		</SettingsLayout>
	);
}