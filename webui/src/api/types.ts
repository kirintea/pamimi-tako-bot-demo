/** 通用类型定义 */

// ========== 多模态消息 ==========

/** 文本内容块 */
export interface TextPart {
	type: 'text';
	text: string;
}

/** 图片内容块 */
export interface ImagePart {
	type: 'image';
	key: string;
}

/** 消息内容块（文本或图片） */
export type ContentPart = TextPart | ImagePart;

/** 图片附件（前端预览用） */
export interface ImageAttachment {
	/** 本地预览 URL（Object.createObjectURL） */
	previewUrl: string;
	/** 对象存储 key（上传成功后赋值） */
	key: string;
	/** 上传状态 */
	status: 'uploading' | 'done' | 'error';
	/** 文件名 */
	name: string;
	/** 错误信息 */
	error?: string;
}

// ========== 会话 ==========

export interface SessionInfo {
	session_id: string;
	user_id: string;
	title: string;
	created_at: number;
	last_active: number;
	message_count: number;
	parent_session_id?: string;
}

export interface SessionListResponse {
	sessions: SessionInfo[];
	total: number;
}

/** root 管理视图：跨用户扫描返回的所有会话 */
export interface AdminSessionsResponse {
	sessions: SessionInfo[];
	total: number;
}

/** 当前用户身份与角色（GET /me） */
export interface MeResponse {
	user_id: string;
	role: 'root' | 'normal';
	is_root: boolean;
}

export interface SessionMessage {
	id: number;
	role: 'user' | 'assistant';
	content: string;
	metadata?: MessageMetadata | null;
	created_at?: string;
	turn_id?: string | null;
	turn_seq?: number | null;
}

export interface MessageMetadata {
	thinking?: string;
	tool_calls?: ToolCallRecord[];
	// v3 有序持久化：行类型标记
	type?: 'text' | 'thinking' | 'tool_call';
	text?: string;              // type=thinking 时的思考内容
	tool_call_id?: string;      // type=tool_call 时
	tool_name?: string;
	tool_args?: unknown;
	result?: string;
	state?: string;
}

export interface ToolCallRecord {
	tool_name: string;
	tool_call_id: string;
	tool_args?: unknown;
	result?: string;
	state?: string;
}

export interface MessagesResponse {
	messages: SessionMessage[];
	has_more: boolean;
	oldest_id: number | null;
}

// ========== 对话 ==========

export interface ChatRequest {
	message: string;
	user_id?: string;
	session_id?: string;
}

export interface ChatMessage {
	id: string;
	/** 后端 PG conversations 行的真实主键（历史消息才有；供中途 fork 截断用） */
	dbId?: number;
	role: 'user' | 'assistant';
	content: string;
	thinking?: string;
	toolCalls?: ToolCallInfo[];
	/** 图片 key 列表（用户消息中的图片） */
	images?: string[];
	/** 创建时间（epoch ms）— PromptNavigator 时间戳用 */
	createdAt?: number;
}

export interface ToolCallInfo {
	tool_name: string;
	tool_call_id: string;
	tool_args?: unknown;
	result?: string;
	state?: string;
}

// ========== WebSocket ==========

export interface WsMessage {
	type: string;
	payload: Record<string, unknown>;
}

// ========== MCP ==========

export interface McpInfo {
	id: string;
	user_id?: string;
	name: string;
	display_name?: string;
	/** 后端自由字符串：stdio / http / streamable_http / sse */
	transport: string;
	command?: string | null;
	args?: string[];
	url?: string | null;
	headers?: Record<string, string>;
	description?: string;
	enabled?: boolean;
	created_at?: string;
	updated_at?: string;
}

export interface CreateMcpRequest {
	name: string;
	display_name?: string;
	/** stdio / http / streamable_http / sse */
	transport: string;
	command?: string;
	args?: string[];
	url?: string;
	headers?: Record<string, string>;
	description?: string;
}

export interface McpListResponse {
	mcps: McpInfo[];
	total: number;
}

// ========== Skill ==========

export interface SkillInfo {
	id: string;
	user_id?: string;
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
	/** 版本（设计稿卡片右上角 Ver badge） */
	version?: string | null;
	enabled?: boolean;
	created_at?: string;
	updated_at?: string;
}

export interface CreateSkillRequest {
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
	/** 文件映射：文件相对路径 → 文件内容（用于压缩包上传） */
	files?: Record<string, string>;
}

export interface UpdateSkillRequest {
	name?: string;
	enabled?: boolean;
	display_name?: string;
	description?: string;
}

export interface SkillListResponse {
	skills: SkillInfo[];
	total: number;
}

// ========== Channel ==========

export interface ChannelConfig {
	bot_id?: string;
	secret?: string;
	allow_from?: string[];
	welcome_message?: string;
}

export interface ChannelInfo {
	id: string;
	user_id?: string;
	name: string;
	type: string;
	enabled: boolean;
	/** 运行态：stopped / starting / running / failed */
	status: string;
	/** 响应中 secret 已脱敏（非空即 "********"） */
	config: ChannelConfig;
	created_at?: string;
	updated_at?: string;
}

export interface CreateChannelRequest {
	name: string;
	type?: string;
	enabled?: boolean;
	config: ChannelConfig;
}

export interface UpdateChannelRequest {
	name?: string;
	enabled?: boolean;
	config?: Partial<ChannelConfig>;
}

export interface ChannelListResponse {
	channels: ChannelInfo[];
	total: number;
}

/** 单个依赖的可用性探测结果（来自后端 /channels/manifests） */
export interface ChannelDependency {
	/** pip 包名（tooltip 展示） */
	name: string;
	/** importlib.util.find_spec 探测的模块名 */
	import_name: string;
	/** 是否已安装 */
	available: boolean;
}

/** 渠道元数据，含运行所需依赖及其可用性（前端渲染红绿指示灯） */
export interface ChannelManifest {
	type: string;
	name: string;
	display_name: string;
	description?: string;
	doc_url?: string;
	/** 后端是否已实现适配器（注册表中存在） */
	backend_supported: boolean;
	/** 创建表单字段（仅后端已接入类型提供） */
	fields?: Array<Record<string, unknown>>;
	dependencies: ChannelDependency[];
	/** 全部依赖就绪为 true；任一缺失为 false */
	dependencies_satisfied: boolean;
}

export interface ChannelRuntime {
	id: string;
	type: string;
	name: string;
	running: boolean;
	state: string;
	error?: string | null;
}

export interface ChannelStatusResponse {
	runtime: Record<string, ChannelRuntime>;
}

// ========== Health ==========

export interface HealthResponse {
	status: string;
	version?: string;
	components?: Record<string, string>;
	active_sessions?: number;
}
