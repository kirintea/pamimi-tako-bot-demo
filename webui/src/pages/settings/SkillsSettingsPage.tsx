/**
 * Skill 设置页 — 与工作台「技能 Skill」共用 SkillManager
 *
 * 卡片网格、过滤 pills、安装弹窗（表单/粘贴/上传）与启停逻辑和工作台一致，
 * 仅页头标题层级不同。
 */

import { SkillManager } from '@/components/skill/SkillManager';

export function SkillsSettingsPage() {
	return (
		<div className="settings-stack">
			<section>
				<SkillManager variant="settings" />
			</section>
		</div>
	);
}
