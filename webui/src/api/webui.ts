/**
 * WebUI 配置下发 API
 *
 * 后端 GET /webui-config 返回前端功能开关（服务端 rag.api_url 等配置的投影）。
 * - rag_url：非空（/rag/）时侧边栏「知识库 RAG」入口跳转该地址
 *   （本站反代，后端自动注入 LightRAG 登录 token）；为 null 时显示占位提示。
 * - auth_required：服务端 AUTH 开关（D9）——true 时 setup 页 API Key 必填强校验。
 */

import { client } from './client';

export interface WebuiConfig {
	/** RAG webui 反代入口（/rag/），null = 未配置 */
	rag_url: string | null;
	/** 服务端认证是否开启（is_auth_enabled 投影）→ setup 页 API Key 必填 */
	auth_required: boolean;
}

export const webuiApi = {
	getConfig: () => client.get<WebuiConfig>('/webui-config'),
};
