/**
 * MCP 设置页 — 与工作台「MCP 服务」共用 McpManager
 *
 * 添加/编辑弹窗、列表行、启停与删除逻辑与工作台一致，仅页头标题层级不同。
 */

import { McpManager } from '@/components/mcp/McpManager';

export function McpSettingsPage() {
	return (
		<div className="settings-stack">
			<section>
				<McpManager variant="settings" />
			</section>
		</div>
	);
}
