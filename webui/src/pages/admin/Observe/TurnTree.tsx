/**
 * 观测台右栏 — 树型节点（会话级时序链）
 *
 * **一条电线贯穿整个会话**：turn 与 turn 之间也连起来（对话本身就是时序的），
 * turn 边界用空心里程碑圆环标记，挂在同一条链上。
 *
 * 链上每一站的圆点与线按执行结果着色：成功绿 / 失败红 / 进行中·未知琥珀
 * （分类规则见 ./toolState）。tool 节点取 state；user / thinking / text 没有
 * state 字段，按已完成处理。turn 里程碑不是执行节点，用中性灰。
 *
 * 图标与节点名另按**节点类型**着色（KIND_TONE / --color-node-*）—— 与上面的
 * 执行结果色是两条独立通道：结果色在电线圆点，类型色在文字。工具链一长，靠
 * 这四个色才能一眼扫出 用户提问 / 思考 / tool / llm-answer 的分布；摘要正文
 * 仍是中性灰，不跟名称抢注意力。
 *
 * tool 节点下方常驻「输入 / 输出」两块 —— 直接取 PG conversations 的
 * metadata.tool_args / metadata.result。中间正文（思考 / 工具输入输出）通常
 * 很长，**默认一律收起**：折叠时只给一行摘要，点「展开」出一个框 —— 框可
 * 滚动（max-h-64 overflow-auto）、可一键复制、文本可用鼠标局部选中。
 *
 * 数据源只有数据库：不接 OTel，也不放任何 trace 占位。
 */

import { Bot, MessageSquare, Wrench } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import type { ObserveNode, ObserveTurn } from '@/api/types';

import { JsonViewer } from './JsonViewer';
import { resultSummary } from './jsonText';
import { toolStateTone, type ToolStateTone } from './toolState';

const KIND_ICON = {
	user: Bot,
	thinking: MessageSquare,
	tool: Wrench,
	text: MessageSquare,
} as const;

const KIND_LABEL = {
	user: '用户提问',
	thinking: '思考',
	tool: 'tool',
	text: 'llm-answer',
} as const;

/**
 * 节点类型 → 名称/图标的文字色。
 *
 * 和下面的 TONE_COLOR 是**两条独立的视觉通道**：TONE_COLOR 是执行结果（成功
 * 绿 / 失败红 / 未知琥珀），只落在电线与圆点上；这里是节点类型，只落在图标与
 * 节点名上。工具一多链路一长，全灰就分不出段落，按类型上色才能一眼扫出
 * 用户提问 / 思考 / tool / llm-answer 的分布。正文摘要仍是 nav-label 中性灰，
 * 不跟名称抢注意力。
 */
const KIND_TONE = {
	user: 'text-node-user',
	thinking: 'text-node-thinking',
	tool: 'text-node-tool',
	text: 'text-node-text',
} as const;

/** 执行结果 → 电线 / 圆点颜色。非 tool 节点没有 state，按已完成处理。 */
const TONE_COLOR: Record<ToolStateTone, string> = {
	success: 'bg-success',
	fail: 'bg-destructive',
	other: 'bg-warning',
};

/** 圆点落在标题行：top-1(4px) + size-3(12px) 的一半 = 10px */
const DOT_CENTER = 10;

/** `mcp__hbl-er-diagnosis__find_device` → `find_device`（完整名放 title） */
function shortToolName(name: string): string {
	const i = name.lastIndexOf('__');
	return i >= 0 ? name.slice(i + 2) : name;
}

function nodeTone(node: ObserveNode): ToolStateTone {
	return node.kind === 'tool' ? toolStateTone(node.state) : 'success';
}

/**
 * 链上一行：左边电线 + 圆点，右边内容。isFirst/isLast 相对**整条会话链**。
 *
 * wire / dot 分开传是因为里程碑是空心环（要 border-* 一类），执行节点是
 * 实心点（要 bg-* 一类），不能共用一个 class 串。
 */
function ChainRow({
	wire,
	dot,
	isFirst,
	isLast,
	children,
}: {
	wire: string;
	dot: string;
	isFirst: boolean;
	isLast: boolean;
	children: ReactNode;
}) {
	return (
		<div className="flex gap-2.5">
			{/* 电线列：竖线贯穿整块高度，圆点压在标题行上 */}
			<div className="relative w-3 shrink-0">
				<div
					className={`absolute left-1/2 w-px -translate-x-1/2 ${wire}`}
					style={{
						top: isFirst ? DOT_CENTER : 0,
						bottom: isLast ? `calc(100% - ${DOT_CENTER}px)` : 0,
					}}
				/>
				<span
					className={`absolute left-1/2 top-1 size-3 -translate-x-1/2 rounded-full ring-2 ring-background ${dot}`}
				/>
			</div>

			<div className="min-w-0 flex-1 pb-3">{children}</div>
		</div>
	);
}

/** turn 里程碑：标出新的一轮从这里开始，本身不是执行节点 */
function TurnMarker({
	turn,
	isFirst,
	isLast,
}: {
	turn: ObserveTurn;
	isFirst: boolean;
	isLast: boolean;
}) {
	return (
		<ChainRow
			wire="bg-border"
			dot="border-2 border-border bg-background"
			isFirst={isFirst}
			isLast={isLast}
		>
			<div className="flex items-center gap-2 pb-0.5">
				<span className="shrink-0 text-[12px] font-semibold text-nav-label">
					Turn {(turn.turn_id || '未标记').slice(0, 8)}
				</span>
				<span className="truncate text-[11px] text-text-tertiary">
					{turn.user_message || '（无提问）'}
				</span>
				<span className="ml-auto shrink-0 text-[11px] text-text-tertiary">
					{(turn.created_at || '').slice(0, 16)}
				</span>
			</div>
		</ChainRow>
	);
}

/** 节点右侧状态徽标。分类规则见 ./toolState（与电线/圆点同一条） */
function ToolStateBadge({ state }: { state: string | null | undefined }) {
	const tone = toolStateTone(state);
	if (tone === 'success') {
		return (
			<span className="shrink-0 rounded-full bg-success/15 px-1.5 py-0.5 text-[10px] text-success">
				已完成
			</span>
		);
	}
	if (tone === 'fail') {
		return (
			<span className="shrink-0 rounded-full bg-destructive/15 px-1.5 py-0.5 text-[10px] text-destructive">
				失败
			</span>
		);
	}
	return (
		<span className="shrink-0 rounded-full bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">
			{state === 'running' ? '进行中' : '未知'}
		</span>
	);
}

/** 展开后的正文框：可滚动 + 可复制 + 鼠标可局部选中（select-text） */
function DetailBox({ full }: { full: string }) {
	const [copied, setCopied] = useState(false);
	const copy = () => {
		navigator.clipboard.writeText(full).then(
			() => {
				setCopied(true);
				setTimeout(() => setCopied(false), 1500);
			},
			() => {
				/* 剪贴板被拒/不可用，静默忽略（只读观测台，不打断） */
			},
		);
	};
	return (
		<div className="mt-1 rounded-[8px] border border-border">
			<div className="flex items-center gap-2 border-b border-border px-2 py-1">
				<span className="text-[11px] text-text-tertiary">全文</span>
				<button
					type="button"
					onClick={copy}
					className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-text-tertiary hover:bg-row-hover"
				>
					{copied ? '已复制' : '复制'}
				</button>
			</div>
			<pre className="max-h-64 select-text overflow-auto whitespace-pre-wrap break-words bg-secondary/40 p-2 text-[11px] leading-[1.6] text-nav-label">
				{full}
			</pre>
		</div>
	);
}

/** 工具的一段输入或输出：折叠只给一行摘要，展开看完整 JsonViewer */
function ToolIo({ label, value }: { label: string; value: unknown }) {
	const [open, setOpen] = useState(false);
	return (
		<div>
			<div className="flex items-center gap-2">
				<span className="shrink-0 text-[11px] text-text-tertiary">{label}</span>
				{open ? null : (
					<span className="min-w-0 flex-1 truncate font-mono text-[11px] text-nav-label">
						{resultSummary(value)}
					</span>
				)}
				<button
					type="button"
					onClick={() => setOpen((v) => !v)}
					className={`${open ? 'ml-auto' : ''} shrink-0 rounded px-1.5 py-0.5 text-[11px] text-primary hover:bg-row-hover`}
				>
					{open ? '收起' : '展开'}
				</button>
			</div>
			{open ? (
				<div className="mt-1">
					<JsonViewer value={value} />
				</div>
			) : null}
		</div>
	);
}

/** 标签上截断的长度。超过这个长度才有「展开」价值 */
const TITLE_SLICE = 40;

/** 链上的一站：标题行 + （tool 的）输入输出 / （思考·文本的）可展开全文 */
function NodeRow({
	node,
	isFirst,
	isLast,
}: {
	node: ObserveNode;
	isFirst: boolean;
	isLast: boolean;
}) {
	const [open, setOpen] = useState(false);

	const Icon = KIND_ICON[node.kind] ?? MessageSquare;
	const text = node.text || '';
	const isTool = node.kind === 'tool';
	// 节点名（着色）与摘要（中性灰）分开渲染：整行同一个灰，工具一多就糊成一片
	const detail = isTool
		? shortToolName(node.tool_name || '') || 'unknown'
		: text.slice(0, TITLE_SLICE) || '（空）';
	const kindTone = KIND_TONE[node.kind];

	// 中间正文很长 → 默认收起，点「展开」出框（滑动 / 复制 / 鼠标选段）
	const expandable = !isTool && text.length > TITLE_SLICE;

	const tone = TONE_COLOR[nodeTone(node)];

	return (
		<ChainRow wire={tone} dot={tone} isFirst={isFirst} isLast={isLast}>
			<div className="flex items-start gap-2">
				<Icon className={`mt-0.5 size-3.5 shrink-0 ${kindTone}`} />
				<span
					className="min-w-0 flex-1 break-all text-[12px] leading-[1.5]"
					title={isTool ? node.tool_name : text}
				>
					<span className={`font-semibold ${kindTone}`}>{KIND_LABEL[node.kind]}</span>
					<span className="text-nav-label"> · {detail}</span>
				</span>
				{isTool && <ToolStateBadge state={node.state} />}
				{expandable ? (
					<button
						type="button"
						onClick={() => setOpen((v) => !v)}
						className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-primary hover:bg-row-hover"
					>
						{open ? '收起' : '展开'}
					</button>
				) : null}
				<span className="shrink-0 text-[11px] text-text-tertiary">—</span>
			</div>

			{expandable && open ? (
				<div className="pl-[22px]">
					<DetailBox full={text} />
				</div>
			) : null}

			{isTool ? (
				<div className="mt-1.5 flex flex-col gap-2 pl-[22px]">
					<ToolIo label="输入" value={node.tool_args ?? null} />
					<ToolIo label="输出" value={node.result ?? null} />
				</div>
			) : null}
		</ChainRow>
	);
}

/** 链上的行：turn 里程碑 或 普通节点 */
type ChainItem =
	| { kind: 'turn'; key: string; turn: ObserveTurn }
	| { kind: 'node'; key: string; node: ObserveNode };

function buildChain(turns: ObserveTurn[]): ChainItem[] {
	const items: ChainItem[] = [];
	turns.forEach((turn, ti) => {
		const tkey = turn.turn_id || `legacy-${ti}`;
		items.push({ kind: 'turn', key: `turn-${tkey}`, turn });
		turn.nodes.forEach((node) => {
			items.push({ kind: 'node', key: `${tkey}-${node.seq}`, node });
		});
	});
	return items;
}

/** 右栏容器 class（三态空/满共用，避免三处重复）。宽度由父级传入，见 index.tsx */
const PANEL =
	'shrink-0 overflow-y-auto overflow-x-hidden border-l border-border p-4 min-w-0';

export function TurnTree({
	loading,
	turns,
	width,
}: {
	loading: boolean;
	turns: ObserveTurn[];
	/** 面板宽度（px）。由 index.tsx 的分隔条拖动决定，随视口 clamp */
	width: number;
}) {
	// 三态都要吃同一套宽度约束，否则窄屏下空态/加载态会写死撑破布局
	const style = { width };
	if (loading) {
		return (
			<div className={`${PANEL} text-[12px] text-text-tertiary`} style={style}>
				加载中…
			</div>
		);
	}
	if (turns.length === 0) {
		return (
			<div className={`${PANEL} text-[12px] text-text-tertiary`} style={style}>
				暂无节点
			</div>
		);
	}

	const items = buildChain(turns);

	return (
		<div className={PANEL} style={style}>
			{/* 面板级头部：视图切换只出现一次（链是会话级的，不按轮重复） */}
			<div className="mb-3 flex items-center gap-2">
				<span className="truncate text-[13px] font-semibold text-nav-label">
					节点追踪 · {turns.length} 轮
				</span>
				<span className="ml-auto flex shrink-0 items-center gap-1">
					<button
						type="button"
						disabled
						className="rounded-full border border-border px-2 py-0.5 text-[10px] text-text-tertiary opacity-50"
					>
						分支视图
					</button>
					<button
						type="button"
						disabled
						className="rounded-full border border-border px-2 py-0.5 text-[10px] text-text-tertiary opacity-50"
					>
						Timeline
					</button>
					<button
						type="button"
						className="rounded-full bg-primary-light px-2 py-0.5 text-[10px] text-primary"
					>
						树型
					</button>
				</span>
			</div>

			{/* 一条电线贯穿整个会话：turn 之间也连着 */}
			{items.map((item, i) => {
				const isFirst = i === 0;
				const isLast = i === items.length - 1;
				return item.kind === 'turn' ? (
					<TurnMarker key={item.key} turn={item.turn} isFirst={isFirst} isLast={isLast} />
				) : (
					<NodeRow key={item.key} node={item.node} isFirst={isFirst} isLast={isLast} />
				);
			})}
		</div>
	);
}
