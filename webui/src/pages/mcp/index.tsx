/**
 * MCP 服务管理页面 — Penpot v2 设计规格
 *
 * 管理 UI 抽至 components/mcp/McpManager（与设置页 MCP 共用）。
 */

import { McpManager } from '@/components/mcp/McpManager';

export function MCPPage() {
	return (
		<div className="h-full overflow-y-auto">
			<div className="px-12 pt-[52px] pb-12">
				<McpManager />
			</div>
		</div>
	);
}
