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
}

export interface MessageMetadata {
	thinking?: string;
	tool_calls?: ToolCallRecord[];
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
	name: string;
	display_name?: string;
	transport: 'stdio' | 'sse';
	command?: string;
	args?: string[];
	url?: string;
	headers?: Record<string, string>;
	description?: string;
}

export interface CreateMcpRequest {
	name: string;
	display_name?: string;
	transport: 'stdio' | 'sse';
	command?: string;
	args?: string[];
	url?: string;
	headers?: Record<string, string>;
	description?: string;
}

// ========== Skill ==========

export interface SkillInfo {
	id: string;
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
}

export interface CreateSkillRequest {
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
}

// ========== Health ==========

export interface HealthResponse {
	status: string;
	version?: string;
	components?: Record<string, string>;
	active_sessions?: number;
}
