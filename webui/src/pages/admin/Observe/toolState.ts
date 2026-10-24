/**
 * 工具调用状态分类 — 右栏 TurnTree 的 ToolStateBadge 用（电线/圆点着色也走这条规则）。
 *
 * 三态：success → 'success' / error、denied → 'fail' / 其余（interrupted、
 * running、空、未知）→ 'other'。
 *
 * 比 chat/MessageBubble 的三态多把 denied 归入失败 —— 守卫（tool_guard /
 * command_guard / path_guard / docker_sandbox_proxy）拦截的调用永远不会
 * 成功，画成 pending 同样误导。MessageBubble 是共享组件，本次不动。
 */

export type ToolStateTone = 'success' | 'fail' | 'other';

export function toolStateTone(state: string | null | undefined): ToolStateTone {
	if (state === 'success') return 'success';
	if (state === 'error' || state === 'denied') return 'fail';
	return 'other';
}
