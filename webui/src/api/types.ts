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

// ========== Health ==========

export interface HealthResponse {
	status: string;
	version?: string;
	components?: Record<string, string>;
	active_sessions?: number;
}
